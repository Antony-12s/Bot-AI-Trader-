"""MT5 auto-trading bot controlled from Telegram.

Run with the MT5 terminal open and logged in:  python bot.py
Settings live in .env (copy .env.example). The rules live in strategies.py, the AI brain in
ai_strategy.py, brain.py picks between them (BRAIN=rules | ai | hybrid), and every decision
and trade goes into journal.db (journal.py).

Modes: dry trades on paper (virtual positions filled from the live candles, nothing is sent
to the broker), demo and live send real orders. All three write the same journal, so the
AI brain learns in dry mode and keeps that experience when it moves on.

Unattended running: run_forever.bat restarts the bot after a crash, install_autostart.bat
starts it at logon, /stop from Telegram ends it for good (until the next start).
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5

import ai_strategy
import brain
import fills
import indicators
from config import ENV_PATH, JOURNAL_PATH, candle_seconds
from config import load_config as load_settings
from journal import Journal, summarize
from risk import account_error, block_reason, day_start, stop_distances, stop_levels

POLL_SECONDS = 5
STALE_CANDLES = 2  # a candle that closed this many candle lengths ago is old news (market was shut)
STOP_FLAG = Path(__file__).with_name("stop.flag")  # /stop leaves this so run_forever.bat does not restart the bot
PAUSE_FLAG = Path(__file__).with_name("pause.flag")  # dashboard.py creates this to pause, removes it to resume


def load_config(env_path=ENV_PATH):
    config = load_settings(env_path)
    for constant in ("TIMEFRAME_" + config["TIMEFRAME"], "ORDER_FILLING_" + config["FILLING"]):
        if not hasattr(mt5, constant):
            raise SystemExit(f"this MetaTrader5 package has no {constant}, check TIMEFRAME / FILLING in .env")
    return config


def build_order(side, tick, symbol_info, config, atr_value=None, spread_points=0):
    """Market order request for mt5.order_send, always with stop loss and take profit.

    Stops are ATR multiples when configured, never closer than the broker's minimum stop
    distance or two spreads.
    """
    is_buy = side == "buy"
    price = tick.ask if is_buy else tick.bid
    floor = max(int(getattr(symbol_info, "trade_stops_level", 0) or 0), 2 * spread_points)
    stop_points, target_points = stop_distances(config, symbol_info.point, atr_value, floor)
    stop_loss, take_profit = stop_levels(side, price, symbol_info.point, symbol_info.digits, stop_points, target_points)
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config["SYMBOL"],
        "volume": config["LOT"],
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": 20,
        "magic": config["MAGIC"],
        "comment": "tradebot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": getattr(mt5, "ORDER_FILLING_" + config["FILLING"]),
    }


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


# --- MT5 ------------------------------------------------------------------

def ensure_connected(config, state):
    """True when the terminal answers. Otherwise tell the owner once and keep trying to reconnect."""
    if mt5.terminal_info() is not None:
        if state.get("disconnected"):
            state["disconnected"] = False
            notify(config, "MT5 connection is back")
        return True
    if not state.get("disconnected"):
        state["disconnected"] = True
        notify(config, "MT5 connection lost (terminal closed?), retrying every few seconds")
    mt5.initialize()
    return False


def open_positions(config):
    positions = mt5.positions_get(symbol=config["SYMBOL"]) or ()
    return [position for position in positions if position.magic == config["MAGIC"]]


def pnl_today(server_now):
    """Realized result of every trade on the account since server midnight.

    Manual trades count too, on purpose: the limit protects the account.
    Floating loss is not counted, it is capped by the single position's SL.
    """
    start = day_start(server_now)
    # Query a wide window and filter on deal.time ourselves: deal and tick times
    # share the broker's server clock, the PC clock does not.
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(days=3), now + timedelta(days=3)) or ()
    return sum(
        deal.profit + deal.commission + deal.swap
        for deal in deals
        if deal.time >= start and deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
    )


def status_text(config, state):
    tick = mt5.symbol_info_tick(config["SYMBOL"])
    pnl = f"{pnl_today(tick.time):.2f}" if tick else "unknown"
    text = (
        f"mode={config['MODE']} brain={config['BRAIN']} paused={state['paused']} "
        f"symbol={config['SYMBOL']} open_positions={len(open_positions(config))} pnl_today={pnl} "
        f"last_decision={state.get('last_decision', 'none yet')}"
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


def place_order(side, tick, symbol_info, config, reason="", atr_value=None, spread_points=0):
    """Send (or, in dry mode, only announce) a market order.

    Returns the request dict when the position exists, with position_id set for real
    orders, or None when the order was rejected.
    """
    request = build_order(side, tick, symbol_info, config, atr_value, spread_points)
    summary = (
        f"{side} {request['volume']} {request['symbol']} @ {request['price']} "
        f"sl {request['sl']} tp {request['tp']}"
    )
    if reason:
        summary += f" [{reason}]"
    if config["MODE"] == "dry":
        notify(config, "[paper] " + summary)
        return request
    # Re-checked on every order: the terminal can be switched to another account mid-run.
    account = mt5.account_info()
    is_demo = account is not None and account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    error = account_error(config["MODE"], is_demo)
    if error:
        notify(config, error)
        raise SystemExit(error)
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        detail = f"retcode={result.retcode} {result.comment}" if result else mt5.last_error()
        notify(config, f"order FAILED: {summary} ({detail})")  # no retry, next signal decides
        return None
    # The position keeps the ticket of the deal's position_id; fall back to the order ticket.
    deals = mt5.history_deals_get(ticket=result.deal) or ()
    request["position_id"] = deals[0].position_id if deals else result.order
    notify(config, "opened " + summary)
    return request


def remember(state, decision):
    """Keep the latest decision for /status and the console log."""
    state["last_decision"] = decision
    print(datetime.now().strftime("%H:%M:%S"), decision)


# --- learning from closed trades --------------------------------------------

def price_path(config, trade):
    """Closes from around the entry to now, for the brain's review of a closed trade."""
    held = round((trade["closed_at"] - trade["opened_at"]) / candle_seconds(config)) + 1
    count = max(2, min(held, ai_strategy.PATH_SHOWN))
    timeframe = getattr(mt5, "TIMEFRAME_" + config["TIMEFRAME"])
    rates = mt5.copy_rates_from_pos(config["SYMBOL"], timeframe, 0, count)
    return [float(candle["close"]) for candle in rates] if rates is not None else []


def finish_trade(config, state, trade_id, price, closed_at, profit, outcome):
    """Record a closed trade, let the AI brain learn from it, tell the owner."""
    journal = state["journal"]
    trade = journal.close_trade(trade_id, price, closed_at, profit, outcome)
    text = f"closed {trade['side']} {trade['lot']} {trade['symbol']} @ {price} by {outcome}, profit {profit:+.2f}"
    if brain.learns(config):
        spent = journal.spend(source="bot", since=day_start(closed_at))
        lesson, playbook = ai_strategy.review(
            journal, trade, price_path(config, trade), config, "bot", state["run"], spent
        )
        if lesson:
            text += "\nlesson: " + lesson
        if playbook:
            text += "\nplaybook rewritten:\n" + playbook
    notify(config, text)


def settle_paper(config, state, candle, symbol_info):
    """Close dry-mode paper positions that this closed candle's range took out."""
    journal = state["journal"]
    for trade in journal.open_trades("paper"):
        hit = fills.exit_price(trade, float(candle["high"]), float(candle["low"]), int(candle["spread"]), symbol_info.point)
        if hit:
            price, outcome = hit
            closed_at = int(candle["time"]) + candle_seconds(config)
            finish_trade(config, state, trade["id"], price, closed_at, fills.profit(trade, price, config["CONTRACT_SIZE"]), outcome)


def settle_mt5(config, state):
    """Close journal entries of MT5 positions that are gone, from the broker's own deals."""
    journal = state.get("journal")
    if not journal:
        return
    for trade in journal.open_trades("mt5"):
        if mt5.positions_get(ticket=trade["position_id"]):
            continue
        deals = mt5.history_deals_get(position=trade["position_id"]) or ()
        exits = [deal for deal in deals if deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            continue  # the terminal has not shown the closing deal yet, look again next poll
        last = max(exits, key=lambda deal: deal.time)
        outcome = {mt5.DEAL_REASON_SL: "sl", mt5.DEAL_REASON_TP: "tp"}.get(last.reason, "closed")
        profit = sum(deal.profit + deal.commission + deal.swap for deal in deals)
        finish_trade(config, state, trade["id"], last.price, last.time, profit, outcome)


# --- the loop -----------------------------------------------------------------

def to_candles(rates):
    """MT5 rate rows as plain dicts, the shape every brain and indicator works on."""
    return [
        {
            "time": int(row["time"]), "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]), "spread": int(row["spread"]),
        }
        for row in rates
    ]


def check_market(config, state):
    """Ask the brain once per newly closed candle."""
    journal = state.get("journal")
    paper = journal is not None and config["MODE"] == "dry"
    timeframe = getattr(mt5, "TIMEFRAME_" + config["TIMEFRAME"])
    rates = mt5.copy_rates_from_pos(config["SYMBOL"], timeframe, 0, brain.candles_needed(config) + 1)
    if rates is None or len(rates) < 2:
        return
    closed_candles = to_candles(rates[:-1])  # the last row is still forming
    candle = closed_candles[-1]
    candle_time = candle["time"]
    if candle_time == state["last_candle_time"]:
        return
    first_look = state["last_candle_time"] is None
    state["last_candle_time"] = candle_time
    if first_look:
        return  # never trade a candle that closed before the bot started
    tick = mt5.symbol_info_tick(config["SYMBOL"])
    symbol_info = mt5.symbol_info(config["SYMBOL"])
    if tick is None or symbol_info is None:
        return remember(state, "skipped: no price from MT5")
    if paper:
        settle_paper(config, state, candle, symbol_info)
    age = tick.time - candle_time
    if age > STALE_CANDLES * candle_seconds(config):
        return remember(state, f"skipped: candle closed {age // 60} min ago, the market was shut")
    spread_points = round((tick.ask - tick.bid) / symbol_info.point)
    start = day_start(tick.time)
    open_count = len(open_positions(config)) + (len(journal.open_trades("paper")) if paper else 0)
    pnl = pnl_today(tick.time) + (journal.profit_since(start, "paper") if paper else 0.0)
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
    fresh_tick = mt5.symbol_info_tick(config["SYMBOL"]) or tick
    opened = place_order(
        signal, fresh_tick, symbol_info, config, reason, atr_values[-1] if atr_values else None, spread_points
    )
    if opened and journal:
        position = {
            "side": signal, "lot": opened["volume"], "entry": opened["price"], "sl": opened["sl"],
            "tp": opened["tp"], "opened_at": fresh_tick.time, "reason": reason,
        }
        journal.open_trade(
            "paper" if paper else "mt5", state["run"], config["SYMBOL"], position,
            position_id=opened.get("position_id"), snapshot=ai_strategy.snapshot(closed_candles, config),
            brain=config["BRAIN"],
        )


def main():
    config = load_config()
    if not mt5.initialize():
        raise SystemExit(f"cannot connect to MT5 (is the terminal open and logged in?): {mt5.last_error()}")
    journal = Journal(JOURNAL_PATH)
    try:
        if not mt5.symbol_select(config["SYMBOL"], True):
            raise SystemExit(f"symbol {config['SYMBOL']} not found, broker naming differs: check SYMBOL in .env")
        account = mt5.account_info()
        is_demo = account is not None and account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        error = account_error(config["MODE"], is_demo)
        if error:
            raise SystemExit(error)
        state = {
            "paused": PAUSE_FLAG.exists(),
            "telegram_paused": False,
            "last_candle_time": None,
            "update_offset": 0,
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
                    settle_mt5(config, state)
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
        mt5.shutdown()


if __name__ == "__main__":
    main()
