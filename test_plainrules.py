import unittest

import plainrules
import strategies
from test_bot import CROSS_UP, FLAT, candles


def bars(closes, opens=None):
    """Closed candles from closes (oldest first); open = the close before unless given."""
    opens = opens or [closes[0]] + closes[:-1]
    return [{"time": 900 * i, "open": o, "high": max(o, c), "low": min(o, c), "close": c} for i, (o, c) in enumerate(zip(opens, closes))]


class ParseTest(unittest.TestCase):
    def test_reads_the_ways_people_write_rules(self):
        cases = {
            "Buy when the 10-candle average price crosses above the 30-candle average. Sell on the opposite cross.":
                (["SMA(10) crosses above SMA(30)"], ["SMA(10) crosses below SMA(30)"]),
            "Buy when price is above EMA 50, RSI(14) is between 40 and 65 and the candle is green. Sell on the mirror image.":
                (["price above EMA(50)", "RSI between 40 and 65", "green candle"], ["price below EMA(50)", "RSI between 35 and 60", "red candle"]),
            "Go long when MACD crosses above its signal line while the 200 EMA is rising.":
                (["MACD crosses above its signal line", "EMA(200) rising"], []),
            "Buy on a bullish engulfing candle. Short when the 20 sma is below the 50 sma and price closes below the previous low.":
                (["bullish engulfing candle"], ["SMA(20) below SMA(50)", "close below the previous low"]),
            "Buy when RSI is oversold and the MACD histogram is positive. Sell when RSI is overbought.":
                (["RSI below 30", "MACD histogram positive"], ["RSI above 70"]),
            "Buy when RSI climbs back above 30 just after a close below the lower Bollinger band.":
                (["RSI crosses above 30", "close below the lower Bollinger band"], []),
        }
        for text, (buy, sell) in cases.items():
            got = plainrules.summary(text)
            self.assertEqual((got["buy"], got["sell"], got["unknown"]), (buy, sell, []), text)

    def test_what_it_cannot_read_is_reported_and_blocks_that_side(self):
        text = "Buy when RSI is below 30 and the moon is full. Sell when RSI is above 70. Only trade when ADX is below 20."
        rules = plainrules.parse(text)
        self.assertEqual(rules["unknown"]["buy"], ["the moon is full", "adx is below 20"])
        self.assertEqual(rules["unknown"]["sell"], ["adx is below 20"])  # a sentence naming no side binds both
        signal, reason = plainrules.decide(text, bars([100.0] * 40))
        self.assertIsNone(signal)
        self.assertIn("no rule the built-in analyst can read", reason)

    def test_a_clause_that_says_more_than_it_matched_is_not_guessed(self):
        self.assertIsNone(plainrules.read_clause("rsi above 30 on the daily chart"))
        self.assertEqual(plainrules.read_clause("rsi above 30"), ("rsi", ">", 30.0))


class DecideTest(unittest.TestCase):
    def test_written_average_cross_trades_like_the_template_code(self):
        text = strategies.PLAIN["ma_cross"][2]
        closed = candles(CROSS_UP)[:-1]
        self.assertEqual(plainrules.decide(text, closed)[0], strategies.ma_cross(closed, {})[0])
        self.assertEqual(plainrules.decide(text, candles(FLAT)[:-1]), (None, "rules not met"))

    def test_trades_one_side_only(self):
        rising = bars([100 + i for i in range(60)])
        self.assertEqual(plainrules.decide("Buy when price is above SMA 20. Sell on the mirror image.", rising),
                         ("buy", "price above SMA(20)"))
        both = "Buy when the candle is green. Sell when price is above SMA 20."
        self.assertEqual(plainrules.decide(both, rising), (None, "both sides' rules hold: hold"))

    def test_candle_shapes(self):
        engulf = bars([100.0, 99.0, 101.0], opens=[100.0, 100.0, 98.5])
        self.assertEqual(plainrules.decide("Buy on a bullish engulfing candle.", engulf)[0], "buy")
        self.assertEqual(plainrules.decide("Buy when price closes above the previous high.", engulf)[0], "buy")
        self.assertIsNone(plainrules.decide("Sell on a red candle.", engulf)[0])

    def test_too_little_history_reads_as_no(self):
        self.assertEqual(plainrules.decide("Buy when price is above EMA 200.", bars([1.0] * 10)), (None, "rules not met"))


if __name__ == "__main__":
    unittest.main()
