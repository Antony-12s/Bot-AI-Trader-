import unittest
from unittest import mock

import indicators
import strategies

CONFIG = {"STRATEGY": "all"}


def candle(index, close, high=None, low=None, open_=None):
    return {
        "time": index * 900, "open": open_ if open_ is not None else close,
        "high": high if high is not None else close, "low": low if low is not None else close,
        "close": close, "spread": 30,
    }


def series(closes):
    return [candle(index, close) for index, close in enumerate(closes)]


class MaCrossTest(unittest.TestCase):
    def test_cross_up_buys_cross_down_sells(self):
        self.assertEqual(strategies.ma_cross(series([100] * 30 + [110]), CONFIG)[0], "buy")
        self.assertEqual(strategies.ma_cross(series([100] * 30 + [90]), CONFIG)[0], "sell")

    def test_no_cross_or_too_little_data_gives_nothing(self):
        self.assertIsNone(strategies.ma_cross(series([100] * 31), CONFIG)[0])
        self.assertIsNone(strategies.ma_cross(series([100, 110]), CONFIG)[0])


def zigzag(start, up, down, length=120):
    """A trend that breathes: alternating up and down steps (RSI near 59, not a 100-RSI straight line)."""
    closes = [start]
    for index in range(length - 1):
        closes.append(closes[-1] + (up if index % 2 == 0 else down))
    return closes


class TrendPullbackTest(unittest.TestCase):
    def uptrend_with_dip(self, bullish_close=True):
        closes = zigzag(1000.0, +2.0, -1.5)  # drifts up, ends on an up step
        candles = series(closes)
        ema20 = indicators.ema(closes, 20)
        candles[-2] = candle(118, closes[-2], low=ema20[-2] - 0.5)  # the candle before last dipped under EMA20
        candles[-1] = candle(119, closes[-1], open_=closes[-1] + (-1.0 if bullish_close else 1.0))
        return candles

    def test_buys_a_bullish_close_after_a_dip_into_ema20_in_an_uptrend(self):
        signal, reason = strategies.trend_pullback(self.uptrend_with_dip(), CONFIG)
        self.assertEqual(signal, "buy", reason)

    def test_holds_without_a_dip_or_with_a_bearish_close(self):
        no_dip = series(zigzag(1000.0, +2.0, -1.5))  # lows never reach EMA20
        self.assertIsNone(strategies.trend_pullback(no_dip, CONFIG)[0])
        self.assertIsNone(strategies.trend_pullback(self.uptrend_with_dip(bullish_close=False), CONFIG)[0])
        straight_up = series([1000 + index * 2.0 for index in range(120)])  # RSI 100: too stretched to buy
        straight_up[-2]["low"] = indicators.ema(strategies.closes(straight_up), 20)[-2] - 0.5
        self.assertIsNone(strategies.trend_pullback(straight_up, CONFIG)[0])

    def test_sells_the_mirror_image(self):
        closes = zigzag(3000.0, -2.0, +1.5)
        candles = series(closes)
        ema20 = indicators.ema(closes, 20)
        candles[-2] = candle(118, closes[-2], high=ema20[-2] + 0.5)
        candles[-1] = candle(119, closes[-1], open_=closes[-1] + 1.0)
        self.assertEqual(strategies.trend_pullback(candles, CONFIG)[0], "sell")


class BollingerBreakoutTest(unittest.TestCase):
    def test_breakout_from_flat_bands(self):
        self.assertEqual(strategies.bollinger_breakout(series([2000.0] * 40 + [2010.0]), CONFIG)[0], "buy")
        self.assertEqual(strategies.bollinger_breakout(series([2000.0] * 40 + [1990.0]), CONFIG)[0], "sell")

    def test_no_squeeze_no_trade(self):
        noisy = [2000 + (index % 2) * 5.0 for index in range(40)] + [2010.0]  # bands were wide already
        self.assertIsNone(strategies.bollinger_breakout(series(noisy), CONFIG)[0])
        self.assertIsNone(strategies.bollinger_breakout(series([2000.0] * 41), CONFIG)[0])


class RsiReversionTest(unittest.TestCase):
    def capitulation(self, sign):
        """Ten steps of 3, a 20-point flush that closes outside the band (RSI 0), then a 25-point snap back."""
        closes = [2000.0] * 30 + [2000 + sign * index * 3.0 for index in range(1, 11)]
        closes.append(closes[-1] + sign * 20.0)
        closes.append(closes[-1] - sign * 25.0)
        return series(closes)

    def test_buys_when_rsi_turns_up_from_oversold_under_the_band(self):
        signal, reason = strategies.rsi_reversion(self.capitulation(-1), CONFIG)
        self.assertEqual(signal, "buy", reason)

    def test_sells_the_mirror_image_and_holds_on_flat(self):
        self.assertEqual(strategies.rsi_reversion(self.capitulation(+1), CONFIG)[0], "sell")
        self.assertIsNone(strategies.rsi_reversion(series([2000.0] * 50), CONFIG)[0])


class SelectionTest(unittest.TestCase):
    def test_selected_names(self):
        self.assertEqual(strategies.selected({"STRATEGY": "all"}), list(strategies.STRATEGIES))
        self.assertEqual(strategies.selected({"STRATEGY": "ma_cross, rsi_reversion"}), ["ma_cross", "rsi_reversion"])

    def test_rules_brain_trades_one_setup_and_holds_on_disagreement(self):
        candles = series([100] * 30 + [110])
        self.assertEqual(strategies.decide(candles, {"STRATEGY": "ma_cross"}), ("buy", "ma_cross: fast MA crossed above slow MA"))
        self.assertEqual(strategies.decide(candles, {"STRATEGY": "rsi_reversion"}), (None, "no setup"))
        self.assertEqual(strategies.candidates(candles, {"STRATEGY": "ma_cross"}), [("ma_cross", "buy", "fast MA crossed above slow MA")])
        agreeing = series([100] * 40 + [90])  # ma_cross sells and bollinger_breakout sells
        self.assertEqual(strategies.decide(agreeing, {"STRATEGY": "ma_cross,bollinger_breakout"})[0], "sell")
        with mock.patch.dict(strategies.STRATEGIES, {"contrarian": lambda candles, config: ("sell", "always sells")}):
            signal, reason = strategies.decide(candles, {"STRATEGY": "all"})
        self.assertIsNone(signal)
        self.assertIn("setups disagree", reason)


if __name__ == "__main__":
    unittest.main()
