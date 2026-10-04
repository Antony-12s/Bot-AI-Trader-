"""Pick the broker named by BROKER in .env. Everything above this line talks to a broker object, never to a platform API.

Every broker offers the same small surface:

    connect() -> bool                  connection_hint() -> str      alive() -> bool      shutdown()
    account() -> namespace(login, server, is_demo, balance, currency) | None
    symbols() -> [name, ...]           select_symbol(name) -> bool
    symbol(name) -> namespace(name, point, digits, stops_level, contract_size, min_lot, spread_points, filling) | None
    tick(name) -> namespace(bid, ask, time) | None                time is broker server time, seconds
    candles(name, timeframe, count) -> [dict(time, open, high, low, close, spread), ...] | None
                                       oldest first, the last one still forming, spread in points
    open_positions(name, magic) -> list of this bot's open positions
    position_result(position_id, trade) -> dict(exit, closed_at, profit, outcome) | None while still open
                                       trade is the journal row (sl, tp, opened_at) for platforms
                                       whose deals do not say whether SL or TP closed the position
    realized_since(server_time) -> float   closed result of every trade on the account since then
    market_order(side, name, lot, price, sl, tp, magic, filling) -> namespace(ok, position_id, detail)
"""

BROKERS = ("mt5", "ctrader")


def load(config):
    name = config.get("BROKER", "mt5")
    if name == "mt5":
        from broker_mt5 import MT5Broker
        return MT5Broker()
    if name == "ctrader":
        try:
            from broker_ctrader import CTraderBroker
        except ImportError as error:
            raise SystemExit(f"BROKER=ctrader needs the ctrader-open-api package: pip install -r requirements.txt ({error})")
        return CTraderBroker(config)
    raise SystemExit(f"unknown BROKER {name!r}, use one of: {', '.join(BROKERS)}")
