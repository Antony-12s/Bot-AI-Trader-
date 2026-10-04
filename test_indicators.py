import unittest

import indicators


class IndicatorTest(unittest.TestCase):
    def test_sma_and_ema_line_up_with_the_last_price(self):
        self.assertEqual(indicators.sma([1, 2, 3, 4, 5], 3), [2, 3, 4])
        self.assertEqual(indicators.ema([1, 2, 3, 4, 5], 3), [2, 3, 4])  # seed 2, weight 0.5
        self.assertEqual(indicators.ema([1, 2], 3), [])

    def test_rsi_hand_computed(self):
        # changes +1 -1 +1: first window gain 0.5 / loss 0.5, then gain 0.75 / loss 0.25
        self.assertEqual(indicators.rsi([1, 2, 1, 2], 2), [50.0, 75.0])
        self.assertEqual(indicators.rsi([1, 2, 3, 4], 2), [100.0, 100.0])
        self.assertEqual(indicators.rsi([4, 3, 2, 1], 2), [0.0, 0.0])
        self.assertEqual(indicators.rsi([5, 5, 5, 5], 2), [50.0, 50.0])
        self.assertEqual(indicators.rsi([1, 2], 2), [])

    def test_macd_lengths_and_direction(self):
        macd_line, signal_line, histogram = indicators.macd(list(range(40)))
        self.assertEqual((len(macd_line), len(signal_line), len(histogram)), (15, 7, 7))
        self.assertTrue(all(value > 0 for value in macd_line))  # steady rise: fast above slow
        self.assertEqual(indicators.macd([7.0] * 40)[2], [0.0] * 7)
        self.assertEqual(indicators.macd([1, 2, 3]), ([], [], []))

    def test_bollinger_bands(self):
        lower, middle, upper = indicators.bollinger([1, 2, 3, 4, 5], period=5, deviations=2)
        self.assertEqual(middle, [3])
        self.assertAlmostEqual(lower[0], 3 - 2 * 2 ** 0.5)
        self.assertAlmostEqual(upper[0], 3 + 2 * 2 ** 0.5)
        self.assertEqual(indicators.bollinger([9.0] * 6, period=5), ([9.0, 9.0],) * 3)


if __name__ == "__main__":
    unittest.main()


class CandleIndicatorTest(unittest.TestCase):
    def candles(self, rows):
        return [{"time": index * 900, "open": o, "high": h, "low": l, "close": c, "spread": 30} for index, (o, h, l, c) in enumerate(rows)]

    def test_true_range_and_atr(self):
        candles = self.candles([(10, 12, 9, 11), (11, 13, 10, 12), (12, 20, 12, 19), (19, 19, 10, 11)])
        self.assertEqual(indicators.true_ranges(candles), [3, 8, 9])  # bar range, gap-up high vs close 12, bar range
        self.assertEqual(indicators.atr(candles, period=2), [5.5, 7.25])  # (3+8)/2 then (5.5 + 9)/2
        self.assertEqual(indicators.atr(candles, period=3), [20 / 3])
        self.assertEqual(indicators.atr(candles, period=4), [])

    def test_resample_merges_clock_aligned_blocks(self):
        rows = [(1, 2, 0.5, 1.5), (1.5, 3, 1, 2), (2, 2.5, 1, 1.2), (1.2, 1.4, 0.8, 1.0), (1.0, 1.1, 0.9, 1.05)]
        candles = self.candles(rows)
        for candle in candles:
            candle["time"] += 1800  # start at half past: the first block is a partial hour
        bars = indicators.resample(candles, factor=4, seconds=900)
        self.assertEqual([bar["time"] for bar in bars], [0, 3600])  # two candles in the first hour, three in the next
        self.assertEqual(bars[0], {"time": 0, "open": 1, "high": 3, "low": 0.5, "close": 2, "spread": 30})
        self.assertEqual(bars[1], {"time": 3600, "open": 2, "high": 2.5, "low": 0.8, "close": 1.05, "spread": 30})

    def test_adx_reads_low_in_chop_and_high_in_a_trend(self):
        chop = self.candles([(2000 + (0.6 if i % 2 == 0 else -0.6),) * 4 for i in range(80)])
        for i, candle in enumerate(chop):
            candle["high"], candle["low"] = candle["close"] + 0.8, candle["close"] - 0.8
        trend = self.candles([(2000 + i, 2001 + i, 1999.5 + i, 2000.8 + i) for i in range(80)])
        self.assertLess(indicators.adx(chop)[-1], 10)
        self.assertGreater(indicators.adx(trend)[-1], 90)
        self.assertEqual(len(indicators.adx(trend)), 80 - 2 * 14 + 1)
        self.assertEqual(indicators.adx(trend[:28]), [])
