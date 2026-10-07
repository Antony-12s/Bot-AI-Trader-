import io
import json
import unittest
from unittest import mock

import news

FEED = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-10-09T08:30:00-04:00", "impact": "High"},
    {"title": "Retail Sales", "country": "USD", "date": "2026-10-09T10:00:00-04:00", "impact": "Medium"},
    {"title": "BOJ Gov Speaks", "country": "JPY", "date": "2026-10-09T08:30:00-04:00", "impact": "High"},
    {"title": "broken", "country": "USD", "date": "soon", "impact": "High"},
]
NFP = 1791549000  # 2026-10-09 12:30 UTC


def opener(request, timeout):
    return io.BytesIO(json.dumps(FEED).encode())


class BlackoutTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(news.cache, {"events": [], "fetched": 0.0, "problem": None})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_high_impact_news_for_the_markets_currencies_blocks_around_it(self):
        self.assertEqual(news.blackout(("XAU", "USD"), 30, NFP - 600, opener), "news: USD Non-Farm Employment Change in 10 min")
        self.assertEqual(news.blackout(("XAU", "USD"), 30, NFP + 1200, opener), "news: USD Non-Farm Employment Change 20 min ago")
        self.assertIsNone(news.blackout(("XAU", "USD"), 30, NFP + 3600, opener))  # Retail Sales is only Medium
        self.assertIsNone(news.blackout(("EUR", "GBP"), 30, NFP, opener))  # not this market's currencies
        self.assertIsNone(news.blackout(("XAU", "USD"), 0, NFP, opener))  # 0 = off

    def test_the_feed_is_read_once_an_hour_and_a_dead_feed_blocks_nothing(self):
        calls = []

        def counting(request, timeout):
            calls.append(1)
            return opener(request, timeout)

        news.blackout(("USD",), 30, NFP, counting)
        news.blackout(("USD",), 30, NFP + 60, counting)
        self.assertEqual(len(calls), 1)

        def dead(request, timeout):
            raise OSError("offline")

        with mock.patch.dict(news.cache, {"events": [], "fetched": 0.0, "problem": None}):
            self.assertIsNone(news.blackout(("USD",), 30, NFP, dead))
            self.assertIn("news pause is off", news.cache["problem"])
        self.assertIsNone(news.blackout((), 30, NFP, dead))  # no currencies: the feed is not even read


if __name__ == "__main__":
    unittest.main()
