"""Export the broker's candles to a CSV file for replay.py. Needs the broker connected (MT5: terminal open, Windows).

    python export_history.py --days 90 --out history.csv

Symbol and timeframe come from .env. Also prints the symbol's contract size, point and
digits, so CONTRACT_SIZE in .env can be checked against the broker.
"""
import argparse
import csv
from datetime import datetime, timezone

import brokers
from config import candle_seconds, load_config


def write_csv(rates, path, digits):
    """Write candles (anything indexable by field name) as replay.py expects them."""
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
    broker = brokers.load(config)
    if not broker.connect():
        raise SystemExit(broker.connection_hint())
    try:
        if not broker.select_symbol(config["SYMBOL"]):
            raise SystemExit(f"symbol {config['SYMBOL']} not found, check SYMBOL in .env")
        info = broker.symbol(config["SYMBOL"])
        count = args.days * 86400 // candle_seconds(config)
        candles = broker.candles(config["SYMBOL"], config["TIMEFRAME"], count)
        if not candles or len(candles) < 2:
            raise SystemExit("no candles returned (the terminal may hold less history, scroll the chart back)")
        written = write_csv(candles[:-1], args.out, info.digits)  # the last candle is still forming
        print(f"wrote {written} {config['TIMEFRAME']} candles of {config['SYMBOL']} to {args.out}")
        print(f"{config['SYMBOL']}: contract size {info.contract_size}, point {info.point}, digits {info.digits}")
        if float(config["CONTRACT_SIZE"]) != float(info.contract_size):
            print(f"note: CONTRACT_SIZE in .env is {config['CONTRACT_SIZE']}, the broker says {info.contract_size}")
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
