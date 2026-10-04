"""MetaTrader 5 as the bot's broker: the only file that imports the MetaTrader5 package.

The surface is described in brokers.py. Runs on Windows only, next to a logged-in terminal.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import MetaTrader5 as mt5

from risk import day_start


def filling_for(flags):
    """FILLING for .env from symbol_info.filling_mode bit flags: 1 = FOK allowed, 2 = IOC allowed."""
    if flags & 2:
        return "IOC"
    if flags & 1:
        return "FOK"
    return "RETURN"


def to_candles(rates):
    """MT5 rate rows as plain dicts, the shape every brain and indicator works on."""
    return [
        {
            "time": int(row["time"]), "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]), "spread": int(row["spread"]),
        }
        for row in rates
    ]


class MT5Broker:
    name = "mt5"

    def connect(self):
        return bool(mt5.initialize())

    def connection_hint(self):
        return f"cannot connect to MT5 (is the terminal open and logged in?): {mt5.last_error()}"

    def alive(self):
        return mt5.terminal_info() is not None

    def shutdown(self):
        mt5.shutdown()

    def account(self):
        info = mt5.account_info()
        if info is None:
            return None
        return SimpleNamespace(
            login=info.login, server=info.server, is_demo=info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            balance=info.balance, currency=info.currency,
        )

    def symbols(self):
        return [symbol.name for symbol in (mt5.symbols_get() or ())]

    def select_symbol(self, name):
        return bool(mt5.symbol_select(name, True))

    def symbol(self, name):
        info = mt5.symbol_info(name)
        if info is None:
            return None
        return SimpleNamespace(
            name=name, point=info.point, digits=info.digits,
            stops_level=int(getattr(info, "trade_stops_level", 0) or 0),
            contract_size=float(info.trade_contract_size), min_lot=float(info.volume_min),
            spread_points=int(info.spread), filling=filling_for(info.filling_mode),
        )

    def tick(self, name):
        tick = mt5.symbol_info_tick(name)
        return None if tick is None else SimpleNamespace(bid=tick.bid, ask=tick.ask, time=int(tick.time))

    def candles(self, name, timeframe, count):
        rates = mt5.copy_rates_from_pos(name, getattr(mt5, "TIMEFRAME_" + timeframe), 0, count)
        return None if rates is None else to_candles(rates)

    def open_positions(self, name, magic):
        return [position for position in (mt5.positions_get(symbol=name) or ()) if position.magic == magic]

    def position_result(self, position_id):
        """How a position ended, from the broker's own deals; None while it is open or not yet in history."""
        if mt5.positions_get(ticket=position_id):
            return None
        deals = mt5.history_deals_get(position=position_id) or ()
        exits = [deal for deal in deals if deal.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            return None  # the terminal has not shown the closing deal yet, look again next poll
        last = max(exits, key=lambda deal: deal.time)
        return {
            "exit": last.price,
            "closed_at": int(last.time),
            "profit": round(sum(deal.profit + deal.commission + deal.swap for deal in deals), 2),
            "outcome": {mt5.DEAL_REASON_SL: "sl", mt5.DEAL_REASON_TP: "tp"}.get(last.reason, "closed"),
        }

    def realized_since(self, server_time):
        """Closed result of every trade on the account since server_time; manual trades count too.

        Deal and tick times share the broker's clock, the PC clock does not, so query a
        wide window and filter on deal.time ourselves.
        """
        now = datetime.now()
        deals = mt5.history_deals_get(now - timedelta(days=3), now + timedelta(days=3)) or ()
        return sum(
            deal.profit + deal.commission + deal.swap
            for deal in deals
            if deal.time >= server_time and deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL)
        )

    def market_order(self, side, name, lot, price, sl, tp, magic, filling):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": name,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": magic,
            "comment": "tradebot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": getattr(mt5, "ORDER_FILLING_" + filling),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            detail = f"retcode={result.retcode} {result.comment}" if result else str(mt5.last_error())
            return SimpleNamespace(ok=False, position_id=None, detail=detail)
        # The position carries the deal's position_id; fall back to the order ticket.
        deals = mt5.history_deals_get(ticket=result.deal) or ()
        return SimpleNamespace(ok=True, position_id=deals[0].position_id if deals else result.order, detail="")
