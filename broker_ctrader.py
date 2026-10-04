"""cTrader Open API as the bot's broker: the only file that talks to demo/live.ctraderapi.com.

The surface is described in brokers.py. Works on any OS and needs no terminal: the bot keeps
one TLS connection to Spotware's gateway, authenticates the app (CTRADER_CLIENT_ID / SECRET)
and then the trading account (CTRADER_ACCESS_TOKEN, obtained once with ctrader_auth.py),
subscribes to spot prices and asks for trendbars, positions and deals when it needs them.

Units the API uses, kept out of the rest of the bot:
    prices in spot events and trendbars  int, 1/100000 of a price unit
    volumes                              int, 1/100 of a unit, so lotSize is in cents too
    money                                int, 1/10**moneyDigits of the account currency
    time                                 ms since the epoch, UTC (trendbars: minutes)

Only the protobuf message classes are taken from the ctrader-open-api package; its Twisted
client is not used, the bot polls and a plain blocking socket plus one reader thread is
all that takes.
"""
import itertools
import socket
import ssl
import struct
import threading
import time
from collections import deque
from types import SimpleNamespace

from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as common
from ctrader_open_api.messages import OpenApiMessages_pb2 as api
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model

from config import TIMEFRAME_SECONDS, save_env_values

HOSTS = {"demo": "demo.ctraderapi.com", "live": "live.ctraderapi.com"}
PORT = 5035
PRICE_SCALE = 100_000
HEARTBEAT_SECONDS = 10  # the gateway drops a connection that stays silent for ~30 s
REQUEST_TIMEOUT = 10
ORDER_TIMEOUT = 20
FIRST_QUOTE_WAIT = 3
EXPIRED_TOKEN_CODES = ("OA_AUTH_TOKEN_EXPIRED", "CH_ACCESS_TOKEN_INVALID")
# Widest from..to window the gateway accepts per trendbar period, ms.
TRENDBAR_WINDOW_MS = {
    **{name: 5 * 7 * 86400 * 1000 for name in ("M1", "M2", "M3", "M4", "M5")},
    **{name: 35 * 7 * 86400 * 1000 for name in ("M10", "M15", "M30", "H1")},
    **{name: 365 * 86400 * 1000 for name in ("H4", "H12", "D1")},
    **{name: 5 * 365 * 86400 * 1000 for name in ("W1", "MN1")},
}
DEAL_WINDOW_MS = 6 * 86400 * 1000  # deal list requests are limited to one week


def _enum(enum, name):
    return enum.Value(name)


ORDER_FILLED = _enum(model.ProtoOAExecutionType, "ORDER_FILLED")
ORDER_PARTIAL_FILL = _enum(model.ProtoOAExecutionType, "ORDER_PARTIAL_FILL")
ORDER_FAILED = tuple(_enum(model.ProtoOAExecutionType, name) for name in ("ORDER_REJECTED", "ORDER_CANCELLED", "ORDER_EXPIRED"))
DEAL_FILLED = tuple(_enum(model.ProtoOADealStatus, name) for name in ("FILLED", "PARTIALLY_FILLED"))
DISTANCE_IN_PERCENT = _enum(model.ProtoOASymbolDistanceType, "SYMBOL_DISTANCE_IN_PERCENTAGE")


class ApiError(Exception):
    """An error reply from the gateway (ProtoErrorRes, ProtoOAErrorRes, ProtoOAOrderErrorEvent) or a timeout."""

    def __init__(self, code, description=""):
        super().__init__(f"{code}: {description}" if description else str(code))
        self.code = code
        self.description = description


# --- wire format ------------------------------------------------------------------

def message_classes():
    """payloadType -> message class, for every message the gateway can send."""
    classes = {}
    for module in (common, api):
        for name in dir(module):
            cls = getattr(module, name)
            if isinstance(cls, type) and hasattr(cls, "DESCRIPTOR") and "payloadType" in cls.DESCRIPTOR.fields_by_name:
                payload_type = cls().payloadType
                if payload_type:
                    classes[payload_type] = cls
    return classes


MESSAGES = message_classes()


def encode(message, client_msg_id=None):
    """One frame: 4-byte big-endian length, then a ProtoMessage wrapping the serialized message."""
    wrapper = common.ProtoMessage(payloadType=message.payloadType, payload=message.SerializeToString())
    if client_msg_id:
        wrapper.clientMsgId = client_msg_id
    body = wrapper.SerializeToString()
    return struct.pack("!I", len(body)) + body


def decode_frames(buffer):
    """Every complete frame at the front of buffer as (message, clientMsgId or None), plus the unread rest."""
    frames = []
    while len(buffer) >= 4:
        length = struct.unpack("!I", buffer[:4])[0]
        if len(buffer) < 4 + length:
            break
        wrapper = common.ProtoMessage()
        wrapper.ParseFromString(buffer[4:4 + length])
        buffer = buffer[4 + length:]
        cls = MESSAGES.get(wrapper.payloadType)
        if cls is None:
            continue  # a message type this adapter does not use
        message = cls()
        message.ParseFromString(wrapper.payload)
        frames.append((message, wrapper.clientMsgId or None))
    return frames, buffer


def raise_if_error(message):
    if isinstance(message, (common.ProtoErrorRes, api.ProtoOAErrorRes, api.ProtoOAOrderErrorEvent)):
        raise ApiError(message.errorCode, message.description)


class Client:
    """One connection to the gateway: sends requests, matches replies by clientMsgId, keeps the latest quotes.

    A reader thread parses everything the gateway sends: spot events update .quotes, every
    other message waits in .messages until request() or wait() claims it. A second thread
    sends heartbeats. Nothing here knows about trading, CTraderBroker does.
    """

    def __init__(self, host, port=PORT, timeout=REQUEST_TIMEOUT):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
        self.connected = False
        self.failure = ""
        self.quotes = {}  # symbolId -> {"bid", "ask", "time"} (time in seconds, UTC)
        self.subscriptions = set()  # symbolIds this connection receives spot events for
        self.messages = deque(maxlen=500)  # (message, clientMsgId) not yet claimed
        self.inbox = threading.Condition()
        self.write_lock = threading.Lock()
        self.ids = itertools.count(1)
        self.threads = []

    def open(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        self.sock.settimeout(1.0)  # recv wakes up regularly so close() can stop the reader
        self.connected, self.failure = True, ""
        self.threads = [threading.Thread(target=self._read_loop, daemon=True), threading.Thread(target=self._heartbeat_loop, daemon=True)]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.connected = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        with self.inbox:
            self.inbox.notify_all()

    def send(self, message, client_msg_id=None):
        if not self.connected:
            raise ApiError("DISCONNECTED", self.failure or "not connected")
        try:
            with self.write_lock:
                self.sock.sendall(encode(message, client_msg_id))
        except OSError as error:
            self._lost(f"send failed: {error}")
            raise ApiError("DISCONNECTED", self.failure)

    def request(self, message, timeout=None):
        """Send message and return the reply that carries the same clientMsgId; errors become ApiError."""
        client_msg_id = f"r{next(self.ids)}"
        self.send(message, client_msg_id)
        reply = self.wait(lambda _, msg_id: msg_id == client_msg_id, timeout)
        if reply is None:
            raise ApiError("TIMEOUT", f"no reply to {type(message).__name__} within {timeout or self.timeout}s ({self.failure or 'still connected'})")
        raise_if_error(reply)
        return reply

    def wait(self, match, timeout=None):
        """The first unclaimed message for which match(message, clientMsgId) is true, or None on timeout/disconnect."""
        deadline = time.monotonic() + (timeout or self.timeout)
        with self.inbox:
            while True:
                for entry in self.messages:
                    if match(*entry):
                        self.messages.remove(entry)
                        return entry[0]
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.connected:
                    return None
                self.inbox.wait(remaining)

    # --- threads ---

    def _read_loop(self):
        buffer = b""
        sock = self.sock
        while self.connected and self.sock is sock:
            try:
                chunk = sock.recv(65536)
            except (socket.timeout, ssl.SSLWantReadError):
                continue
            except OSError as error:
                return self._lost(f"connection error: {error}")
            if not chunk:
                return self._lost("connection closed by the gateway")
            buffer += chunk
            frames, buffer = decode_frames(buffer)
            for message, client_msg_id in frames:
                self.dispatch(message, client_msg_id)

    def _heartbeat_loop(self):
        sock = self.sock
        while self.connected and self.sock is sock:
            time.sleep(HEARTBEAT_SECONDS)
            if self.connected and self.sock is sock:
                try:
                    self.send(common.ProtoHeartbeatEvent())
                except ApiError:
                    return

    def dispatch(self, message, client_msg_id=None):
        """Route one inbound message (public so tests can feed messages without a socket)."""
        if isinstance(message, api.ProtoOASpotEvent):
            return self._quote(message)
        if isinstance(message, common.ProtoHeartbeatEvent):
            return None
        if isinstance(message, api.ProtoOAClientDisconnectEvent):
            return self._lost(f"gateway closed the session: {message.reason}")
        if isinstance(message, api.ProtoOAAccountsTokenInvalidatedEvent):
            return self._lost(f"access token invalidated: {message.reason}")
        with self.inbox:
            self.messages.append((message, client_msg_id))
            self.inbox.notify_all()

    def _quote(self, event):
        quote = self.quotes.setdefault(event.symbolId, {"bid": None, "ask": None, "time": 0})
        if event.bid:  # absent means unchanged, and 0 is never a price
            quote["bid"] = event.bid / PRICE_SCALE
        if event.ask:
            quote["ask"] = event.ask / PRICE_SCALE
        quote["time"] = event.timestamp // 1000 if event.timestamp else int(time.time())

    def _lost(self, reason):
        self.failure = reason
        self.connected = False
        with self.inbox:
            self.inbox.notify_all()


# --- conversions ----------------------------------------------------------------------

def money(value, money_digits):
    return value / 10 ** (money_digits or 2)


def trendbar_candle(bar, spread_points):
    """One ProtoOATrendbar as the candle dict the brains work on."""
    low = bar.low
    return {
        "time": int(bar.utcTimestampInMinutes) * 60,
        "open": (low + bar.deltaOpen) / PRICE_SCALE,
        "high": (low + bar.deltaHigh) / PRICE_SCALE,
        "low": low / PRICE_SCALE,
        "close": (low + bar.deltaClose) / PRICE_SCALE,
        "spread": int(spread_points),
    }


def with_forming_candle(candles, now, seconds, quote, spread_points):
    """Make sure the last candle is the one still forming, as bot.py expects.

    The gateway's history may end with the last closed bar; then the bar in progress is built
    from the latest quote (bid, like trendbars). With no quote at all the list is returned as is.
    """
    start = now - now % seconds
    if candles and candles[-1]["time"] >= start:
        return candles
    if not quote or not quote.get("bid"):
        return candles
    bid = quote["bid"]
    return candles + [{"time": start, "open": bid, "high": bid, "low": bid, "close": bid, "spread": int(spread_points)}]


def relative_distance(price, level):
    """SL/TP distance from the entry as the gateway wants it: whole 1/100000 price units, never negative."""
    return max(0, int(round(abs(price - level) * PRICE_SCALE)))


def outcome_for(exit_price, trade):
    """sl or tp when the exit sits on that level of the journal row (within a fifth of the SL-TP span), else closed.

    cTrader's deals carry no close reason, so the level nearest the fill decides.
    """
    if not trade or not trade.get("sl") or not trade.get("tp"):
        return "closed"
    sl, tp = float(trade["sl"]), float(trade["tp"])
    span = abs(tp - sl)
    distance, label = min((abs(exit_price - sl), "sl"), (abs(exit_price - tp), "tp"))
    return label if span and distance <= 0.2 * span else "closed"


def deal_result(deal):
    """Signed money of one deal: gross profit, swap and commission of a closing deal, commission of an opening one.

    The gateway reports charges as negative numbers, so plain sums work. The first demo trade
    is the place to check that against the balance shown in cTrader.
    """
    if deal.HasField("closePositionDetail"):
        detail = deal.closePositionDetail
        return money(detail.grossProfit + detail.swap + detail.commission, detail.moneyDigits or deal.moneyDigits)
    return money(deal.commission, deal.moneyDigits)


# --- the broker ------------------------------------------------------------------------

class CTraderBroker:
    name = "ctrader"

    def __init__(self, config, client_factory=Client, save_tokens=save_env_values):
        self.config = config
        self.env = config.get("CTRADER_ENV", "demo")
        self.client_factory = client_factory
        self.save_tokens = save_tokens
        self.client = None
        self.hint = "not connected yet"
        self.account_id = int(config.get("CTRADER_ACCOUNT_ID") or 0)
        self.access_token = config.get("CTRADER_ACCESS_TOKEN", "")
        self.refresh_token = config.get("CTRADER_REFRESH_TOKEN", "")
        self.is_live = self.env == "live"
        self.symbol_ids = {}  # name -> symbolId
        self.details = {}  # symbolId -> ProtoOASymbol
        self.assets = {}  # assetId -> name
        self.subscribed = set()

    # --- connection ---

    def connect(self):
        self.shutdown()
        if not (self.config.get("CTRADER_CLIENT_ID") and self.config.get("CTRADER_CLIENT_SECRET")):
            self.hint = "CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET are missing in .env: register an app at https://openapi.ctrader.com, then run  python ctrader_auth.py"
            return False
        if not self.access_token:
            self.hint = "CTRADER_ACCESS_TOKEN is missing in .env: run  python ctrader_auth.py  once to log the bot into your cTrader ID"
            return False
        client = self.client_factory(HOSTS[self.env])
        try:
            client.open()
            client.request(api.ProtoOAApplicationAuthReq(clientId=self.config["CTRADER_CLIENT_ID"], clientSecret=self.config["CTRADER_CLIENT_SECRET"]))
            self._authorize_account(client)
            self.symbol_ids = {symbol.symbolName: symbol.symbolId for symbol in client.request(api.ProtoOASymbolsListReq(ctidTraderAccountId=self.account_id)).symbol}
            self.assets = {asset.assetId: asset.name for asset in client.request(api.ProtoOAAssetListReq(ctidTraderAccountId=self.account_id)).asset}
            for symbol_id in sorted(self.subscribed):  # after a reconnect, prices keep flowing
                self._subscribe(client, symbol_id)
        except (ApiError, OSError) as error:
            self.hint = f"cannot connect to cTrader ({HOSTS[self.env]}): {error}"
            client.close()
            return False
        self.client = client
        self.hint = ""
        return True

    def _authorize_account(self, client):
        accounts = client.request(api.ProtoOAGetAccountListByAccessTokenReq(accessToken=self.access_token)).ctidTraderAccount
        if not self.account_id:
            wanted = [account for account in accounts if bool(account.isLive) == (self.env == "live")]
            if len(wanted) != 1:
                known = ", ".join(f"{account.ctidTraderAccountId} ({'live' if account.isLive else 'demo'})" for account in accounts) or "none"
                raise ApiError("ACCOUNT", f"set CTRADER_ACCOUNT_ID in .env to one of: {known} (python ctrader_auth.py does this)")
            self.account_id = int(wanted[0].ctidTraderAccountId)
            self.save_tokens({"CTRADER_ACCOUNT_ID": str(self.account_id)})
        for account in accounts:
            if int(account.ctidTraderAccountId) == self.account_id:
                self.is_live = bool(account.isLive)
                break
        else:
            raise ApiError("ACCOUNT", f"the access token does not cover account {self.account_id}; run python ctrader_auth.py again")
        try:
            client.request(api.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.access_token))
        except ApiError as error:
            if error.code not in EXPIRED_TOKEN_CODES or not self.refresh_token:
                raise
            fresh = client.request(api.ProtoOARefreshTokenReq(refreshToken=self.refresh_token))
            self.access_token, self.refresh_token = fresh.accessToken, fresh.refreshToken or self.refresh_token
            self.save_tokens({"CTRADER_ACCESS_TOKEN": self.access_token, "CTRADER_REFRESH_TOKEN": self.refresh_token})
            client.request(api.ProtoOAAccountAuthReq(ctidTraderAccountId=self.account_id, accessToken=self.access_token))

    def connection_hint(self):
        return self.hint or "not connected to cTrader"

    def alive(self):
        return self.client is not None and self.client.connected

    def shutdown(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def _request(self, message, timeout=None):
        if self.client is None:
            raise ApiError("DISCONNECTED", "not connected")
        message.ctidTraderAccountId = self.account_id
        return self.client.request(message, timeout)

    def _now(self):
        """Server-ish time in seconds: the latest quote's stamp when there is one, else this clock."""
        stamps = [quote["time"] for quote in self.client.quotes.values() if quote.get("time")] if self.client else []
        return max(stamps) if stamps else int(time.time())

    # --- account and symbols ---

    def account(self):
        try:
            trader = self._request(api.ProtoOATraderReq()).trader
        except ApiError:
            return None
        return SimpleNamespace(
            login=int(trader.traderLogin), server=f"{trader.brokerName} cTrader {self.env}".strip(), is_demo=not self.is_live,
            balance=money(trader.balance, trader.moneyDigits), currency=self.assets.get(trader.depositAssetId, ""),
        )

    def symbols(self):
        return sorted(self.symbol_ids)

    def select_symbol(self, name):
        symbol_id = self.symbol_ids.get(name)
        if symbol_id is None or self.client is None:
            return False
        try:
            self._subscribe(self.client, symbol_id)
        except ApiError:
            return False
        deadline = time.monotonic() + FIRST_QUOTE_WAIT
        while self.tick(name) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return True

    def _subscribe(self, client, symbol_id):
        self.subscribed.add(symbol_id)  # remembered across reconnects
        if symbol_id not in client.subscriptions:
            client.request(api.ProtoOASubscribeSpotsReq(ctidTraderAccountId=self.account_id, symbolId=[symbol_id], subscribeToSpotTimestamp=True))
            client.subscriptions.add(symbol_id)

    def _detail(self, symbol_id):
        if symbol_id not in self.details:
            found = self._request(api.ProtoOASymbolByIdReq(symbolId=[symbol_id])).symbol
            if not found:
                raise ApiError("SYMBOL_NOT_FOUND", str(symbol_id))
            self.details[symbol_id] = found[0]
        return self.details[symbol_id]

    def _quote(self, symbol_id):
        if self.client is None:
            return None
        if symbol_id not in self.client.subscriptions:
            try:
                self._subscribe(self.client, symbol_id)
            except ApiError:
                return None
        quote = self.client.quotes.get(symbol_id)
        return quote if quote and quote["bid"] and quote["ask"] else None

    def symbol(self, name):
        symbol_id = self.symbol_ids.get(name)
        if symbol_id is None:
            return None
        try:
            detail = self._detail(symbol_id)
        except ApiError:
            return None
        point = 10 ** -detail.digits
        quote = self._quote(symbol_id)
        spread_points = round((quote["ask"] - quote["bid"]) / point) if quote else 0
        stops_level = int(detail.slDistance or 0)
        if detail.distanceSetIn == DISTANCE_IN_PERCENT:  # a percentage of the price, turned into points
            stops_level = round(quote["bid"] * detail.slDistance / 100 / point) if quote else 0
        lot_size = detail.lotSize or 100
        return SimpleNamespace(
            name=name, point=point, digits=int(detail.digits), stops_level=stops_level,
            contract_size=lot_size / 100, min_lot=detail.minVolume / lot_size if detail.minVolume else 0.01,
            spread_points=int(spread_points), filling="IOC",  # market orders on cTrader need no filling mode
        )

    def tick(self, name):
        symbol_id = self.symbol_ids.get(name)
        quote = self._quote(symbol_id) if symbol_id is not None else None
        return None if quote is None else SimpleNamespace(bid=quote["bid"], ask=quote["ask"], time=int(quote["time"]))

    # --- market data ---

    def candles(self, name, timeframe, count):
        symbol_id = self.symbol_ids.get(name)
        if symbol_id is None or timeframe not in TRENDBAR_WINDOW_MS:
            return None
        seconds = TIMEFRAME_SECONDS[timeframe]
        now = self._now()
        to_ms = (now + seconds) * 1000
        span_ms = min(TRENDBAR_WINDOW_MS[timeframe], max(count * seconds * 3, 7 * 86400) * 1000)
        try:
            detail = self._detail(symbol_id)
            reply = self._request(api.ProtoOAGetTrendbarsReq(
                fromTimestamp=to_ms - span_ms, toTimestamp=to_ms, period=_enum(model.ProtoOATrendbarPeriod, timeframe),
                symbolId=symbol_id, count=count,
            ))
        except ApiError:
            return None
        quote = self._quote(symbol_id)
        spread_points = round((quote["ask"] - quote["bid"]) * 10 ** detail.digits) if quote else 0
        candles = sorted((trendbar_candle(bar, spread_points) for bar in reply.trendbar), key=lambda candle: candle["time"])
        return with_forming_candle(candles, now, seconds, quote, spread_points)[-count:]

    # --- positions and history ---

    def _positions(self):
        return list(self._request(api.ProtoOAReconcileReq()).position)

    def open_positions(self, name, magic):
        symbol_id = self.symbol_ids.get(name)
        try:
            positions = self._positions()
        except ApiError:
            return []
        return [
            SimpleNamespace(
                position_id=int(position.positionId), side="buy" if position.tradeData.tradeSide == _enum(model.ProtoOATradeSide, "BUY") else "sell",
                volume=int(position.tradeData.volume), price=position.price, sl=position.stopLoss, tp=position.takeProfit,
            )
            for position in positions
            if position.tradeData.symbolId == symbol_id and position.tradeData.label == str(magic)
        ]

    def position_result(self, position_id, trade=None):
        """How a position ended, from its deals; None while it is still open or the closing deal is not listed yet."""
        try:
            if any(int(position.positionId) == int(position_id) for position in self._positions()):
                return None
            now_ms = self._now() * 1000
            opened_ms = int(trade["opened_at"]) * 1000 - 3600 * 1000 if trade and trade.get("opened_at") else now_ms - DEAL_WINDOW_MS
            deals = self._request(api.ProtoOADealListByPositionIdReq(
                positionId=int(position_id), fromTimestamp=max(opened_ms, now_ms - DEAL_WINDOW_MS), toTimestamp=now_ms + 60_000,
            )).deal
        except ApiError:
            return None
        deals = [deal for deal in deals if deal.dealStatus in DEAL_FILLED]
        closing = [deal for deal in deals if deal.HasField("closePositionDetail")]
        if not closing:
            return None
        last = max(closing, key=lambda deal: deal.executionTimestamp)
        return {
            "exit": last.executionPrice,
            "closed_at": int(last.executionTimestamp) // 1000,
            "profit": round(sum(deal_result(deal) for deal in deals), 2),
            "outcome": outcome_for(last.executionPrice, trade),
        }

    def realized_since(self, server_time):
        """Closed result of every deal on the account since server_time (seconds, UTC); manual trades count too."""
        total, from_ms, to_ms = 0.0, int(server_time) * 1000, self._now() * 1000 + 60_000
        for _ in range(20):  # pages of up to maxRows deals
            try:
                reply = self._request(api.ProtoOADealListReq(fromTimestamp=from_ms, toTimestamp=to_ms, maxRows=1000))
            except ApiError:
                break
            deals = [deal for deal in reply.deal if deal.dealStatus in DEAL_FILLED]
            total += sum(deal_result(deal) for deal in deals)
            if not reply.hasMore or not reply.deal:
                break
            from_ms = max(deal.executionTimestamp for deal in reply.deal) + 1
        return round(total, 2)

    # --- orders ---

    def market_order(self, side, name, lot, price, sl, tp, magic, filling):
        symbol_id = self.symbol_ids.get(name)
        if symbol_id is None or self.client is None:
            return SimpleNamespace(ok=False, position_id=None, detail=f"unknown symbol {name}" if symbol_id is None else "not connected")
        try:
            detail = self._detail(symbol_id)
        except ApiError as error:
            return SimpleNamespace(ok=False, position_id=None, detail=str(error))
        client_order_id = f"tb{int(time.time() * 1000)}"
        request = api.ProtoOANewOrderReq(
            ctidTraderAccountId=self.account_id, symbolId=symbol_id, orderType=_enum(model.ProtoOAOrderType, "MARKET"),
            tradeSide=_enum(model.ProtoOATradeSide, "BUY" if side == "buy" else "SELL"),
            volume=int(round(lot * (detail.lotSize or 100))), label=str(magic), clientOrderId=client_order_id, comment="tradebot",
        )
        if sl:
            request.relativeStopLoss = relative_distance(price, sl)
        if tp:
            request.relativeTakeProfit = relative_distance(price, tp)
        client_msg_id = f"o{client_order_id}"

        def mine(message, msg_id):
            if msg_id == client_msg_id:
                return True
            return isinstance(message, api.ProtoOAExecutionEvent) and message.order.clientOrderId == client_order_id

        try:
            self.client.send(request, client_msg_id)
            deadline = time.monotonic() + ORDER_TIMEOUT
            while True:
                event = self.client.wait(mine, max(0.1, deadline - time.monotonic()))
                if event is None:
                    return SimpleNamespace(ok=False, position_id=None, detail=f"no fill confirmation within {ORDER_TIMEOUT}s; check cTrader before sending another order")
                raise_if_error(event)
                if not isinstance(event, api.ProtoOAExecutionEvent):
                    continue
                if event.executionType in (ORDER_FILLED, ORDER_PARTIAL_FILL):
                    position_id = int(event.position.positionId or event.deal.positionId or event.order.positionId)
                    return SimpleNamespace(ok=True, position_id=position_id, detail="")
                if event.executionType in ORDER_FAILED:
                    return SimpleNamespace(ok=False, position_id=None, detail=f"{model.ProtoOAExecutionType.Name(event.executionType)} {event.errorCode}".strip())
                # ORDER_ACCEPTED and the like: the fill comes next
        except ApiError as error:
            return SimpleNamespace(ok=False, position_id=None, detail=str(error))
