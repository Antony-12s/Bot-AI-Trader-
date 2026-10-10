import io
import json
import unittest

import signals


class ParseTest(unittest.TestCase):
    def test_reads_plain_text_and_tradingview_json(self):
        self.assertEqual(signals.parse("buy"), ("buy", None))
        self.assertEqual(signals.parse("SELL XAUUSD now"), ("sell", None))
        self.assertEqual(signals.parse('{"action": "long", "ticker": "XAUUSD"}'), ("buy", None))
        self.assertEqual(signals.parse("short"), ("sell", None))

    def test_skips_unclear_messages(self):
        for text in ("hello", "", "buy or sell?", "buyer"):
            side, why = signals.parse(text)
            self.assertIsNone(side)
            self.assertTrue(why)

    def test_exit_wording_never_opens_a_trade(self):
        for text in ("close long", "Exit short", '{"action": "sell", "comment": "exit long"}', "flat"):
            side, why = signals.parse(text)
            self.assertIsNone(side, text)
            self.assertIn("exit", why)

    def test_old_signals_are_too_late(self):
        self.assertIsNone(signals.too_old(1000, 1000 + signals.MAX_AGE_SECONDS))
        self.assertIn("too late", signals.too_old(1000, 1000 + signals.MAX_AGE_SECONDS + 1))


class PollTest(unittest.TestCase):
    def test_returns_messages_and_advances_the_cursor(self):
        lines = [{"event": "open"}, {"event": "message", "id": "a1", "time": 100, "message": "buy"},
                 {"event": "message", "id": "b2", "time": 101, "message": "sell"}]
        seen = []

        def opener(url, timeout):
            seen.append(url)
            return io.BytesIO("\n".join(json.dumps(line) for line in lines).encode())

        texts, cursor = signals.poll("t", "123", opener)
        self.assertEqual((texts, cursor), ([("buy", 100), ("sell", 101)], "b2"))
        self.assertIn("/t/json?poll=1&since=123", seen[0])

    def test_keeps_the_cursor_when_nothing_arrived(self):
        self.assertEqual(signals.poll("t", "x9", lambda url, timeout: io.BytesIO(b"")), ([], "x9"))

    def test_topics_are_long_and_random(self):
        self.assertNotEqual(signals.new_topic(), signals.new_topic())
        self.assertGreater(len(signals.new_topic()), 30)


if __name__ == "__main__":
    unittest.main()
