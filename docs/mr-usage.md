# MR-Intraday implementation

This branch adds an offline, deterministic strategy engine and portfolio backtest.
The existing MT5/Telegram bot is unchanged. `BRAIN=rules` still uses MA crossover;
the new strategy is not yet an MT5 execution mode.

## Run

Python 3.10+ and timezone data for `America/New_York` are required. No Anthropic
or MetaTrader5 package is required for the offline runner. On Windows, install
`tzdata` if the operating system does not provide IANA timezone data.

```sh
python -B -m unittest discover -s tests -v
python backtest_mr.py --data prices.csv --news news.json --brokers brokers.json --output report.json
```

Prices CSV header: `symbol,time,open,high,low,close,spread`.
`time` is the UTC epoch second of the M1 open, aligned to a minute. OHLC are
bid prices. Spread is in price units, not points or pips. Resolve the broker
timestamp convention before export; the runner does not guess an offset.
Provide both symbols to test the shared 1% open-risk ceiling.

News JSON format:

```json
{"coverage_start": 1790812800, "coverage_end": 1793491200,
 "events": [{"time": 1790944200, "currency": "USD", "impact": "high"}]}
```

Coverage must span the complete input dataset. An empty event list is an explicit
claim of no relevant news, not an automatic calendar fetch. Supply a verified calendar.

Broker JSON is keyed by `GBPUSD` and `XAUUSD`. Each value must contain
`contract`, `step`, `minimum`, `maximum`, `commission`, and `slippage`.
Commission is round-trip account-currency cost per lot; slippage is price distance
applied adversely to all non-TP exits. Contract and cost values must come from
the actual broker. The sizing formula assumes the account currency is USD.

## Rules and interpretation

- Complete M15/M30 buckets are built from M1; gapped buckets are discarded.
- EMA20, Wilder ATR14/ADX14, ATR strict-less percentile over the last 100 M30
  readings, NY session/DST, news exclusion, and same-slot spread median are used.
- Spread warm-up requires all 15 minutes in the matching slot on each of the
  preceding 20 weekdays. Holidays with no data therefore block setups.
- The spread median pools all 300 prior observations. ATR ties share a rank.
- Setup locks EMA/ATR; the first M1 trigger queues entry for the next open.
- Reset is released only by a completed M15 z beyond the specified boundary.
- TP must be on the profitable side and at least 1R away at the actual entry.
- SL precedes TP for an ambiguous M1 bar. Gap-through SL exits at open. Time/news
  and end-of-day exits occur at the first available M1 open at/after the deadline.
- Time stop counts the next eight M15 boundaries, including a partial entry interval.
- Lots round down and respect broker bounds and the shared 1% risk budget.
- The runner retains open positions at dataset end instead of fabricating fills.

## Validation and limits

Unit tests validate mechanics, not profitability. No historical price dataset
or verified news calendar was supplied, so no performance claim is made.
Walk-forward OOS, random-entry baseline, regime ablation, cost sensitivity,
and the parameter grid remain research work requiring real data.

Each M1 bar uses one spread value; intraminute ask paths and slippage cannot be
reconstructed exactly. Symbols at identical timestamps process in alphabetical
order and other-symbol equity marks use the most recent available close.
The engine does not send orders, manage MT5 positions, persist live state, or
poll Telegram. Those require a separate execution adapter and broker validation.
