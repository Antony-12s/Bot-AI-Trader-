"""The MetaTrader5 adapter against a faked MetaTrader5 module (tests_support/ or the real package)."""
import unittest
from types import SimpleNamespace
from unittest import mock

import broker_mt5

mt5 = broker_mt5.mt5
DAY = 86400
NOW = 10 * DAY + 3600


class ShapeTest(unittest.TestCase):
    def test_filling_prefers_ioc_then_fok_then_return(self):
        self.assertEqual([broker_mt5.filling_for(flags) for flags in (3, 2, 1, 0)], ["IOC", "IOC", "FOK", "RETURN"])

    def test_to_candles_makes_plain_dicts(self):
        rows = [{"time": 1.0, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "spread": 30.0}]
        self.assertEqual(broker_mt5.to_candles(rows), [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "spread": 30}])

    def test_account_and_symbol_are_normalised(self):
        broker = broker_mt5.MT5Broker()
        info = SimpleNamespace(login=5, server="XM-Demo", trade_mode=mt5.ACCOUNT_TRADE_MODE_DEMO, balance=99.5, currency="USD")
        with mock.patch.object(mt5, "account_info", return_value=info):
            account = broker.account()
        self.assertEqual((account.login, account.server, account.is_demo, account.balance), (5, "XM-Demo", True, 99.5))
        with mock.patch.object(mt5, "account_info", return_value=None):
            self.assertIsNone(broker.account())
        gold = SimpleNamespace(point=0.01, digits=2, trade_stops_level=0, trade_contract_size=100.0, volume_min=0.01, spread=28, filling_mode=2)
        with mock.patch.object(mt5, "symbol_info", return_value=gold):
            symbol = broker.symbol("GOLD")
        self.assertEqual((symbol.name, symbol.contract_size, symbol.min_lot, symbol.spread_points, symbol.filling), ("GOLD", 100.0, 0.01, 28, "IOC"))
        with mock.patch.object(mt5, "symbol_info", return_value=None):
            self.assertIsNone(broker.symbol("NOPE"))


class HistoryTest(unittest.TestCase):
    def test_realized_since_skips_deposits_and_yesterday(self):
        buy, balance = mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_BALANCE
        deals = [
            SimpleNamespace(time=NOW - 60, type=buy, profit=-12.0, commission=-0.5, swap=0.0),
            SimpleNamespace(time=NOW - 60, type=balance, profit=1000.0, commission=0.0, swap=0.0),
            SimpleNamespace(time=NOW - DAY, type=buy, profit=-99.0, commission=0.0, swap=0.0),
        ]
        with mock.patch.object(mt5, "history_deals_get", return_value=deals):
            self.assertEqual(broker_mt5.MT5Broker().realized_since(10 * DAY), -12.5)

    def test_position_result_comes_from_the_closing_deal(self):
        deals = [
            SimpleNamespace(entry=mt5.DEAL_ENTRY_IN, time=NOW - 1800, price=2650.5, profit=0.0, commission=-0.1, swap=0.0, reason=mt5.DEAL_REASON_EXPERT),
            SimpleNamespace(entry=mt5.DEAL_ENTRY_OUT, time=NOW - 100, price=2645.5, profit=-5.0, commission=-0.1, swap=0.0, reason=mt5.DEAL_REASON_SL),
        ]

        def deals_for(*args, **kwargs):
            return deals if kwargs.get("position") == 42 else ()

        broker = broker_mt5.MT5Broker()
        with mock.patch.object(mt5, "positions_get", return_value=()), mock.patch.object(mt5, "history_deals_get", side_effect=deals_for):
            self.assertEqual(broker.position_result(42), {"exit": 2645.5, "closed_at": NOW - 100, "profit": -5.2, "outcome": "sl"})
            self.assertIsNone(broker.position_result(43))  # no closing deal visible yet
        with mock.patch.object(mt5, "positions_get", return_value=(SimpleNamespace(ticket=42),)):
            self.assertIsNone(broker.position_result(42))  # still open


class OrderTest(unittest.TestCase):
    def send(self, retcode, deals=()):
        result = SimpleNamespace(retcode=retcode, comment="fake", order=501, deal=901)
        with mock.patch.object(mt5, "order_send", return_value=result) as order_send, \
                mock.patch.object(mt5, "history_deals_get", return_value=deals):
            outcome = broker_mt5.MT5Broker().market_order("buy", "GOLD", 0.01, 2650.5, 2645.5, 2660.5, 7, "IOC")
        return outcome, order_send.call_args.args[0]

    def test_accepted_order_builds_the_mt5_request_and_returns_the_position_id(self):
        outcome, request = self.send(mt5.TRADE_RETCODE_DONE, deals=[SimpleNamespace(position_id=777)])
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.position_id, 777)
        self.assertEqual((request["type"], request["type_filling"], request["magic"], request["sl"], request["tp"]),
                         (mt5.ORDER_TYPE_BUY, mt5.ORDER_FILLING_IOC, 7, 2645.5, 2660.5))
        outcome, _ = self.send(mt5.TRADE_RETCODE_DONE, deals=())
        self.assertEqual(outcome.position_id, 501)  # no deal visible yet: the order ticket

    def test_rejected_order_reports_the_retcode(self):
        outcome, _ = self.send(10027)
        self.assertFalse(outcome.ok)
        self.assertIn("10027", outcome.detail)


if __name__ == "__main__":
    unittest.main()
