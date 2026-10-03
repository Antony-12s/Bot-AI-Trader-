import unittest

import indicators
import strategy


class StrategyTest(unittest.TestCase):
    def test_cross_up_buys_cross_down_sells(self):
        self.assertEqual(strategy.decide([100] * 30 + [110])[0], "buy")
        self.assertEqual(strategy.decide([100] * 30 + [90])[0], "sell")

    def test_no_cross_or_too_little_data_gives_nothing(self):
        self.assertIsNone(strategy.decide([100] * 31)[0])
        self.assertIsNone(strategy.decide([100, 110])[0])


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
