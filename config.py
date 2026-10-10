"""Settings shared by bot.py, replay.py and the tools: .env next to the code, defaults below.

Nothing here needs MetaTrader5, so the replay and the tests run on any OS.
"""
import sys
from pathlib import Path

# Installed build (TradeBot.exe): user files sit next to the exe, not inside its _internal folder.
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
ENV_PATH = APP_DIR / ".env"
JOURNAL_PATH = APP_DIR / "journal.db"
MT5_DIR = APP_DIR / "mt5"  # TradeBot's own MT5 terminal (portable), set up from the app's MetaTrader 5 page
STOP_FLAG = APP_DIR / "stop.flag"  # /stop leaves this so run_forever.bat does not restart the bot; the dashboard creates it to ask for a stop
ALIVE = APP_DIR / "bot.alive"  # bot.py touches it every loop so the dashboard can tell a bot is running

DEFAULTS = {
    "MODE": "demo",  # the app offers demo and live; dry (paper) stays for the console tools and replay
    "BRAIN": "rules",
    "STRATEGY": "trend_pullback",
    "AI_PROVIDER": "claude",  # one, or an order to fall back through: claude,openai,gemini
    "AI_MODEL": "",  # older single setting: the first provider's model when its own below is empty
    "AI_MODEL_CLAUDE": "",
    "AI_MODEL_OPENAI": "",
    "AI_MODEL_GEMINI": "",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_API_KEY": "",
    "GEMINI_API_KEY": "",
    "AI_BUDGET_USD": "5",
    "SYMBOL": "XAUUSD",
    "TIMEFRAME": "M15",
    "LOT": "0.01",
    "CONTRACT_SIZE": "100",
    "SL_ATR": "1.5",
    "TP_ATR": "3.0",
    "ATR_PERIOD": "14",
    "SL_POINTS": "500",
    "TP_POINTS": "1000",
    "MAX_DAILY_LOSS": "20",
    "MAX_TRADES_PER_DAY": "0",
    "MAX_SPREAD_POINTS": "50",
    "NEWS_BLACKOUT_MINUTES": "30",
    "MAGIC": "20261003",
    "FILLING": "IOC",
    "TELEGRAM_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "SIGNAL_WEBHOOK": "off",
    "WEBHOOK_TOPIC": "",
    "SIGNAL_TELEGRAM": "off",
    "AUTO_START_BOT": "off",
    # The broker accounts behind Demo and Live (no passwords: TradeBot's MT5 remembers those itself)
    "DEMO_LOGIN": "",
    "DEMO_SERVER": "",
    "LIVE_LOGIN": "",
    "LIVE_SERVER": "",
}
NUMBER_TYPES = {
    "AI_BUDGET_USD": float,
    "LOT": float,
    "CONTRACT_SIZE": float,
    "SL_ATR": float,
    "TP_ATR": float,
    "ATR_PERIOD": int,
    "SL_POINTS": int,
    "TP_POINTS": int,
    "MAX_DAILY_LOSS": float,
    "MAX_TRADES_PER_DAY": int,
    "MAX_SPREAD_POINTS": int,
    "NEWS_BLACKOUT_MINUTES": int,
    "MAGIC": int,
}
# Candle length of every MT5 timeframe name, in seconds.
TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600, "M12": 720,
    "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}
FILLING_MODES = ("IOC", "FOK", "RETURN")


def terminal_args():
    """mt5.initialize() arguments: TradeBot's own portable terminal when it has one, else the PC's default MT5.

    Every MT5 connection (bot, dashboard, tools) goes through this, so none of them can end up on the
    terminal the owner trades by hand.
    """
    terminal = MT5_DIR / "terminal64.exe"
    return {"path": str(terminal), "portable": True} if terminal.exists() else {}


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
    if config["BRAIN"] not in ("rules", "ai", "hybrid"):
        raise SystemExit(f"BRAIN must be rules, ai or hybrid, got {config['BRAIN']!r}")
    order = [name.strip() for name in config["AI_PROVIDER"].split(",") if name.strip()]
    if not order or len(set(order)) != len(order) or any(name not in ("claude", "openai", "gemini") for name in order):
        raise SystemExit(f"AI_PROVIDER must be claude, openai or gemini, or an order like claude,openai: got {config['AI_PROVIDER']!r}")
    from strategies import STRATEGIES, selected  # here to keep strategies.py free to import config
    unknown = [name for name in selected(config) if name not in STRATEGIES]
    if unknown or not selected(config):
        raise SystemExit(f"unknown STRATEGY {config['STRATEGY']!r}, use all or any of: {', '.join(STRATEGIES)}")
    if min(config["LOT"], config["SL_POINTS"], config["TP_POINTS"], config["MAX_DAILY_LOSS"]) <= 0:
        raise SystemExit("LOT, SL_POINTS, TP_POINTS and MAX_DAILY_LOSS must all be above 0")
    if config["MAX_TRADES_PER_DAY"] < 0:
        raise SystemExit("MAX_TRADES_PER_DAY must be 0 (no limit) or above")
    if config["CONTRACT_SIZE"] <= 0 or config["AI_BUDGET_USD"] < 0:
        raise SystemExit("CONTRACT_SIZE must be above 0 and AI_BUDGET_USD at least 0")
    if min(config["SL_ATR"], config["TP_ATR"]) < 0 or config["ATR_PERIOD"] < 1:
        raise SystemExit("SL_ATR and TP_ATR must be 0 (fixed points) or above, ATR_PERIOD at least 1")
    if config["TIMEFRAME"] not in TIMEFRAME_SECONDS:
        raise SystemExit(f"unknown TIMEFRAME {config['TIMEFRAME']!r}, use one of {' '.join(TIMEFRAME_SECONDS)}")
    if config["FILLING"] not in FILLING_MODES:
        raise SystemExit(f"unknown FILLING {config['FILLING']!r}, use IOC, FOK or RETURN")
    if any(config[key] not in ("on", "off") for key in ("SIGNAL_WEBHOOK", "SIGNAL_TELEGRAM", "AUTO_START_BOT")):
        raise SystemExit("SIGNAL_WEBHOOK, SIGNAL_TELEGRAM and AUTO_START_BOT must be on or off")
    if config["SIGNAL_WEBHOOK"] == "on" and len(config["WEBHOOK_TOPIC"]) < 20:
        raise SystemExit("SIGNAL_WEBHOOK=on needs a WEBHOOK_TOPIC of 20+ characters: it is the only secret")
    if config["SIGNAL_TELEGRAM"] == "on" and not (config["TELEGRAM_TOKEN"] and config["TELEGRAM_CHAT_ID"]):
        raise SystemExit("SIGNAL_TELEGRAM=on needs TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")
    return config


def candle_seconds(config):
    return TIMEFRAME_SECONDS[config["TIMEFRAME"]]
