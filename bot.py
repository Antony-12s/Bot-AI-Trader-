"""MT5 auto-trading bot controlled from Telegram.

Run with the MT5 terminal open and logged in:  python bot.py
Settings live in .env (copy .env.example). Strategy lives in strategy.py.
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5

import strategy

ENV_PATH = Path(__file__).with_name(".env")
POLL_SECONDS = 5
SECONDS_PER_DAY = 86400
DEFAULTS = {
    "MODE": "dry",
    "SYMBOL": "XAUUSD",
    "TIMEFRAME": "M15",
    "LOT": "0.01",
    "SL_POINTS": "500",
    "TP_POINTS": "1000",
    "MAX_DAILY_LOSS": "20",
    "MAX_SPREAD_POINTS": "50",
    "MAGIC": "20261003",
    "FILLING": "IOC",
    "TELEGRAM_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
}
NUMBER_TYPES = {
    "LOT": float,
    "SL_POINTS": int,
    "TP_POINTS": int,
    "MAX_DAILY_LOSS": float,
    "MAX_SPREAD_POINTS": int,
    "MAGIC": int,
}


def load_config(env_path=ENV_PATH):
    config = dict(DEFAULTS)
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and not key.lstrip().startswith("#"):
                config[key.strip()] = value.strip()
    for key, convert in NUMBER_TYPES.items():
        config[key] = convert(config[key])
    if config["MODE"] not in ("dry", "demo", "live"):
        raise SystemExit(f"MODE must be dry, demo or live, got {config['MODE']!r}")
    if min(config["LOT"], config["SL_POINTS"], config["TP_POINTS"], config["MAX_DAILY_LOSS"]) <= 0:
        raise SystemExit("LOT, SL_POINTS, TP_POINTS and MAX_DAILY_LOSS must all be above 0")
    for constant in ("TIMEFRAME_" + config["TIMEFRAME"], "ORDER_FILLING_" + config["FILLING"]):
        if not hasattr(mt5, constant):
            raise SystemExit(f"unknown MT5 constant {constant}, check TIMEFRAME / FILLING in .env")
    return config


# --- pure decisions -------------------------------------------------------

def block_reason(paused, open_position_count, pnl_today, spread_points, config):
    """Return why a new trade is not allowed right now, or None when it is."""
    if paused:
        return "paused"
    if open_position_count:
        return "position already open"
    if pnl_today <= -config["MAX_DAILY_LOSS"]:
        return f"daily loss limit hit ({pnl_today:.2f})"
    if spread_points > config["MAX_SPREAD_POINTS"]:
        return f"spread too wide ({spread_points} points)"
    return None


def account_error(mode, is_demo_account):
    """Return why this account must not be traded in this mode, or None."""
    if mode == "demo" and not is_demo_account:
        return "MODE=demo but the logged-in MT5 account is not a demo account, refusing to trade"
    return None


def build_order(side, tick, symbol_info, config):
    """Market order request for mt5.order_send, always with stop loss and take profit."""
    is_buy = side == "buy"
    direction = 1 if is_buy else -1
    price = tick.ask if is_buy else tick.bid
    point, digits = symbol_info.point, symbol_info.digits
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config["SYMBOL"],
        "volume": config["LOT"],
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(price - direction * config["SL_POINTS"] * point, digits),
        "tp": round(price + direction * config["TP_POINTS"] * point, digits),
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
    if command == "/pause":
        state["paused"] = True
        return "paused: no new trades (open positions keep their SL/TP)"
    if command == "/resume":
        state["paused"] = False
        return "resumed"
    if command == "/status":
        return status_text(config, state)
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

def open_positions(config):
    positions = mt5.positions_get(symbol=config["SYMBOL"]) or ()
    return [position for position in positions if position.magic == config["MAGIC"]]


def pnl_today(server_now):
    """Realized result of every trade on the account since server midnight.

    Manual trades count too, on purpose: the limit protects the account.
    Floating loss is not counted, it is capped by the single position's SL.
    """
    day_start = server_now - server_now % SECONDS_PER_DAY
    # Query a wide window and filter on deal.time ourselves: deal and tick times
    # share the broker's server clock, the PC clock does not.
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(days=3), now + timedelta(days=3)) or ()
    return sum(
        deal.profit + deal.commission + deal.swap
        for deal in deals
        if deal.time >= day_start and deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
    )


def status_text(config, state):
    tick = mt5.symbol_info_tick(config["SYMBOL"])
    pnl = f"{pnl_today(tick.time):.2f}" if tick else "unknown"
    return (
        f"mode={config['MODE']} paused={state['paused']} symbol={config['SYMBOL']} "
        f"open_positions={len(open_positions(config))} pnl_today={pnl}"
    )


def place_order(side, tick, symbol_info, config):
    request = build_order(side, tick, symbol_info, config)
    summary = (
        f"{side} {request['volume']} {request['symbol']} @ {request['price']} "
        f"sl {request['sl']} tp {request['tp']}"
    )
    if config["MODE"] == "dry":
        notify(config, "[dry] would " + summary)
        return
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
    else:
        notify(config, "opened " + summary)


def check_market(config, state):
    """Evaluate the strategy once per newly closed candle."""
    timeframe = getattr(mt5, "TIMEFRAME_" + config["TIMEFRAME"])
    rates = mt5.copy_rates_from_pos(config["SYMBOL"], timeframe, 0, strategy.CANDLES_NEEDED + 1)
    if rates is None or len(rates) < 2:
        return
    closed_candles = rates[:-1]  # the last row is still forming
    candle_time = closed_candles[-1]["time"]
    if candle_time == state["last_candle_time"]:
        return
    first_look = state["last_candle_time"] is None
    state["last_candle_time"] = candle_time
    if first_look:
        return  # never trade a candle that closed before the bot started
    signal = strategy.decide([float(candle["close"]) for candle in closed_candles])
    if not signal:
        return
    tick = mt5.symbol_info_tick(config["SYMBOL"])
    symbol_info = mt5.symbol_info(config["SYMBOL"])
    if tick is None or symbol_info is None:
        notify(config, f"{signal} signal skipped: no price from MT5")
        return
    spread_points = round((tick.ask - tick.bid) / symbol_info.point)
    reason = block_reason(
        state["paused"], len(open_positions(config)), pnl_today(tick.time), spread_points, config
    )
    if reason:
        notify(config, f"{signal} signal skipped: {reason}")
        return
    place_order(signal, tick, symbol_info, config)


def main():
    config = load_config()
    if not mt5.initialize():
        raise SystemExit(f"cannot connect to MT5 (is the terminal open and logged in?): {mt5.last_error()}")
    try:
        if not mt5.symbol_select(config["SYMBOL"], True):
            raise SystemExit(f"symbol {config['SYMBOL']} not found, broker naming differs: check SYMBOL in .env")
        account = mt5.account_info()
        is_demo = account is not None and account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        error = account_error(config["MODE"], is_demo)
        if error:
            raise SystemExit(error)
        state = {"paused": False, "last_candle_time": None, "update_offset": 0}
        read_commands(config, state, skip_only=True)
        notify(config, "bot started: " + status_text(config, state))
        while True:
            read_commands(config, state)
            check_market(config, state)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        notify(config, "bot stopped")
    except Exception as error:
        notify(config, f"bot crashed: {error!r}")
        raise
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
