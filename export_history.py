"""Export MT5 candles to a CSV file for replay.py. Needs the MT5 terminal open (Windows).

    python export_history.py --days 90 --out history.csv

Symbol and timeframe come from .env. Also prints the symbol's contract size, point and
digits, so CONTRACT_SIZE in .env can be checked against the broker.
"""
import argparse
import csv
from datetime import datetime, timezone

import MetaTrader5 as mt5

from config import candle_seconds, load_config, terminal_args


def write_csv(rates, path, digits):
    """Write MT5 rates (anything indexable by field name) as replay.py expects them."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "spread"])
        for row in rates:
            when = datetime.fromtimestamp(int(row["time"]), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            prices = [f"{float(row[name]):.{digits}f}" for name in ("open", "high", "low", "close")]
            writer.writerow([when] + prices + [int(row["spread"])])
    return len(rates)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=90, help="how far back to go (default 90)")
    parser.add_argument("--out", default="history.csv")
    args = parser.parse_args()
    config = load_config()
    if not mt5.initialize(**terminal_args()):
        raise SystemExit(f"cannot connect to MT5 (is the terminal open and logged in?): {mt5.last_error()}")
    try:
        if not mt5.symbol_select(config["SYMBOL"], True):
            raise SystemExit(f"symbol {config['SYMBOL']} not found, check SYMBOL in .env")
        info = mt5.symbol_info(config["SYMBOL"])
        count = args.days * 86400 // candle_seconds(config)
        rates = mt5.copy_rates_from_pos(config["SYMBOL"], getattr(mt5, "TIMEFRAME_" + config["TIMEFRAME"]), 0, count)
        if rates is None or len(rates) < 2:
            raise SystemExit(f"no candles returned: {mt5.last_error()} (the terminal may hold less history, scroll the chart back)")
        written = write_csv(rates[:-1], args.out, info.digits)  # the last candle is still forming
        print(f"wrote {written} {config['TIMEFRAME']} candles of {config['SYMBOL']} to {args.out}")
        print(f"{config['SYMBOL']}: contract size {info.trade_contract_size}, point {info.point}, digits {info.digits}")
        if float(config["CONTRACT_SIZE"]) != float(info.trade_contract_size):
            print(f"note: CONTRACT_SIZE in .env is {config['CONTRACT_SIZE']}, the broker says {info.trade_contract_size}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
