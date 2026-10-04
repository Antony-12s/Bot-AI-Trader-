"""Offline portfolio backtest; requires explicit news coverage and broker costs."""
import argparse
import csv
import json
from pathlib import Path

from mr_intraday import Bar, Engine


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, help='CSV: symbol,time,open,high,low,close,spread; UTC epoch seconds')
    parser.add_argument('--news', required=True, help='JSON: coverage_start,coverage_end,events')
    parser.add_argument('--brokers', required=True, help='JSON keyed by GBPUSD and XAUUSD; contract,step,minimum,maximum,commission,slippage')
    parser.add_argument('--output', required=True)
    parser.add_argument('--equity', type=float, default=10000)
    parser.add_argument('--risk', type=float, default=.005)
    args = parser.parse_args()
    with open(args.data, newline='') as source:
        rows = sorted(csv.DictReader(source), key=lambda r: (int(r['time']), r['symbol']))
    if not rows:
        parser.error('empty price data')
    news = json.loads(Path(args.news).read_text())
    if news['coverage_start'] > int(rows[0]['time']) or news['coverage_end'] < int(rows[-1]['time']) + 60:
        parser.error('news coverage must include the entire price dataset')
    broker = json.loads(Path(args.brokers).read_text())
    engines = {s: Engine(s, news['events'], equity=args.equity, risk=args.risk, **broker[s])
               for s in {r['symbol'] for r in rows}}
    equity, trades = args.equity, []
    for row in rows:
        engine = engines[row['symbol']]
        # Conservatively reserve the full initial SL risk for all other positions.
        risk = sum(abs(e.position.entry - e.position.sl) * e.position.lots * e.contract
                   for e in engines.values() if e is not engine and e.position)
        bar = Bar(int(row['time']), *(float(row[k]) for k in ('open', 'high', 'low', 'close', 'spread')))
        floating = 0
        for e in engines.values():
            if e.position:
                p = e.position
                quote = bar.open if e is engine else e.bars[-1].close
                spread = bar.spread if e is engine else e.bars[-1].spread
                quote += spread if p.side == -1 else 0
                floating += p.side * (quote - p.entry) * p.lots * e.contract
        before = len(engine.trades)
        engine.on_bar(bar, equity=equity + floating, open_risk=risk)
        for trade in engine.trades[before:]:
            equity += trade['pnl']
            trades.append(trade)
    report = dict(initial_equity=args.equity, realized_equity=equity, trades=trades,
                  trade_count=len(trades), net_pnl=equity - args.equity,
                  open_positions={s: vars(e.position) for s, e in engines.items() if e.position},
                  notes=['No synthetic liquidation at dataset end.',
                         'Equity includes floating PnL at the current symbol open and last available close for other symbols.',
                         'M1 spread is held constant within each minute; tick execution is not simulated.'])
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(f"{len(trades)} closed trades; net PnL {equity - args.equity:.2f}; report: {args.output}")


if __name__ == '__main__':
    run()
