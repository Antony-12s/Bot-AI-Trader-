import unittest

import fills

CONFIG = {"LOT": 0.01, "SL_POINTS": 500, "TP_POINTS": 1000}
POINT, DIGITS = 0.01, 2


class OpenPositionTest(unittest.TestCase):
    def test_buy_opens_at_ask_sell_at_bid(self):
        buy = fills.open_position("buy", 2000.00, 30, POINT, DIGITS, CONFIG, opened_at=1, reason="up")
        self.assertEqual((buy["entry"], buy["sl"], buy["tp"]), (2000.30, 1995.30, 2010.30))
        sell = fills.open_position("sell", 2000.00, 30, POINT, DIGITS, CONFIG, opened_at=1)
        self.assertEqual((sell["entry"], sell["sl"], sell["tp"]), (2000.00, 2005.00, 1990.00))
        self.assertEqual((buy["lot"], buy["opened_at"], buy["reason"]), (0.01, 1, "up"))


class ExitTest(unittest.TestCase):
    BUY = {"side": "buy", "entry": 2000.30, "sl": 1995.30, "tp": 2010.30, "lot": 0.01}
    SELL = {"side": "sell", "entry": 2000.00, "sl": 2005.00, "tp": 1990.00, "lot": 0.01}

    def test_buy_exits_on_bid(self):
        self.assertIsNone(fills.exit_price(self.BUY, high=2005.0, low=1996.0, spread_points=30, point=POINT))
        self.assertEqual(fills.exit_price(self.BUY, 2005.0, 1995.30, 30, POINT), (1995.30, "sl"))
        self.assertEqual(fills.exit_price(self.BUY, 2010.30, 1998.0, 30, POINT), (2010.30, "tp"))

    def test_sell_exits_on_ask_so_the_spread_counts(self):
        # high 2004.80 + spread 0.30 = 2005.10 reaches the stop at 2005.00
        self.assertEqual(fills.exit_price(self.SELL, 2004.80, 1995.0, 30, POINT), (2005.00, "sl"))
        self.assertIsNone(fills.exit_price(self.SELL, 2004.60, 1995.0, 30, POINT))
        # low 1989.80 + 0.30 = 1990.10 does not reach the target at 1990.00
        self.assertIsNone(fills.exit_price(self.SELL, 2001.0, 1989.80, 30, POINT))
        self.assertEqual(fills.exit_price(self.SELL, 2001.0, 1989.60, 30, POINT), (1990.00, "tp"))

    def test_candle_spanning_both_levels_takes_the_stop(self):
        self.assertEqual(fills.exit_price(self.BUY, 2020.0, 1990.0, 30, POINT)[1], "sl")
        self.assertEqual(fills.exit_price(self.SELL, 2020.0, 1980.0, 30, POINT)[1], "sl")


class ProfitTest(unittest.TestCase):
    def test_gold_lot_math(self):
        buy = {"side": "buy", "entry": 2000.30, "lot": 0.01}
        self.assertEqual(fills.profit(buy, 2010.30, contract_size=100), 10.0)
        self.assertEqual(fills.profit(buy, 1995.30, contract_size=100), -5.0)
        sell = {"side": "sell", "entry": 2000.00, "lot": 0.02}
        self.assertEqual(fills.profit(sell, 1990.00, contract_size=100), 20.0)
        self.assertEqual(fills.profit(sell, 2005.00, contract_size=100), -10.0)


if __name__ == "__main__":
    unittest.main()
