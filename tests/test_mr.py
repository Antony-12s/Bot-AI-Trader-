import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from mr_intraday import Bar, Engine, Position, aggregate, atr, adx, lot_size, NY


T = 15 * 3600


class MRTests(unittest.TestCase):
    def engine(self, **kw):
        return Engine('GBPUSD', [], contract=100000, **kw)

    def test_aggregation_rejects_gap_and_partial(self):
        rows = [Bar(i * 60, 10, 12, 9, 11, .1) for i in range(15)]
        self.assertEqual(aggregate(rows, 15), [Bar(0, 10, 12, 9, 11, .1)])
        self.assertEqual(aggregate(rows[:-1], 15), [])
        self.assertEqual(aggregate(rows[:5] + rows[6:], 15), [])

    def test_wilder_constant_and_trending(self):
        flat = [Bar(i * 60, 10, 11, 9, 10, 0) for i in range(50)]
        self.assertTrue(all(x == 2 for x in atr(flat)))
        self.assertTrue(all(x == 0 for x in adx(flat)))
        trend = [Bar(i * 60, i + 10, i + 11, i + 9, i + 10, 0) for i in range(50)]
        self.assertTrue(all(x == 100 for x in adx(trend)))

    def test_lot_floor_and_portfolio_cap(self):
        self.assertEqual(lot_size(10000, .005, .001, 100000, .01, .01, 100), .5)
        self.assertEqual(lot_size(10000, .005, .001, 100000, .01, .01, 100, 80), .2)
        self.assertEqual(lot_size(10000, .005, .001, 100000, .01, .01, 100, 100), 0)
        self.assertEqual(lot_size(100, .005, 1, 100000, .01, .01, 100), 0)

    def test_long_sl_first_and_gap(self):
        for op, expected in [(10, 9), (8, 8)]:
            e = self.engine()
            e.position = Position(1, T, 10, 9, 11, .01, 999999)
            e.on_bar(Bar(T + 60, op, 12, 7, 10, 0))
            self.assertEqual(e.trades[-1]['reason'], 'sl')
            self.assertEqual(e.trades[-1]['exit'], expected)

    def test_short_uses_ask_for_stop(self):
        e = self.engine()
        e.position = Position(-1, T, 10, 11, 9, .01, 999999)
        e.on_bar(Bar(T + 60, 10, 10.9, 9.9, 10, .2))
        self.assertEqual(e.trades[-1]['exit'], 11)

    def test_trigger_executes_next_open_and_locks_target(self):
        e = self.engine(equity=1000000)
        t = int(datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc).timestamp())
        e.bars = [Bar(t - 60, 10, 10.1, 9.9, 10, .1)]
        e.window = (1, t, 12, .5)
        e.on_bar(Bar(t, 10, 10.3, 9.9, 10.2, .1))
        self.assertIsNone(e.position)
        e.on_bar(Bar(t + 60, 10.2, 10.4, 10, 10.3, .1))
        self.assertAlmostEqual(e.position.entry, 10.3)
        self.assertEqual(e.position.tp, 12)
        self.assertFalse(e.reset[1])

    def test_rr_rejection_and_expired_window(self):
        e = self.engine()
        e.pending = (1, 10.1, 1)
        e.on_bar(Bar(60, 10, 10, 10, 10, .1))
        self.assertIsNone(e.position)
        e.window = (1, 60, 20, 1)
        e.on_bar(Bar(960, 10, 10, 10, 10, .1))
        self.assertIsNone(e.window)

    def test_news_close_and_eod_dst(self):
        e = self.engine()
        t = int(datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc).timestamp())
        e.news = [dict(time=t + 300, currency='USD', impact='high')]
        e.position = Position(1, t - 60, 10, 9, 12, .01, t + 10000)
        e.on_bar(Bar(t, 10, 10.1, 9.9, 10, .1))
        self.assertEqual(e.trades[-1]['reason'], 'news')
        for month, hour in [(7, 20), (12, 21)]:
            t = int(datetime(2026, month, 1, hour, 50, tzinfo=timezone.utc).timestamp())
            e.position = Position(1, t - 60, 10, 9, 12, .01, t + 10000)
            self.assertEqual(e.forced_exit(t), 'end_of_day')

    def test_missing_spread_baseline_blocks(self):
        self.assertIsNone(self.engine().spread_baseline(0))

    def test_setup_filters_and_reset(self):
        e = self.engine()
        t = int(datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc).timestamp())
        setup = Bar(t - 900, 10, 11, 7, 7, .1)
        with patch('mr_intraday.aggregate', return_value=[setup]), \
             patch('mr_intraday.ema20', return_value=10), \
             patch('mr_intraday.atr', return_value=[1] * 100), \
             patch('mr_intraday.adx', return_value=[10]), \
             patch.object(e, 'spread_baseline', return_value=.1):
            e.setup(t, .1, True)
            self.assertEqual(e.window, (1, t, 10, 1))
            e.reset[1] = False
            e.setup(t, .1, True)
            self.assertIsNone(e.window)
            e.reset[1] = True
            e.setup(t, .16, True)
            self.assertIsNone(e.window)
            e.news = [dict(time=t, currency='USD', impact='high')]
            e.setup(t, .1, True)
            self.assertIsNone(e.window)

    def test_time_stop_and_costs(self):
        e = self.engine(commission=7, slippage=.001)
        e.position = Position(1, T, 10, 9, 12, .01, T + 60)
        e.on_bar(Bar(T + 60, 10.1, 10.2, 10, 10.1, .1))
        self.assertEqual(e.trades[-1]['reason'], 'time_stop')
        self.assertAlmostEqual(e.trades[-1]['pnl'], 98.93)


if __name__ == '__main__':
    unittest.main()
