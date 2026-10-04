"""The cTrader adapter against a fake gateway connection: no network, real protobuf messages."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import broker_ctrader
import ctrader_auth
from broker_ctrader import ApiError, CTraderBroker, api, common, model
from config import save_env_values

DAY = 86400
NOW = 10 * DAY + 3600  # seconds, UTC
GOLD_ID, EUR_ID, ACCOUNT = 41, 1, 9_000_001
CONFIG = {
    "BROKER": "ctrader", "CTRADER_ENV": "demo", "CTRADER_CLIENT_ID": "app", "CTRADER_CLIENT_SECRET": "secret",
    "CTRADER_ACCESS_TOKEN": "access", "CTRADER_REFRESH_TOKEN": "refresh", "CTRADER_ACCOUNT_ID": str(ACCOUNT),
}


def side(name):
    return model.ProtoOATradeSide.Value(name)


def gold_detail():
    return model.ProtoOASymbol(symbolId=GOLD_ID, digits=2, pipPosition=1, lotSize=10_000, minVolume=100, stepVolume=100, slDistance=5)


def spot(symbol_id, bid=None, ask=None, at=NOW):
    event = api.ProtoOASpotEvent(ctidTraderAccountId=ACCOUNT, symbolId=symbol_id, timestamp=at * 1000)
    if bid is not None:
        event.bid = round(bid * broker_ctrader.PRICE_SCALE)
    if ask is not None:
        event.ask = round(ask * broker_ctrader.PRICE_SCALE)
    return event


def trendbar(minute, low, open_delta, high_delta, close_delta, period="M15"):
    return model.ProtoOATrendbar(
        utcTimestampInMinutes=minute, low=round(low * broker_ctrader.PRICE_SCALE), deltaOpen=open_delta, deltaHigh=high_delta,
        deltaClose=close_delta, period=model.ProtoOATrendbarPeriod.Value(period), volume=1,
    )


def closing_deal(position_id, price, at, gross, swap=0, commission=0, deal_id=1):
    deal = model.ProtoOADeal(
        dealId=deal_id, orderId=deal_id, positionId=position_id, symbolId=GOLD_ID, volume=100, filledVolume=100, executionTimestamp=at * 1000,
        executionPrice=price, tradeSide=side("SELL"), dealStatus=model.ProtoOADealStatus.Value("FILLED"), moneyDigits=2,
    )
    deal.closePositionDetail.CopyFrom(model.ProtoOAClosePositionDetail(entryPrice=2650.5, grossProfit=gross, swap=swap, commission=commission, moneyDigits=2, closedVolume=100))
    return deal


def opening_deal(position_id, price, at, commission=0, deal_id=2):
    return model.ProtoOADeal(
        dealId=deal_id, orderId=deal_id, positionId=position_id, symbolId=GOLD_ID, volume=100, filledVolume=100, executionTimestamp=at * 1000,
        executionPrice=price, tradeSide=side("BUY"), dealStatus=model.ProtoOADealStatus.Value("FILLED"), commission=commission, moneyDigits=2,
    )


class FakeClient:
    """Answers each request type from canned replies (or raises), records what was sent, and holds quotes like the real one."""

    def __init__(self, host):
        self.host = host
        self.connected = False
        self.failure = ""
        self.quotes = {}
        self.subscriptions = set()
        self.requests = []
        self.sent = []
        self.replies = {}  # message class name -> reply message, exception, or a list consumed in order
        self.events = []  # what wait() hands out, in order

    def open(self):
        self.connected = True

    def close(self):
        self.connected = False

    def send(self, message, client_msg_id=None):
        self.sent.append((message, client_msg_id))

    def request(self, message, timeout=None):
        self.requests.append(message)
        reply = self.replies.get(type(message).__name__)
        if isinstance(reply, list):
            reply = reply.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            raise ApiError("NO_FAKE_REPLY", type(message).__name__)
        return reply

    def wait(self, match, timeout=None):
        for index, entry in enumerate(self.events):
            if match(*entry):
                return self.events.pop(index)[0]
        return None


def gateway(**overrides):
    """A fake client that gets a demo gold account through connect()."""
    client = FakeClient("demo.ctraderapi.com")
    accounts = api.ProtoOAGetAccountListByAccessTokenRes(accessToken="access")
    accounts.ctidTraderAccount.add(ctidTraderAccountId=ACCOUNT, isLive=False, traderLogin=555)
    accounts.ctidTraderAccount.add(ctidTraderAccountId=ACCOUNT + 1, isLive=True, traderLogin=556)
    symbols = api.ProtoOASymbolsListRes(ctidTraderAccountId=ACCOUNT)
    symbols.symbol.add(symbolId=EUR_ID, symbolName="EURUSD")
    symbols.symbol.add(symbolId=GOLD_ID, symbolName="XAUUSD")
    assets = api.ProtoOAAssetListRes(ctidTraderAccountId=ACCOUNT)
    assets.asset.add(assetId=4, name="USD")
    details = api.ProtoOASymbolByIdRes(ctidTraderAccountId=ACCOUNT)
    details.symbol.add().CopyFrom(gold_detail())
    client.replies = {
        "ProtoOAApplicationAuthReq": api.ProtoOAApplicationAuthRes(),
        "ProtoOAGetAccountListByAccessTokenReq": accounts,
        "ProtoOAAccountAuthReq": api.ProtoOAAccountAuthRes(ctidTraderAccountId=ACCOUNT),
        "ProtoOASymbolsListReq": symbols,
        "ProtoOAAssetListReq": assets,
        "ProtoOASymbolByIdReq": details,
        "ProtoOASubscribeSpotsReq": api.ProtoOASubscribeSpotsRes(ctidTraderAccountId=ACCOUNT),
        "ProtoOATraderReq": api.ProtoOATraderRes(ctidTraderAccountId=ACCOUNT, trader=model.ProtoOATrader(
            ctidTraderAccountId=ACCOUNT, balance=123_456, moneyDigits=2, traderLogin=555, brokerName="Demo Broker", depositAssetId=4)),
        "ProtoOAReconcileReq": api.ProtoOAReconcileRes(ctidTraderAccountId=ACCOUNT),
        **overrides,
    }
    return client


def connected(client=None, config=None, saved=None):
    client = client or gateway()
    broker = CTraderBroker(dict(config or CONFIG), client_factory=lambda host: client, save_tokens=(saved if saved is not None else {}).update)
    broker_ctrader.FIRST_QUOTE_WAIT = 0  # tests never wait for a spot event
    assert broker.connect(), broker.connection_hint()
    return broker, client


class WireTest(unittest.TestCase):
    def test_frames_round_trip_and_partial_reads_wait(self):
        auth = api.ProtoOAApplicationAuthReq(clientId="a", clientSecret="b")
        heartbeat = common.ProtoHeartbeatEvent()
        wire = broker_ctrader.encode(auth, "r7") + broker_ctrader.encode(heartbeat)
        self.assertEqual(wire[:4], bytes([0, 0, 0, len(wire) - 4 - len(broker_ctrader.encode(heartbeat))]))
        frames, rest = broker_ctrader.decode_frames(wire[:-3])
        self.assertEqual(len(frames), 1)
        self.assertEqual((type(frames[0][0]), frames[0][0].clientId, frames[0][1]), (api.ProtoOAApplicationAuthReq, "a", "r7"))
        frames, rest = broker_ctrader.decode_frames(rest + wire[-3:])
        self.assertEqual((type(frames[0][0]), frames[0][1], rest), (common.ProtoHeartbeatEvent, None, b""))

    def test_payload_types_match_the_sdk(self):
        self.assertIs(broker_ctrader.MESSAGES[2131], api.ProtoOASpotEvent)
        self.assertIs(broker_ctrader.MESSAGES[51], common.ProtoHeartbeatEvent)
        self.assertNotIn(0, broker_ctrader.MESSAGES)

    def test_dispatch_keeps_quotes_and_queues_the_rest(self):
        client = broker_ctrader.Client("demo.ctraderapi.com")
        client.connected = True
        client.dispatch(spot(GOLD_ID, bid=2650.2, ask=2650.5))
        client.dispatch(spot(GOLD_ID, ask=2650.6, at=NOW + 1))  # bid unchanged
        self.assertEqual(client.quotes[GOLD_ID], {"bid": 2650.2, "ask": 2650.6, "time": NOW + 1})
        client.dispatch(common.ProtoHeartbeatEvent())
        client.dispatch(api.ProtoOAAccountAuthRes(ctidTraderAccountId=ACCOUNT), "r1")
        self.assertEqual(len(client.messages), 1)
        reply = client.wait(lambda message, msg_id: msg_id == "r1", timeout=0.01)
        self.assertEqual(reply.ctidTraderAccountId, ACCOUNT)
        self.assertIsNone(client.wait(lambda message, msg_id: msg_id == "r1", timeout=0.01))
        client.dispatch(api.ProtoOAClientDisconnectEvent(reason="bye"))
        self.assertFalse(client.connected)
        self.assertIn("bye", client.failure)
        with self.assertRaises(ApiError):
            client.send(common.ProtoHeartbeatEvent())

    def test_error_replies_raise(self):
        with self.assertRaises(ApiError) as caught:
            broker_ctrader.raise_if_error(api.ProtoOAErrorRes(errorCode="NOT_ENOUGH_MONEY", description="nope"))
        self.assertEqual((caught.exception.code, str(caught.exception)), ("NOT_ENOUGH_MONEY", "NOT_ENOUGH_MONEY: nope"))
        broker_ctrader.raise_if_error(api.ProtoOAAccountAuthRes())  # not an error


class ConversionTest(unittest.TestCase):
    def test_trendbar_to_candle(self):
        candle = broker_ctrader.trendbar_candle(trendbar(100, 2650.00, 10_000, 50_000, 30_000), 25)
        self.assertEqual(candle, {"time": 6000, "open": 2650.1, "high": 2650.5, "low": 2650.0, "close": 2650.3, "spread": 25})

    def test_forming_candle_is_added_only_when_missing(self):
        closed = [{"time": NOW - NOW % 900 - 900, "open": 1, "high": 1, "low": 1, "close": 1, "spread": 2}]
        quote = {"bid": 2650.2, "ask": 2650.5, "time": NOW}
        out = broker_ctrader.with_forming_candle(closed, NOW, 900, quote, 30)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1], {"time": NOW - NOW % 900, "open": 2650.2, "high": 2650.2, "low": 2650.2, "close": 2650.2, "spread": 30})
        self.assertEqual(broker_ctrader.with_forming_candle(out, NOW, 900, quote, 30), out)  # already there
        self.assertEqual(broker_ctrader.with_forming_candle(closed, NOW, 900, None, 30), closed)  # no quote, nothing invented

    def test_relative_distance_and_outcome(self):
        self.assertEqual(broker_ctrader.relative_distance(2650.5, 2645.5), 500_000)
        self.assertEqual(broker_ctrader.relative_distance(2650.5, 2660.5), 1_000_000)
        trade = {"sl": 2645.5, "tp": 2660.5}
        self.assertEqual(broker_ctrader.outcome_for(2645.62, trade), "sl")
        self.assertEqual(broker_ctrader.outcome_for(2660.1, trade), "tp")
        self.assertEqual(broker_ctrader.outcome_for(2652.0, trade), "closed")
        self.assertEqual(broker_ctrader.outcome_for(2645.5, None), "closed")

    def test_deal_result_sums_signed_cents(self):
        self.assertEqual(broker_ctrader.deal_result(closing_deal(1, 2645.5, NOW, gross=-500, swap=-3, commission=-7)), -5.1)
        self.assertEqual(broker_ctrader.deal_result(opening_deal(1, 2650.5, NOW, commission=-7)), -0.07)


class ConnectTest(unittest.TestCase):
    def test_connect_authenticates_app_then_account_and_loads_symbols(self):
        broker, client = connected()
        names = [type(request).__name__ for request in client.requests]
        self.assertEqual(names[:3], ["ProtoOAApplicationAuthReq", "ProtoOAGetAccountListByAccessTokenReq", "ProtoOAAccountAuthReq"])
        self.assertEqual(client.requests[2].accessToken, "access")
        self.assertEqual(broker.symbols(), ["EURUSD", "XAUUSD"])
        self.assertTrue(broker.alive())
        self.assertFalse(broker.account().is_demo is False)
        account = broker.account()
        self.assertEqual((account.login, account.server, account.is_demo, account.balance, account.currency), (555, "Demo Broker cTrader demo", True, 1234.56, "USD"))
        broker.shutdown()
        self.assertFalse(broker.alive())

    def test_missing_token_gives_a_hint_without_connecting(self):
        config = dict(CONFIG, CTRADER_ACCESS_TOKEN="")
        client = gateway()
        broker = CTraderBroker(config, client_factory=lambda host: client)
        self.assertFalse(broker.connect())
        self.assertIn("ctrader_auth.py", broker.connection_hint())
        self.assertFalse(client.connected)

    def test_expired_token_is_refreshed_and_saved(self):
        saved = {}
        client = gateway()
        client.replies["ProtoOAAccountAuthReq"] = [ApiError("OA_AUTH_TOKEN_EXPIRED", "expired"), api.ProtoOAAccountAuthRes(ctidTraderAccountId=ACCOUNT)]
        client.replies["ProtoOARefreshTokenReq"] = api.ProtoOARefreshTokenRes(accessToken="access2", refreshToken="refresh2", expiresIn=100)
        broker, client = connected(client, saved=saved)
        self.assertEqual(saved, {"CTRADER_ACCESS_TOKEN": "access2", "CTRADER_REFRESH_TOKEN": "refresh2"})
        self.assertEqual([request.accessToken for request in client.requests if isinstance(request, api.ProtoOAAccountAuthReq)], ["access", "access2"])

    def test_other_auth_errors_fail_the_connect(self):
        client = gateway()
        client.replies["ProtoOAAccountAuthReq"] = ApiError("CH_CLIENT_AUTH_FAILURE", "bad app")
        broker = CTraderBroker(dict(CONFIG), client_factory=lambda host: client)
        self.assertFalse(broker.connect())
        self.assertIn("CH_CLIENT_AUTH_FAILURE", broker.connection_hint())
        self.assertFalse(client.connected)

    def test_account_id_is_picked_from_the_token_when_env_has_none(self):
        saved = {}
        broker, client = connected(config=dict(CONFIG, CTRADER_ACCOUNT_ID=""), saved=saved)
        self.assertEqual((broker.account_id, saved), (ACCOUNT, {"CTRADER_ACCOUNT_ID": str(ACCOUNT)}))
        live, hosts = gateway(), []
        broker = CTraderBroker(dict(CONFIG, CTRADER_ACCOUNT_ID="", CTRADER_ENV="live"), client_factory=lambda host: hosts.append(host) or live, save_tokens=saved.update)
        self.assertTrue(broker.connect())
        self.assertEqual((broker.account_id, broker.is_live, hosts), (ACCOUNT + 1, True, ["live.ctraderapi.com"]))

    def test_reconnect_resubscribes_the_symbols_in_use(self):
        broker, client = connected()
        self.assertTrue(broker.select_symbol("XAUUSD"))
        self.assertEqual([request.symbolId for request in client.requests if isinstance(request, api.ProtoOASubscribeSpotsReq)], [[GOLD_ID]])
        client.dispatch = None
        fresh = gateway()
        broker.client_factory = lambda host: fresh
        self.assertTrue(broker.connect())
        self.assertEqual([request.symbolId for request in fresh.requests if isinstance(request, api.ProtoOASubscribeSpotsReq)], [[GOLD_ID]])
        self.assertFalse(broker.select_symbol("NOPE"))


class MarketDataTest(unittest.TestCase):
    def setUp(self):
        self.broker, self.client = connected()
        self.client.quotes[GOLD_ID] = {"bid": 2650.20, "ask": 2650.50, "time": NOW}
        self.client.subscriptions.add(GOLD_ID)

    def test_symbol_is_normalised(self):
        symbol = self.broker.symbol("XAUUSD")
        self.assertEqual((symbol.name, symbol.point, symbol.digits, symbol.stops_level), ("XAUUSD", 0.01, 2, 5))
        self.assertEqual((symbol.contract_size, symbol.min_lot, symbol.spread_points, symbol.filling), (100.0, 0.01, 30, "IOC"))
        self.assertIsNone(self.broker.symbol("NOPE"))
        tick = self.broker.tick("XAUUSD")
        self.assertEqual((tick.bid, tick.ask, tick.time), (2650.2, 2650.5, NOW))
        self.assertIsNone(self.broker.tick("NOPE"))

    def test_tick_subscribes_on_first_use(self):
        self.client.quotes[EUR_ID] = {"bid": 1.1, "ask": 1.1002, "time": NOW}
        self.assertEqual(self.broker.tick("EURUSD").ask, 1.1002)
        self.assertEqual([request.symbolId for request in self.client.requests if isinstance(request, api.ProtoOASubscribeSpotsReq)], [[EUR_ID]])
        self.broker.tick("EURUSD")
        self.assertEqual(len([request for request in self.client.requests if isinstance(request, api.ProtoOASubscribeSpotsReq)]), 1)

    def test_candles_come_oldest_first_with_a_forming_bar(self):
        reply = api.ProtoOAGetTrendbarsRes(ctidTraderAccountId=ACCOUNT, symbolId=GOLD_ID)
        last_closed = (NOW - NOW % 900 - 900) // 60
        reply.trendbar.add().CopyFrom(trendbar(last_closed, 2649.0, 0, 100_000, 50_000))
        reply.trendbar.add().CopyFrom(trendbar(last_closed - 15, 2648.0, 0, 100_000, 100_000))
        self.client.replies["ProtoOAGetTrendbarsReq"] = reply
        candles = self.broker.candles("XAUUSD", "M15", 3)
        self.assertEqual([candle["time"] for candle in candles], [(last_closed - 15) * 60, last_closed * 60, NOW - NOW % 900])
        self.assertEqual(candles[0]["close"], 2649.0)
        self.assertEqual(candles[-1]["close"], 2650.2)
        self.assertEqual({candle["spread"] for candle in candles}, {30})
        request = next(r for r in self.client.requests if isinstance(r, api.ProtoOAGetTrendbarsReq))
        self.assertEqual((request.symbolId, request.count, request.period), (GOLD_ID, 3, model.ProtoOATrendbarPeriod.Value("M15")))
        self.assertLessEqual(request.toTimestamp - request.fromTimestamp, broker_ctrader.TRENDBAR_WINDOW_MS["M15"])
        self.assertGreater(request.toTimestamp, NOW * 1000)
        self.assertIsNone(self.broker.candles("XAUUSD", "M6", 3))  # cTrader has no M6
        self.client.replies["ProtoOAGetTrendbarsReq"] = ApiError("REQUEST_FREQUENCY_EXCEEDED")
        self.assertIsNone(self.broker.candles("XAUUSD", "M15", 3))


class PositionTest(unittest.TestCase):
    def setUp(self):
        self.broker, self.client = connected()
        self.client.quotes[GOLD_ID] = {"bid": 2650.20, "ask": 2650.50, "time": NOW}
        self.client.subscriptions.add(GOLD_ID)

    def reconcile(self, *positions):
        reply = api.ProtoOAReconcileRes(ctidTraderAccountId=ACCOUNT)
        for position_id, label, symbol_id in positions:
            position = reply.position.add(positionId=position_id, price=2650.5, stopLoss=2645.5, takeProfit=2660.5)
            position.tradeData.CopyFrom(model.ProtoOATradeData(symbolId=symbol_id, volume=100, tradeSide=side("BUY"), label=label, openTimestamp=NOW * 1000))
        self.client.replies["ProtoOAReconcileReq"] = reply

    def test_open_positions_filters_by_symbol_and_label(self):
        self.reconcile((1, "7", GOLD_ID), (2, "8", GOLD_ID), (3, "7", EUR_ID))
        mine = self.broker.open_positions("XAUUSD", 7)
        self.assertEqual([(position.position_id, position.side, position.sl) for position in mine], [(1, "buy", 2645.5)])
        self.client.replies["ProtoOAReconcileReq"] = ApiError("TIMEOUT")
        self.assertEqual(self.broker.open_positions("XAUUSD", 7), [])

    def test_position_result_waits_for_the_closing_deal(self):
        trade = {"position_id": 42, "sl": 2645.5, "tp": 2660.5, "opened_at": NOW - 7200}
        self.reconcile((42, "7", GOLD_ID))
        self.assertIsNone(self.broker.position_result(42, trade))
        self.reconcile()
        self.client.replies["ProtoOADealListByPositionIdReq"] = api.ProtoOADealListByPositionIdRes(ctidTraderAccountId=ACCOUNT)
        self.assertIsNone(self.broker.position_result(42, trade))  # closed, but the deal is not listed yet
        deals = api.ProtoOADealListByPositionIdRes(ctidTraderAccountId=ACCOUNT)
        deals.deal.add().CopyFrom(opening_deal(42, 2650.5, NOW - 7200, commission=-7))
        deals.deal.add().CopyFrom(closing_deal(42, 2645.52, NOW - 100, gross=-498, swap=-3, commission=-7))
        self.client.replies["ProtoOADealListByPositionIdReq"] = deals
        self.assertEqual(self.broker.position_result(42, trade), {"exit": 2645.52, "closed_at": NOW - 100, "profit": -5.15, "outcome": "sl"})
        request = next(r for r in self.client.requests if isinstance(r, api.ProtoOADealListByPositionIdReq))
        self.assertEqual(request.positionId, 42)
        self.assertLessEqual(request.fromTimestamp, (NOW - 7200) * 1000)
        self.assertLessEqual(request.toTimestamp - request.fromTimestamp, 7 * DAY * 1000)

    def test_realized_since_sums_every_filled_deal_and_pages(self):
        first = api.ProtoOADealListRes(ctidTraderAccountId=ACCOUNT, hasMore=True)
        first.deal.add().CopyFrom(opening_deal(1, 2650.5, NOW - 300, commission=-7, deal_id=1))
        first.deal.add().CopyFrom(closing_deal(1, 2660.5, NOW - 200, gross=1000, commission=-7, deal_id=2))
        second = api.ProtoOADealListRes(ctidTraderAccountId=ACCOUNT, hasMore=False)
        rejected = closing_deal(2, 2660.5, NOW - 100, gross=5000, deal_id=3)
        rejected.dealStatus = model.ProtoOADealStatus.Value("REJECTED")
        second.deal.add().CopyFrom(rejected)
        second.deal.add().CopyFrom(closing_deal(3, 2640.5, NOW - 50, gross=-250, deal_id=4))
        self.client.replies["ProtoOADealListReq"] = [first, second]
        self.assertEqual(self.broker.realized_since(NOW - DAY), 7.36)
        requests = [r for r in self.client.requests if isinstance(r, api.ProtoOADealListReq)]
        self.assertEqual(requests[0].fromTimestamp, (NOW - DAY) * 1000)
        self.assertEqual(requests[1].fromTimestamp, (NOW - 200) * 1000 + 1)


class OrderTest(unittest.TestCase):
    def setUp(self):
        self.broker, self.client = connected()
        self.client.quotes[GOLD_ID] = {"bid": 2650.20, "ask": 2650.50, "time": NOW}
        self.client.subscriptions.add(GOLD_ID)

    def execution(self, kind, client_order_id, position_id=0, error=""):
        event = api.ProtoOAExecutionEvent(ctidTraderAccountId=ACCOUNT, executionType=model.ProtoOAExecutionType.Value(kind), errorCode=error)
        event.order.clientOrderId = client_order_id
        if position_id:
            event.position.positionId = position_id
        return event

    def sent_order(self):
        return next(message for message, _ in self.client.sent if isinstance(message, api.ProtoOANewOrderReq))

    def test_market_buy_carries_relative_stops_and_returns_the_position(self):
        self.client.events = []
        original_wait = self.client.wait

        def wait(match, timeout=None):  # the gateway answers after the order went out
            order = self.sent_order()
            if not self.client.events:
                self.client.events = [(self.execution("ORDER_ACCEPTED", order.clientOrderId), f"o{order.clientOrderId}"), (self.execution("ORDER_FILLED", order.clientOrderId, position_id=4242), None)]
            return original_wait(match, timeout)

        self.client.wait = wait
        result = self.broker.market_order("buy", "XAUUSD", 0.02, 2650.5, 2645.5, 2660.5, 7, "IOC")
        self.assertEqual((result.ok, result.position_id, result.detail), (True, 4242, ""))
        order = self.sent_order()
        self.assertEqual((order.symbolId, order.volume, order.label, order.comment), (GOLD_ID, 200, "7", "tradebot"))
        self.assertEqual((order.orderType, order.tradeSide), (model.ProtoOAOrderType.Value("MARKET"), side("BUY")))
        self.assertEqual((order.relativeStopLoss, order.relativeTakeProfit), (500_000, 1_000_000))
        self.assertFalse(order.HasField("stopLoss"))

    def test_rejected_order_fails_with_the_reason(self):
        self.client.wait = lambda match, timeout=None: self.execution("ORDER_REJECTED", self.sent_order().clientOrderId, error="NOT_ENOUGH_MONEY")
        result = self.broker.market_order("sell", "XAUUSD", 0.01, 2650.2, 2655.2, 2640.2, 7, "IOC")
        self.assertEqual((result.ok, result.position_id, result.detail), (False, None, "ORDER_REJECTED NOT_ENOUGH_MONEY"))

    def test_order_error_event_and_silence_fail(self):
        self.client.wait = lambda match, timeout=None: api.ProtoOAOrderErrorEvent(ctidTraderAccountId=ACCOUNT, errorCode="TRADING_BAD_STOPS", description="too close")
        result = self.broker.market_order("sell", "XAUUSD", 0.01, 2650.2, 2655.2, 2640.2, 7, "IOC")
        self.assertEqual((result.ok, result.detail), (False, "TRADING_BAD_STOPS: too close"))
        broker_ctrader.ORDER_TIMEOUT = 0.01
        try:
            self.client.wait = lambda match, timeout=None: None
            result = self.broker.market_order("buy", "XAUUSD", 0.01, 2650.5, 2645.5, 2660.5, 7, "IOC")
        finally:
            broker_ctrader.ORDER_TIMEOUT = 20
        self.assertFalse(result.ok)
        self.assertIn("no fill confirmation", result.detail)
        self.assertEqual(self.broker.market_order("buy", "NOPE", 0.01, 1, 0, 0, 7, "IOC").detail, "unknown symbol NOPE")


class AuthHelperTest(unittest.TestCase):
    def test_urls_and_code_parsing(self):
        url = ctrader_auth.authorize_url("app123")
        self.assertTrue(url.startswith("https://openapi.ctrader.com/apps/auth?"))
        self.assertIn("client_id=app123", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A8765%2Fcallback", url)
        self.assertIn("scope=trading", url)
        self.assertEqual(ctrader_auth.code_from_url("http://localhost:8765/callback?code=abc_123&state=x"), "abc_123")
        self.assertEqual(ctrader_auth.code_from_url("  abc_123 "), "abc_123")
        self.assertEqual(ctrader_auth.code_from_url("http://localhost:8765/callback?error=denied"), "")
        token_url = ctrader_auth.token_request("app", "sec", code="abc")
        self.assertIn("grant_type=authorization_code", token_url)
        self.assertIn("code=abc", token_url)
        self.assertIn("grant_type=refresh_token", ctrader_auth.token_request("app", "sec", refresh_token="r"))

    def test_token_reply_parsing(self):
        tokens = ctrader_auth.parse_tokens('{"accessToken": "a", "refreshToken": "r", "expiresIn": 2628000, "tokenType": "bearer", "errorCode": null}')
        self.assertEqual(tokens, {"CTRADER_ACCESS_TOKEN": "a", "CTRADER_REFRESH_TOKEN": "r"})
        self.assertEqual(ctrader_auth.parse_tokens({"access_token": "a"}), {"CTRADER_ACCESS_TOKEN": "a", "CTRADER_REFRESH_TOKEN": ""})
        with self.assertRaises(SystemExit):
            ctrader_auth.parse_tokens('{"errorCode": "invalid_grant", "description": "bad code"}')

    def test_choose_account(self):
        accounts = [(1, False, 100), (2, True, 200)]
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(ctrader_auth.choose_account(accounts, ask=lambda prompt: ""), (1, False, 100))
            self.assertEqual(ctrader_auth.choose_account(accounts, ask=lambda prompt: "2"), (2, True, 200))
            with self.assertRaises(SystemExit):
                ctrader_auth.choose_account([])
        self.assertIn("account 200  (LIVE)", shown.getvalue())

    def test_save_env_values_updates_or_appends_and_starts_from_the_template(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            (Path(folder) / ".env.example").write_text("# comment\nBROKER=mt5\nMODE=dry\nCTRADER_ACCESS_TOKEN=\n", encoding="utf-8")
            save_env_values({"BROKER": "ctrader", "CTRADER_ACCESS_TOKEN": "t", "CTRADER_ACCOUNT_ID": "5"}, env)
            self.assertEqual(env.read_text(encoding="utf-8"), "# comment\nBROKER=ctrader\nMODE=dry\nCTRADER_ACCESS_TOKEN=t\nCTRADER_ACCOUNT_ID=5\n")
            save_env_values({"MODE": "demo"}, env)
            self.assertIn("MODE=demo\n", env.read_text(encoding="utf-8"))
            self.assertIn("CTRADER_ACCOUNT_ID=5\n", env.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
