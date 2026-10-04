"""Settings shared by bot.py, replay.py and the tools: .env next to the code, defaults below.

Nothing here needs MetaTrader5, so the replay and the tests run on any OS.
"""
from pathlib import Path

ENV_PATH = Path(__file__).with_name(".env")
JOURNAL_PATH = Path(__file__).with_name("journal.db")

DEFAULTS = {
    "MODE": "dry",
    "BRAIN": "rules",
    "STRATEGY": "trend_pullback",
    "ANTHROPIC_API_KEY": "",
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
    "MAX_SPREAD_POINTS": "50",
    "MAGIC": "20261003",
    "FILLING": "IOC",
    "TELEGRAM_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
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
    "MAX_SPREAD_POINTS": int,
    "MAGIC": int,
}
# Candle length of every MT5 timeframe name, in seconds.
TIMEFRAME_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300, "M6": 360, "M10": 600, "M12": 720,
    "M15": 900, "M20": 1200, "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200, "D1": 86400, "W1": 604800, "MN1": 2592000,
}
FILLING_MODES = ("IOC", "FOK", "RETURN")


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
    from strategies import STRATEGIES, selected  # here to keep strategies.py free to import config
    unknown = [name for name in selected(config) if name not in STRATEGIES]
    if unknown or not selected(config):
        raise SystemExit(f"unknown STRATEGY {config['STRATEGY']!r}, use all or any of: {', '.join(STRATEGIES)}")
    if min(config["LOT"], config["SL_POINTS"], config["TP_POINTS"], config["MAX_DAILY_LOSS"]) <= 0:
        raise SystemExit("LOT, SL_POINTS, TP_POINTS and MAX_DAILY_LOSS must all be above 0")
    if config["CONTRACT_SIZE"] <= 0 or config["AI_BUDGET_USD"] < 0:
        raise SystemExit("CONTRACT_SIZE must be above 0 and AI_BUDGET_USD at least 0")
    if min(config["SL_ATR"], config["TP_ATR"]) < 0 or config["ATR_PERIOD"] < 1:
        raise SystemExit("SL_ATR and TP_ATR must be 0 (fixed points) or above, ATR_PERIOD at least 1")
    if config["TIMEFRAME"] not in TIMEFRAME_SECONDS:
        raise SystemExit(f"unknown TIMEFRAME {config['TIMEFRAME']!r}, use one of {' '.join(TIMEFRAME_SECONDS)}")
    if config["FILLING"] not in FILLING_MODES:
        raise SystemExit(f"unknown FILLING {config['FILLING']!r}, use IOC, FOK or RETURN")
    return config


def candle_seconds(config):
    return TIMEFRAME_SECONDS[config["TIMEFRAME"]]
