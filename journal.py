"""Trade journal: every decision, every trade and what came of it, in one SQLite file.

The AI brain reads its own record back from here (statistics, the playbook it distilled,
lessons from recent trades), so experience gathered in replay carries over to paper and
live trading. Rows are plain dicts; times are broker server time unless named otherwise.
"""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,         -- bot | replay
    run TEXT NOT NULL,
    kind TEXT NOT NULL,           -- decide | reflect | distill
    at INTEGER NOT NULL,          -- server time of the candle that triggered it
    action TEXT,                  -- buy | sell | hold (decide only)
    reason TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    recorded_at INTEGER NOT NULL  -- wall clock
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,         -- paper | mt5 | replay
    run TEXT NOT NULL,
    position_id INTEGER,          -- MT5 position ticket, NULL for paper trades
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    lot REAL NOT NULL,
    entry REAL NOT NULL,
    sl REAL NOT NULL,
    tp REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    reason TEXT,
    snapshot TEXT,                -- what the brain saw when it entered
    exit REAL,
    closed_at INTEGER,
    profit REAL,
    outcome TEXT,                 -- sl | tp | closed (by hand or by the broker)
    lesson TEXT,
    brain TEXT                    -- rules | ai | hybrid: who decided
);
CREATE TABLE IF NOT EXISTS playbook (
    id INTEGER PRIMARY KEY,
    created_at INTEGER NOT NULL,
    trades_seen INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""


LEARNING_BRAINS = ("ai", "hybrid")  # trades the AI decided: the only ones that shape its experience


class Journal:
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(trades)")}
        if "brain" not in columns:  # journals written before the column existed
            with self.connection:
                self.connection.execute("ALTER TABLE trades ADD COLUMN brain TEXT")

    def close(self):
        self.connection.close()

    def _rows(self, sql, params=()):
        return [dict(row) for row in self.connection.execute(sql, params)]

    # --- decisions and API spend ------------------------------------------

    def record_decision(self, source, run, kind, at, action=None, reason=None, cost_usd=0.0):
        with self.connection:
            self.connection.execute(
                "INSERT INTO decisions (source, run, kind, at, action, reason, cost_usd, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (source, run, kind, int(at), action, reason, float(cost_usd), int(time.time())),
            )

    def spend(self, source=None, run=None, since=None):
        """Total API cost in USD of the matching decisions."""
        sql, params = "SELECT COALESCE(SUM(cost_usd), 0) FROM decisions WHERE 1=1", []
        if source is not None:
            sql, params = sql + " AND source = ?", params + [source]
        if run is not None:
            sql, params = sql + " AND run = ?", params + [run]
        if since is not None:
            sql, params = sql + " AND at >= ?", params + [int(since)]
        return float(self.connection.execute(sql, params).fetchone()[0])

    def decision_counts(self, source=None, run=None):
        sql, params = "SELECT action, COUNT(*) FROM decisions WHERE kind = 'decide'", []
        if source is not None:
            sql, params = sql + " AND source = ?", params + [source]
        if run is not None:
            sql, params = sql + " AND run = ?", params + [run]
        return dict(self.connection.execute(sql + " GROUP BY action", params).fetchall())

    # --- trades ----------------------------------------------------------------

    def open_trade(self, source, run, symbol, position, position_id=None, snapshot=None, brain=None):
        """Store a freshly opened position (a dict from fills.open_position). Returns its id."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO trades (source, run, position_id, symbol, side, lot, entry, sl, tp,"
                " opened_at, reason, snapshot, brain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source, run, position_id, symbol, position["side"], position["lot"],
                    position["entry"], position["sl"], position["tp"], int(position["opened_at"]),
                    position.get("reason"), snapshot, brain,
                ),
            )
        return cursor.lastrowid

    def trade(self, trade_id):
        rows = self._rows("SELECT * FROM trades WHERE id = ?", (trade_id,))
        return rows[0] if rows else None

    def open_trades(self, source=None):
        sql, params = "SELECT * FROM trades WHERE closed_at IS NULL", []
        if source is not None:
            sql, params = sql + " AND source = ?", [source]
        return self._rows(sql + " ORDER BY id", params)

    def closed_trades(self, source=None, run=None):
        sql, params = "SELECT * FROM trades WHERE closed_at IS NOT NULL", []
        if source is not None:
            sql, params = sql + " AND source = ?", params + [source]
        if run is not None:
            sql, params = sql + " AND run = ?", params + [run]
        return self._rows(sql + " ORDER BY closed_at, id", params)

    def learned_trades(self):
        """Closed trades the AI decided (any source): its track record."""
        marks = ", ".join("?" * len(LEARNING_BRAINS))
        return self._rows(
            f"SELECT * FROM trades WHERE closed_at IS NOT NULL AND brain IN ({marks}) ORDER BY closed_at, id",
            LEARNING_BRAINS,
        )

    def close_trade(self, trade_id, exit, closed_at, profit, outcome):
        with self.connection:
            self.connection.execute(
                "UPDATE trades SET exit = ?, closed_at = ?, profit = ?, outcome = ? WHERE id = ?",
                (exit, int(closed_at), round(profit, 2), outcome, trade_id),
            )
        return self.trade(trade_id)

    def add_lesson(self, trade_id, lesson):
        with self.connection:
            self.connection.execute("UPDATE trades SET lesson = ? WHERE id = ?", (lesson, trade_id))

    def profit_since(self, since, source=None, run=None):
        """Realized result of trades closed at or after `since` (server time)."""
        sql, params = "SELECT COALESCE(SUM(profit), 0) FROM trades WHERE closed_at >= ?", [int(since)]
        if source is not None:
            sql, params = sql + " AND source = ?", params + [source]
        if run is not None:
            sql, params = sql + " AND run = ?", params + [run]
        return float(self.connection.execute(sql, params).fetchone()[0])

    def recent_lessons(self, limit=5):
        return self._rows(
            "SELECT * FROM trades WHERE lesson IS NOT NULL ORDER BY closed_at DESC, id DESC LIMIT ?",
            (limit,),
        )

    # --- playbook ---------------------------------------------------------

    def playbook(self):
        rows = self._rows("SELECT * FROM playbook ORDER BY id DESC LIMIT 1")
        return rows[0] if rows else None

    def save_playbook(self, text, trades_seen):
        with self.connection:
            self.connection.execute(
                "INSERT INTO playbook (created_at, trades_seen, text) VALUES (?, ?, ?)",
                (int(time.time()), trades_seen, text),
            )

    # --- what the brain gets to read -------------------------------------------

    def experience_text(self, lessons=5):
        """The AI brain's own track record (trades it decided), formatted for its prompt."""
        trades = self.learned_trades()
        if not trades:
            return "Your track record: no closed trades yet. Trade cautiously and build one."
        totals = summarize(trades)
        lines = [
            f"Your track record: {totals['trades']} closed trades, {totals['wins']} wins /"
            f" {totals['losses']} losses ({totals['win_rate']:.0%} win rate), net {totals['net']:+.2f},"
            f" profit factor {_number(totals['profit_factor'])}.",
            f"Average win {totals['avg_win']:+.2f}, average loss {totals['avg_loss']:+.2f},"
            f" longest losing streak {totals['longest_losing_streak']}.",
        ]
        for side in ("buy", "sell"):
            if side in totals["by_side"]:
                part = totals["by_side"][side]
                lines.append(f"{side.capitalize()}s: {part['trades']} trades, {part['win_rate']:.0%} wins, net {part['net']:+.2f}.")
        playbook = self.playbook()
        if playbook:
            lines += [f"Your playbook (rules you distilled after {playbook['trades_seen']} trades):", playbook["text"]]
        recent = self.recent_lessons(lessons)
        if recent:
            lines.append(f"Lessons from your last {len(recent)} trades, newest first:")
            lines += [f"- ({t['side']} {t['outcome']} {t['profit']:+.2f}) {t['lesson']}" for t in recent]
        return "\n".join(lines)


def summarize(trades):
    """Performance figures for a list of closed trade dicts (any order)."""
    trades = sorted(trades, key=lambda trade: (trade["closed_at"], trade["id"]))
    profits = [trade["profit"] for trade in trades]
    wins = [profit for profit in profits if profit > 0]
    losses = [profit for profit in profits if profit < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    peak = running = drawdown = 0.0
    streak = longest_streak = 0
    for profit in profits:
        running += profit
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
        streak = streak + 1 if profit < 0 else 0
        longest_streak = max(longest_streak, streak)
    by_side = {}
    for side in ("buy", "sell"):
        side_profits = [trade["profit"] for trade in trades if trade["side"] == side]
        if side_profits:
            side_wins = sum(1 for profit in side_profits if profit > 0)
            by_side[side] = {
                "trades": len(side_profits),
                "wins": side_wins,
                "win_rate": side_wins / len(side_profits),
                "net": round(sum(side_profits), 2),
            }
    outcomes = {}
    for trade in trades:
        outcomes[trade["outcome"]] = outcomes.get(trade["outcome"], 0) + 1
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "net": round(sum(profits), 2),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "max_drawdown": round(drawdown, 2),
        "longest_losing_streak": longest_streak,
        "by_side": by_side,
        "outcomes": outcomes,
    }


def _number(value):
    return "n/a (no losing trades)" if value is None else f"{value:.2f}"
