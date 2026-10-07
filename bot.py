"""MT5 auto-trading bot controlled from Telegram.

Run with the MT5 terminal open and logged in:  python bot.py
Settings live in .env (copy .env.example). It trades what the user's agents decide (agents.py,
agents.json: strategy templates in strategies.py, written rules read by plainrules.py, or an AI)
and the outside signals (TradingView, Telegram); every decision and trade goes into journal.db.

Modes: dry trades on paper (virtual positions filled from the live candles, nothing is sent
to the broker), demo and live send real orders. All three write the same journal, so the
AI brain learns in dry mode and keeps that experience when it moves on.

Unattended running: run_forever.bat restarts the bot after a crash, install_autostart.bat
starts it at logon, /stop from Telegram ends it for good (until the next start).
"""
import json
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

import MetaTrader5 as mt5

import agents
import ai_strategy
import brain
import fills
import indicators
import news
import signals
from config import ALIVE, ENV_PATH, JOURNAL_PATH, STOP_FLAG, candle_seconds, terminal_args
from config import load_config as load_settings
from journal import Journal, summarize
from risk import account_error, block_reason, day_start, stop_distances, stop_levels

POLL_SECONDS = 5
STALE_CANDLES = 2  # a candle that closed this many candle lengths ago is old news (market was shut)
ENTRY_GRACE_SECONDS = 60  # strategy entries land this soon after a candle opens (an AI call included)


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
        state["paused"] = True
        return "paused: no new trades (open positions keep their SL/TP)"
    if command == "/resume":
        state["paused"] = False
        return "resumed"
    if command == "/stop":
        state["stopping"] = True
        return "stopping: the bot exits now and the watchdog will not restart it (open positions keep their SL/TP)"
    if command in ("/buy", "/sell"):
        if config["SIGNAL_TELEGRAM"] != "on":
            return "Telegram signals are off: turn them on in the dashboard's Signals page"
        state.setdefault("pending", []).append(("telegram", command[1:], time.time()))
        return f"{command[1:]} signal queued: it trades if the risk rules allow"
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
    mt5.initialize(**terminal_args())
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
    # only the AI brain's own calls teach it: an agent's, a webhook's or a rules trade is not its lesson
    if brain.learns(config) and trade.get("brain") in brain.LEARNING:
        spent = journal.spend(source="bot", since=day_start(closed_at))
        lesson, playbook = ai_strategy.review(
            journal, trade, price_path(config, trade), config, "bot", state["run"], spent
        )
        if lesson:
            text += "\nlesson: " + lesson
        if playbook:
            text += "\nplaybook rewritten:\n" + playbook
    elif (trade.get("brain") or "").startswith("agent:"):
        agent = next((a for a in agents.load() if "agent:" + a["id"] == trade["brain"]), None)
        if agent:  # deleted agents learn nothing
            path = price_path(dict(config, SYMBOL=trade["symbol"], TIMEFRAME=agent["timeframe"]), trade)
            spent = journal.spend(since=day_start(closed_at))
            text += "".join("\n" + note for note in agents.after_trade(agent, trade, journal, config, path, state["run"], spent))
    notify(config, text)


def settle_paper(config, state, candle, symbol_info):
    """Close dry-mode paper positions on this candle's market that its range took out."""
    journal = state["journal"]
    for trade in journal.open_trades("paper"):
        if trade["symbol"] != config["SYMBOL"]:
            continue  # agents trade other markets: a gold candle must never settle a EURUSD trade
        # ponytail: an entry deep inside this candle (an outside signal) cannot be judged by its whole
        # high/low, which includes prices from before the entry; skip it. Exits inside that one candle
        # are missed; settle from live ticks if that matters.
        if trade["opened_at"] - int(candle["time"]) > ENTRY_GRACE_SECONDS:
            continue
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
            "volume": tick_volume(row),
        }
        for row in rates
    ]


def tick_volume(row):
    """Ticks in the candle (MT5's volume for CFDs and forex: no exchange volume), 0 when the row has none."""
    try:
        return int(row["tick_volume"])
    except (KeyError, ValueError, IndexError):
        return 0


def current_block(config, state, tick, symbol_info):
    """(why no new trade is allowed now or None, spread in points): the risk rules every signal passes."""
    journal = state.get("journal")
    paper = journal is not None and config["MODE"] == "dry"
    spread_points = round((tick.ask - tick.bid) / symbol_info.point)
    start = day_start(tick.time)
    # one open position per market (MT5 positions are already filtered by symbol and magic)
    paper_open = [t for t in journal.open_trades("paper") if t["symbol"] == config["SYMBOL"]] if paper else []
    open_count = len(open_positions(config)) + len(paper_open)
    pnl = pnl_today(tick.time) + (journal.profit_since(start, "paper") if paper else 0.0)
    spent = journal.spend(source="bot", since=start) if journal else 0.0
    traded = journal.trades_opened_since(start, "paper" if paper else "mt5") if journal else 0
    blocked = block_reason(state["paused"], open_count, pnl, spread_points, config, spent, traded)
    if not blocked:  # last: the calendar is a network read, once an hour at most
        currencies = (getattr(symbol_info, "currency_base", ""), getattr(symbol_info, "currency_profit", ""))
        blocked = news.blackout(currencies, config["NEWS_BLACKOUT_MINUTES"])
        if news.cache["problem"] and state.get("news_problem") != news.cache["problem"]:
            state["news_problem"] = news.cache["problem"]
            notify(config, news.cache["problem"])  # once per new problem: the owner should know the pause is off
    return blocked, spread_points


def open_trade(config, state, side, reason, closed_candles, tick, symbol_info, spread_points, decided_by):
    """Place the order (paper in dry mode) and journal it. Returns True when a position opened."""
    journal = state.get("journal")
    atr_values = indicators.atr(closed_candles, config["ATR_PERIOD"])
    opened = place_order(side, tick, symbol_info, config, reason, atr_values[-1] if atr_values else None, spread_points)
    if opened and journal:
        position = {
            "side": side, "lot": opened["volume"], "entry": opened["price"], "sl": opened["sl"],
            "tp": opened["tp"], "opened_at": tick.time, "reason": reason,
        }
        journal.open_trade(
            "paper" if config["MODE"] == "dry" else "mt5", state["run"], config["SYMBOL"], position,
            position_id=opened.get("position_id"), snapshot=ai_strategy.snapshot(closed_candles, config),
            brain=decided_by,
        )
    return bool(opened)


def outside_signals(config, state):
    """Trade the queued TradingView (webhook) and Telegram signals under the same risk rules."""
    if config["SIGNAL_WEBHOOK"] == "on":
        try:
            messages, state["webhook_since"] = signals.poll(config["WEBHOOK_TOPIC"], state["webhook_since"])
            state["pending"] += [("webhook", text, sent_at) for text, sent_at in messages]
        except Exception as error:  # the relay being down must not stop the bot
            print("webhook poll failed:", error)
    while state["pending"]:
        source, text, sent_at = state["pending"].pop(0)
        side, why = signals.parse(text)
        tick = mt5.symbol_info_tick(config["SYMBOL"])
        symbol_info = mt5.symbol_info(config["SYMBOL"])
        late = signals.too_old(sent_at, time.time())  # queued while MT5 was down, or the relay lagged
        if side and late:
            why = late
        elif side and (tick is None or symbol_info is None):
            why = "no price from MT5"
        elif side:
            blocked, spread_points = current_block(config, state, tick, symbol_info)
            why = blocked
            if not blocked:
                # closed candles only (start at 1): the ATR for the stops and the snapshot the AI learns from
                timeframe = getattr(mt5, "TIMEFRAME_" + config["TIMEFRAME"])
                rates = mt5.copy_rates_from_pos(config["SYMBOL"], timeframe, 1, brain.candles_needed(config))
                if rates is None or len(rates) < 2:
                    why = "no candles from MT5"
                elif not open_trade(config, state, side, f"{source}: {text.strip()[:80]}", to_candles(rates),
                                    tick, symbol_info, spread_points, source):
                    why = "order rejected by the broker"
        outcome = f"{side or 'no'} signal from {source}: " + (f"skipped, {why}" if why else "traded")
        state["last_decision"] = outcome
        notify(config, outcome)  # prints it too
        if state.get("journal"):
            at = tick.time if tick else int(time.time())
            state["journal"].record_decision(source, state["run"], "signal", at, side or "invalid", ("skipped: " + why) if why else "traded: " + text.strip()[:80])


def wrong_terminal(info):
    """Why this terminal must not be traded through, or None. With its own MT5, TradeBot uses only that one,
    never the MT5 the owner trades in by hand (the library can attach to another running terminal)."""
    own = terminal_args().get("path")
    if own and (info is None or Path(info.path).resolve() != Path(own).parent.resolve()):
        return f"connected to the wrong MT5 ({info.path if info else 'none'}), expected TradeBot's own in {Path(own).parent}"
    return None


def run_agents(config, state):
    """The user's agents (agents.json): on each market, decide when it is time, then act by mode."""
    journal = state.get("journal")
    paper = journal is not None and config["MODE"] == "dry"
    for agent in agents.load():
        frame = getattr(mt5, "TIMEFRAME_" + agent["timeframe"], None)
        for market in agent["markets"]:
            try:
                run_agent_on(config, state, journal, paper, agent, frame, market)
            except Exception as error:  # one broken market or agent must not stop the bot
                print(f"agent {agent.get('name')} on {market}: {error!r}")


def run_agent_on(config, state, journal, paper, agent, frame, market):
    mt5.symbol_select(market, True)
    rates = mt5.copy_rates_from_pos(market, frame, 0, brain.candles_needed(config) + 1)
    symbol_info, tick = mt5.symbol_info(market), mt5.symbol_info_tick(market)
    if rates is None or len(rates) < 2 or symbol_info is None or tick is None:
        return
    closed = to_candles(rates[:-1])  # the last row is still forming
    candle = closed[-1]
    market_config = dict(config, SYMBOL=market, TIMEFRAME=agent["timeframe"],
                         CONTRACT_SIZE=getattr(symbol_info, "trade_contract_size", 0) or config["CONTRACT_SIZE"])
    settle_key = (market, agent["timeframe"])
    if paper and state["settle_seen"].get(settle_key) not in (None, candle["time"]):
        settle_paper(market_config, state, candle, symbol_info)  # this market's paper trades, once per new candle
    state["settle_seen"][settle_key] = candle["time"]
    key, now = (agent["id"], market), time.time()
    seen = state["agent_seen"].get(key)
    if agent["runs"] == "candle":
        state["agent_seen"][key] = candle["time"]
        if seen is None or seen == candle["time"]:
            return  # nothing new; and never act on a candle that closed before the bot started
    else:
        if seen is not None and now - seen < agent["every_minutes"] * 60:
            return
        state["agent_seen"][key] = now
    if tick.time - candle["time"] > STALE_CANDLES * candle_seconds(market_config):
        return  # the market is shut
    spent = journal.spend(since=day_start(tick.time)) if journal else 0.0
    experience = journal.experience_text(brains=("agent:" + agent["id"],)) if journal and agent["analyst"] != "rules" else ""
    signal, confidence, reason, cost = agents.decide(agent, closed, market_config, spent, experience)
    note = f"{market} · {confidence}% · {reason}"
    if signal and agent["mode"] == "suggest":
        notify(config, f"Agent {agent['name']} suggests {signal.upper()} {market} ({confidence}%): {reason}")
    if signal and agent["mode"] == "auto":
        blocked, spread_points = current_block(market_config, state, tick, symbol_info)
        if blocked:
            note += f" (not traded: {blocked})"
        elif not open_trade(market_config, state, signal, f"agent {agent['name']}: {reason}", closed,
                            mt5.symbol_info_tick(market) or tick, symbol_info, spread_points, "agent:" + agent["id"]):
            note += " (not traded: the order was rejected)"
    if journal:
        journal.record_decision("agent:" + agent["id"], state["run"], "agent", candle["time"], signal or "hold", note, cost)


def should_stop(state):
    """/stop from Telegram, or stop.flag created by the dashboard while the bot runs."""
    return bool(state.get("stopping")) or STOP_FLAG.exists()


INSTANCE_PORT = 47821  # held while a bot runs: the OS frees it the moment the process ends, even on a crash


def single_instance():
    """A socket only one process can bind: two bots on one account would double every signal."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: no other socket may share the port
        lock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        lock.bind(("127.0.0.1", INSTANCE_PORT))
    except OSError:
        lock.close()
        raise SystemExit("another TradeBot bot is already running on this PC; stop it first") from None
    return lock


def main():
    config = load_config()
    instance = single_instance()  # noqa: F841 - held open until the process exits
    if not mt5.initialize(**terminal_args()):
        raise SystemExit(f"cannot connect to MT5 (is the terminal open and logged in?): {mt5.last_error()}")
    error = wrong_terminal(mt5.terminal_info())
    if error:
        mt5.shutdown()
        raise SystemExit(error)
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
            "paused": False,
            "update_offset": 0,
            "journal": journal,
            "run": f"{config['MODE']}-{datetime.now():%Y%m%d-%H%M%S}",
            "pending": [],  # (source, text, sent_at) signals waiting for the risk rules
            "agent_seen": {},  # (agent id, market) -> last candle time (or run time, for timer agents)
            "settle_seen": {},  # (market, timeframe) -> last candle that settled paper trades there
            "webhook_since": str(int(time.time())),  # alerts sent while the bot was off are stale, skip them
        }
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
        read_commands(config, state, skip_only=True)
        notify(config, "bot started: " + status_text(config, state))
        while not should_stop(state):
            ALIVE.touch()
            read_commands(config, state)
            if ensure_connected(config, state):
                if config["MODE"] != "dry":
                    settle_mt5(config, state)
                outside_signals(config, state)
                run_agents(config, state)
            time.sleep(POLL_SECONDS)
        STOP_FLAG.touch()
        notify(config, "bot stopped by /stop or the dashboard")
    except KeyboardInterrupt:
        notify(config, "bot stopped")
    except Exception as error:
        notify(config, f"bot crashed: {error!r}")
        raise
    finally:
        ALIVE.unlink(missing_ok=True)
        journal.close()
        mt5.shutdown()


if __name__ == "__main__":
    main()
