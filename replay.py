"""Replay: run a brain over historical candles and trade them on paper, fast.

    python replay.py history.csv                  # history.csv from export_history.py
    python replay.py history.csv --compare        # every rule strategy side by side, free
    python replay.py history.csv --brain hybrid --budget 2
    python replay.py history.csv --brain rules --strategy bollinger_breakout

Settings come from .env like the live bot (lot, stops, loss limit, brain, API key). Fills
follow fills.py, the same rules dry mode uses, and everything goes into the same journal:
what the AI brain learns here it keeps when the bot runs for real. --compare uses throwaway
journals so the AI's record is not polluted by rule-only trades.

CSV columns: time (YYYY-MM-DD HH:MM:SS, broker server time), open, high, low, close and
spread (points; optional, --spread fills the gap). Prices use the symbol's full number of
decimals, which is how the replay learns the point size.

Caveat: the model may know the period being replayed. The prompt carries no dates, but a
good replay is still weaker evidence than a good paper or demo run.
"""
import argparse
import csv
from datetime import datetime, timezone

import ai_strategy
import brain
import fills
import indicators
from config import JOURNAL_PATH, candle_seconds, load_config
from journal import Journal, summarize
from report import format_report, stamp
from risk import block_reason, day_start
from strategies import STRATEGIES

MAX_AI_FAILURES = 3  # consecutive failed AI calls before the replay gives up


def read_candles(path, default_spread=30):
    """Candles from a CSV file, oldest first, plus the number of price decimals it uses."""
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{path} has no candles")
    candles, digits = [], 0
    for row in rows:
        when = datetime.strptime(row["time"].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        spread = row.get("spread")
        candles.append({
            "time": int(when.timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "spread": int(float(spread)) if spread not in (None, "") else default_spread,
        })
        digits = max(digits, len(row["close"].strip().partition(".")[2]))
    if any(later["time"] <= earlier["time"] for earlier, later in zip(candles, candles[1:])):
        raise SystemExit("candles must be in ascending time order without duplicates")
    return candles, digits


def replay(candles, config, journal, run, digits, log=print, decide=None):
    """Trade the candles on paper. Returns why it stopped: "end", "budget" or "ai failures".

    decide(window) -> (signal, reason, cost) replaces the brain, e.g. an agent's analyst (ui.backtest)."""
    needed = brain.candles_needed(config)
    learns = brain.learns(config) and decide is None
    point = 10 ** -digits
    position = None
    failures = 0
    stopped = "end"
    for index in range(needed - 1, len(candles) - 1):
        candle = candles[index]  # has just closed; a new trade fills at candles[index + 1]'s open
        if position:
            hit = fills.exit_price(position, candle["high"], candle["low"], candle["spread"], point)
            if hit:
                position = settle(journal, run, config, position, candles, index, *hit, log=log)
            continue
        start = day_start(candle["time"])
        pnl_today = journal.profit_since(start, "replay", run)
        traded = journal.trades_opened_since(start, "replay", run)
        blocked = block_reason(False, 0, pnl_today, candle["spread"], config, journal.spend(run=run), traded)
        if blocked and blocked.startswith("AI budget"):
            log(f"{stamp(candle['time'])} stopped: {blocked}")
            stopped = "budget"
            break
        if blocked:
            continue
        window = candles[index + 1 - needed:index + 1]
        experience = journal.experience_text() if learns else ""
        signal, reason, cost = decide(window) if decide else brain.decide(window, config, experience)
        if learns:
            failures = failures + 1 if reason.startswith("AI error") else 0
        journal.record_decision("replay", run, "decide", candle["time"], signal or "hold", reason, cost)
        if failures >= MAX_AI_FAILURES:
            log(f"{stamp(candle['time'])} stopped: {failures} AI calls in a row failed, last: {reason}")
            stopped = "ai failures"
            break
        if not signal:
            continue
        opening = candles[index + 1]
        atr_values = indicators.atr(window, config["ATR_PERIOD"])
        position = fills.open_position(
            signal, opening["open"], opening["spread"], point, digits, config,
            opened_at=opening["time"], reason=reason, atr_value=atr_values[-1] if atr_values else None,
        )
        position["index"] = index + 1
        position["trade_id"] = journal.open_trade(
            "replay", run, config["SYMBOL"], position, snapshot=ai_strategy.snapshot(window, config) if decide is None else None,  # the app's quick tests skip it
            brain=config["BRAIN"],
        )
        log(f"{stamp(opening['time'])} {signal} @ {position['entry']} sl {position['sl']} tp {position['tp']} [{reason}]")
    if position:  # history ran out: book it at the last close so nothing dangles in the journal
        last = candles[-1]
        settle(journal, run, config, position, candles, len(candles) - 1, last["close"], "closed", log=log)
    return stopped


def settle(journal, run, config, position, candles, index, price, outcome, log):
    """Close a replay position at price, let the AI brain learn from it. Returns None."""
    closed_at = candles[index]["time"] + candle_seconds(config)
    profit = fills.profit(position, price, config["CONTRACT_SIZE"])
    trade = journal.close_trade(position["trade_id"], price, closed_at, profit, outcome)
    log(f"{stamp(closed_at)} closed {trade['side']} @ {price} by {outcome}, profit {profit:+.2f}")
    if brain.learns(config):
        path = [c["close"] for c in candles[position["index"]:index + 1]]
        lesson, playbook = ai_strategy.review(journal, trade, path, config, "replay", run, journal.spend(run=run))
        if lesson:
            log("  lesson: " + lesson)
        if playbook:
            log("  playbook rewritten:\n" + playbook)
    return None


def compare(candles, config, digits):
    """Run every rule strategy, and all of them together, on throwaway journals. [(name, totals)]."""
    rows = []
    for name in list(STRATEGIES) + ["all"]:
        journal = Journal()
        run = f"compare-{name}"
        replay(candles, dict(config, BRAIN="rules", STRATEGY=name), journal, run, digits, log=lambda line: None)
        rows.append((name, summarize(journal.closed_trades(run=run))))
        journal.close()
    return rows


def format_comparison(rows, config):
    stops = (
        f"stops: {config['SL_ATR']} / {config['TP_ATR']} ATR({config['ATR_PERIOD']})"
        if config["SL_ATR"] > 0 and config["TP_ATR"] > 0
        else f"stops: {config['SL_POINTS']} / {config['TP_POINTS']} points"
    )
    header = f"{'strategy':20} {'trades':>6} {'win%':>5} {'net':>10} {'PF':>6} {'maxDD':>8} {'streak':>6}  outcomes"
    lines = [f"Rule strategies on the same candles, {stops}", header, "-" * len(header)]
    for name, totals in sorted(rows, key=lambda row: row[1]["net"], reverse=True):
        factor = "n/a" if totals["profit_factor"] is None else f"{totals['profit_factor']:.2f}"
        outcomes = ", ".join(f"{key} {count}" for key, count in sorted(totals["outcomes"].items())) or "-"
        lines.append(
            f"{name:20} {totals['trades']:>6} {totals['win_rate']:>5.0%} {totals['net']:>+10.2f} {factor:>6}"
            f" {totals['max_drawdown']:>8.2f} {totals['longest_losing_streak']:>6}  {outcomes}"
        )
    lines.append("Nothing here is proof: a few dozen trades can look good by luck. Prefer many trades and a small drawdown.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", help="candles exported by export_history.py")
    parser.add_argument("--compare", action="store_true", help="run every rule strategy and print a comparison, no AI")
    parser.add_argument("--brain", choices=("rules", "ai", "hybrid"), help="override BRAIN from .env")
    parser.add_argument("--strategy", help="override STRATEGY from .env (a name, a comma list, or all)")
    parser.add_argument("--budget", type=float, help="USD the AI may spend in this run (default AI_BUDGET_USD from .env)")
    parser.add_argument("--spread", type=int, default=30, help="spread in points when the CSV has none (default 30)")
    parser.add_argument("--digits", type=int, help="price decimals of the symbol (default: as many as the CSV uses)")
    parser.add_argument("--journal", default=JOURNAL_PATH, help="journal file (default: journal.db next to the code)")
    args = parser.parse_args()
    config = load_config()
    if args.brain:
        config["BRAIN"] = args.brain
    if args.strategy:
        config["STRATEGY"] = args.strategy
    if args.budget is not None:
        config["AI_BUDGET_USD"] = args.budget
    candles, digits = read_candles(args.csv, args.spread)
    digits = args.digits if args.digits is not None else digits
    needed = brain.candles_needed(config)
    if len(candles) <= needed:
        raise SystemExit(f"need more than {needed} candles, the file has {len(candles)}")
    print(
        f"{len(candles)} candles of {config['SYMBOL']} {config['TIMEFRAME']} from {stamp(candles[0]['time'])}"
        f" to {stamp(candles[-1]['time'])}, {digits} decimals"
    )
    if args.compare:
        print(format_comparison(compare(candles, config, digits), config))
        return
    run = f"replay-{datetime.now():%Y%m%d-%H%M%S}"
    print(f"{run}: brain={config['BRAIN']} strategy={config['STRATEGY']} budget ${config['AI_BUDGET_USD']:.2f}")
    journal = Journal(args.journal)
    try:
        stopped = replay(candles, config, journal, run, digits)
        print(f"\nfinished ({stopped})\n")
        print(format_report(journal, run=run))
    finally:
        journal.close()


if __name__ == "__main__":
    main()
