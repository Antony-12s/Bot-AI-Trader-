"""Auto-trading bot controlled from Telegram (and dashboard.py).

Run with the broker's terminal open and logged in:  python bot.py
Settings live in .env (copy .env.example). BROKER picks the platform (brokers.py), the
rules live in strategies.py, the AI brain in ai_strategy.py, brain.py picks between them
(BRAIN=rules | ai | hybrid), and every decision and trade goes into journal.db (journal.py).

Modes: dry trades on paper (virtual positions filled from the live candles, nothing is sent
to the broker), demo and live send real orders. All three write the same journal, so the
AI brain learns in dry mode and keeps that experience when it moves on.

Unattended running: run_forever.bat restarts the bot after a crash, install_autostart.bat
starts it at logon, /stop from Telegram (or stop.flag) ends it for good until the next start.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import ai_strategy
import brain
import brokers
import fills
import indicators
from config import JOURNAL_PATH, candle_seconds, load_config
from journal import Journal, summarize
from risk import account_error, block_reason, day_start, stop_distances, stop_levels

POLL_SECONDS = 5
STALE_CANDLES = 2  # a candle that closed this many candle lengths ago is old news (market was shut)
STOP_FLAG = Path(__file__).with_name("stop.flag")  # /stop leaves this so run_forever.bat does not restart the bot
PAUSE_FLAG = Path(__file__).with_name("pause.flag")  # dashboard.py creates this to pause, removes it to resume


def plan_order(side, tick, symbol, config, atr_value=None, spread_points=0):
    """Price, lot and stops for a market order.

    Stops are ATR multiples when configured, never closer than the broker's minimum stop
    distance or two spreads.
    """
    price = tick.ask if side == "buy" else tick.bid
    floor = max(int(symbol.stops_level or 0), 2 * spread_points)
    stop_points, target_points = stop_distances(config, symbol.point, atr_value, floor)
    stop_loss, take_profit = stop_levels(side, price, symbol.point, symbol.digits, stop_points, target_points)
    return {"side": side, "lot": config["LOT"], "price": price, "sl": stop_loss, "tp": take_profit}


# --- telegram -------------------------------------------------------------

def telegram(method, config, **params):
    url = f"https://api.telegram.org/bot{config['TELEGRAM_TOKEN']}/{method}"
    body = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, body, timeout=15) as response:
        return json.load(response)["result"]


def notify(config, text):
    print(datetime.now().strftime("%H:%M:%S"), text)
    if not config["TELEGRAM_TOKEN"]:
        return
    try:
        telegram("sendMessage", config, chat_id=config["TELEGRAM_CHAT_ID"], text=text)
    except Exception as error:  # Telegram is a side channel, it must never stop trading logic
        print("telegram send failed:", error)


def handle_command(text, config, state):
    command = text.strip().split("@")[0].lower()
    journal = state.get("journal")
    if command == "/pause":
        state["paused"] = state["telegram_paused"] = True
        return "paused: no new trades (open positions keep their SL/TP)"
    if command == "/resume":
        state["paused"] = state["telegram_paused"] = False
        return "resumed" if not PAUSE_FLAG.exists() else "resumed on Telegram, but the dashboard still holds the pause"
    if command == "/stop":
        state["stopping"] = True
        return "stopping: the bot exits now and the watchdog will not restart it (open positions keep their SL/TP)"
    if command == "/status":
        return status_text(config, state)
    if command == "/playbook" and journal:
        playbook = journal.playbook()
        return playbook["text"] if playbook else "no playbook yet: it is written after %d closed trades" % ai_strategy.DISTILL_EVERY
    if command == "/lessons" and journal:
        lessons = journal.recent_lessons(3)
        if not lessons:
            return "no lessons yet"
        return "\n".join(f"({t['side']} {t['outcome']} {t['profit']:+.2f}) {t['lesson']}" for t in lessons)
    return None


def apply_flags(state):
    """Let the dashboard's files pause or stop the bot alongside Telegram. Returns True to stop."""
    state["paused"] = bool(state.get("telegram_paused")) or PAUSE_FLAG.exists()
    return bool(state.get("stopping")) or STOP_FLAG.exists()


def read_commands(config, state, skip_only=False):
    """Run Telegram commands from the configured chat, ignore everyone else.

    skip_only drops the backlog at startup so a stale /resume cannot act.
    """
    if not config["TELEGRAM_TOKEN"]:
        return
    try:
        updates = telegram("getUpdates", config, offset=state["update_offset"], timeout=0)
    except Exception as error:
        print("telegram poll failed:", error)
        return
    for update in updates:
        state["update_offset"] = update["update_id"] + 1
        message = update.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id"))
        if skip_only:
            continue
        if chat_id != config["TELEGRAM_CHAT_ID"]:
            print("ignored message from chat", chat_id)
            continue
        reply = handle_command(message.get("text", ""), config, state)
        if reply:
            notify(config, reply)


# --- broker ---------------------------------------------------------------------

def ensure_connected(config, state):
    """True when the broker answers. Otherwise tell the owner once and keep trying to reconnect."""
    broker = state["broker"]
    if broker.alive():
        if state.get("disconnected"):
            state["disconnected"] = False
            notify(config, f"{broker.name} connection is back")
        return True
    if not state.get("disconnected"):
        state["disconnected"] = True
        notify(config, f"{broker.name} connection lost (terminal closed?), retrying every few seconds")
    broker.connect()
    return False


def pnl_today(broker, server_now):
    """Realized result of every trade on the account since server midnight.

    Manual trades count too, on purpose: the limit protects the account.
    Floating loss is not counted, it is capped by the single position's SL.
    """
    return broker.realized_since(day_start(server_now))


def status_text(config, state):
    broker = state["broker"]
    tick = broker.tick(config["SYMBOL"])
    pnl = f"{pnl_today(broker, tick.time):.2f}" if tick else "unknown"
    text = (
        f"mode={config['MODE']} brain={config['BRAIN']} paused={state['paused']} "
        f"symbol={config['SYMBOL']} open_positions={len(broker.open_positions(config['SYMBOL'], config['MAGIC']))}"
        f" pnl_today={pnl} last_decision={state.get('last_decision', 'none yet')}"
    )
    journal = state.get("journal")
    if journal:
        totals = summarize(journal.closed_trades())
        spent = journal.spend(source="bot", since=day_start(tick.time)) if tick else 0.0
        text += (
            f" paper_open={len(journal.open_trades('paper'))} trades={totals['trades']} wins={totals['wins']}"
            f" net={totals['net']:+.2f} ai_spent_today=${spent:.2f}"
        )
    return text


def place_order(side, tick, symbol, config, state, reason="", atr_value=None, spread_points=0):
    """Send (or, in dry mode, only announce) a market order.

    Returns the order plan when the position exists, with position_id set for real orders,
    or None when the broker rejected it.
    """
    broker = state["broker"]
    plan = plan_order(side, tick, symbol, config, atr_value, spread_points)
    summary = f"{side} {plan['lot']} {config['SYMBOL']} @ {plan['price']} sl {plan['sl']} tp {plan['tp']}"
    if reason:
        summary += f" [{reason}]"
    if config["MODE"] == "dry":
        notify(config, "[paper] " + summary)
        return plan
    # Re-checked on every order: the terminal can be switched to another account mid-run.
    account = broker.account()
    error = account_error(config["MODE"], account is not None and account.is_demo)
    if error:
        notify(config, error)
        raise SystemExit(error)
    result = broker.market_order(
        side, config["SYMBOL"], plan["lot"], plan["price"], plan["sl"], plan["tp"], config["MAGIC"], config["FILLING"]
    )
    if not result.ok:
        notify(config, f"order FAILED: {summary} ({result.detail})")  # no retry, next signal decides
        return None
    plan["position_id"] = result.position_id
    notify(config, "opened " + summary)
    return plan


def remember(state, decision):
    """Keep the latest decision for /status and the console log."""
    state["last_decision"] = decision
    print(datetime.now().strftime("%H:%M:%S"), decision)


# --- learning from closed trades --------------------------------------------

def price_path(config, state, trade):
    """Closes from around the entry to now, for the brain's review of a closed trade."""
    held = round((trade["closed_at"] - trade["opened_at"]) / candle_seconds(config)) + 1
    count = max(2, min(held, ai_strategy.PATH_SHOWN))
    candles = state["broker"].candles(config["SYMBOL"], config["TIMEFRAME"], count)
    return [candle["close"] for candle in candles] if candles else []


def finish_trade(config, state, trade_id, price, closed_at, profit, outcome):
    """Record a closed trade, let the AI brain learn from it, tell the owner."""
    journal = state["journal"]
    trade = journal.close_trade(trade_id, price, closed_at, profit, outcome)
    text = f"closed {trade['side']} {trade['lot']} {trade['symbol']} @ {price} by {outcome}, profit {profit:+.2f}"
    if brain.learns(config):
        spent = journal.spend(source="bot", since=day_start(closed_at))
        lesson, playbook = ai_strategy.review(
            journal, trade, price_path(config, state, trade), config, "bot", state["run"], spent
        )
        if lesson:
            text += "\nlesson: " + lesson
        if playbook:
            text += "\nplaybook rewritten:\n" + playbook
    notify(config, text)


def settle_paper(config, state, candle, symbol):
    """Close dry-mode paper positions that this closed candle's range took out."""
    journal = state["journal"]
    for trade in journal.open_trades("paper"):
        hit = fills.exit_price(trade, float(candle["high"]), float(candle["low"]), int(candle["spread"]), symbol.point)
        if hit:
            price, outcome = hit
            closed_at = int(candle["time"]) + candle_seconds(config)
            finish_trade(config, state, trade["id"], price, closed_at, fills.profit(trade, price, config["CONTRACT_SIZE"]), outcome)


def settle_broker(config, state):
    """Close journal entries of broker positions that are gone, from the broker's own records."""
    journal, broker = state.get("journal"), state["broker"]
    if not journal:
        return
    for trade in journal.open_trades(broker.name):
        result = broker.position_result(trade["position_id"])
        if result:
            finish_trade(config, state, trade["id"], result["exit"], result["closed_at"], result["profit"], result["outcome"])


# --- the loop -----------------------------------------------------------------

def check_market(config, state):
    """Ask the brain once per newly closed candle."""
    broker, journal = state["broker"], state.get("journal")
    paper = journal is not None and config["MODE"] == "dry"
    candles = broker.candles(config["SYMBOL"], config["TIMEFRAME"], brain.candles_needed(config) + 1)
    if not candles or len(candles) < 2:
        return
    closed_candles = candles[:-1]  # the last one is still forming
    candle = closed_candles[-1]
    candle_time = candle["time"]
    if candle_time == state["last_candle_time"]:
        return
    first_look = state["last_candle_time"] is None
    state["last_candle_time"] = candle_time
    if first_look:
        return  # never trade a candle that closed before the bot started
    tick = broker.tick(config["SYMBOL"])
    symbol = broker.symbol(config["SYMBOL"])
    if tick is None or symbol is None:
        return remember(state, "skipped: no price from the broker")
    if paper:
        settle_paper(config, state, candle, symbol)
    age = tick.time - candle_time
    if age > STALE_CANDLES * candle_seconds(config):
        return remember(state, f"skipped: candle closed {age // 60} min ago, the market was shut")
    spread_points = round((tick.ask - tick.bid) / symbol.point)
    start = day_start(tick.time)
    open_count = len(broker.open_positions(config["SYMBOL"], config["MAGIC"])) + (len(journal.open_trades("paper")) if paper else 0)
    pnl = pnl_today(broker, tick.time) + (journal.profit_since(start, "paper") if paper else 0.0)
    spent = journal.spend(source="bot", since=start) if journal else 0.0
    blocked = block_reason(state["paused"], open_count, pnl, spread_points, config, spent)
    if blocked:
        return remember(state, "skipped: " + blocked)  # checked first: a blocked candle costs no AI call
    experience = journal.experience_text() if journal and brain.learns(config) else ""
    signal, reason, cost = brain.decide(closed_candles, config, experience)
    if journal:
        journal.record_decision("bot", state["run"], "decide", candle_time, signal or "hold", reason, cost)
    remember(state, f"{signal or 'hold'}: {reason}")
    if not signal:
        return
    atr_values = indicators.atr(closed_candles, config["ATR_PERIOD"])
    # the AI call can take a while, so price the order from a fresh tick
    fresh_tick = broker.tick(config["SYMBOL"]) or tick
    opened = place_order(
        signal, fresh_tick, symbol, config, state, reason, atr_values[-1] if atr_values else None, spread_points
    )
    if opened and journal:
        position = {
            "side": signal, "lot": opened["lot"], "entry": opened["price"], "sl": opened["sl"],
            "tp": opened["tp"], "opened_at": fresh_tick.time, "reason": reason,
        }
        journal.open_trade(
            "paper" if paper else broker.name, state["run"], config["SYMBOL"], position,
            position_id=opened.get("position_id"), snapshot=ai_strategy.snapshot(closed_candles, config),
            brain=config["BRAIN"],
        )


def main():
    config = load_config()
    broker = brokers.load(config)
    if not broker.connect():
        raise SystemExit(broker.connection_hint())
    journal = Journal(JOURNAL_PATH)
    try:
        if not broker.select_symbol(config["SYMBOL"]):
            raise SystemExit(f"symbol {config['SYMBOL']} not found, broker naming differs: check SYMBOL in .env")
        account = broker.account()
        error = account_error(config["MODE"], account is not None and account.is_demo)
        if error:
            raise SystemExit(error)
        state = {
            "paused": PAUSE_FLAG.exists(),
            "telegram_paused": False,
            "last_candle_time": None,
            "update_offset": 0,
            "broker": broker,
            "journal": journal,
            "run": f"{config['MODE']}-{datetime.now():%Y%m%d-%H%M%S}",
        }
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
        read_commands(config, state, skip_only=True)
        notify(config, "bot started: " + status_text(config, state))
        while True:
            read_commands(config, state)
            if apply_flags(state):
                break
            if ensure_connected(config, state):
                if config["MODE"] != "dry":
                    settle_broker(config, state)
                check_market(config, state)
            time.sleep(POLL_SECONDS)
        STOP_FLAG.touch()
        notify(config, "bot stopped by /stop or the dashboard")
    except KeyboardInterrupt:
        notify(config, "bot stopped")
    except Exception as error:
        notify(config, f"bot crashed: {error!r}")
        raise
    finally:
        journal.close()
        broker.shutdown()


if __name__ == "__main__":
    main()
