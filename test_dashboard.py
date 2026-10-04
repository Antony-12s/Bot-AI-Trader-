import sys
import time
import unittest
from types import SimpleNamespace

import dashboard
from journal import Journal


class BotProcessTest(unittest.TestCase):
    def test_child_output_arrives_line_by_line_and_exit_is_reported(self):
        bot = dashboard.BotProcess([sys.executable, "-u", "-c", "print('one'); print('two')"])
        self.assertFalse(bot.running)
        bot.start()
        deadline = time.time() + 10
        lines = []
        while time.time() < deadline and not any(line.startswith("[bot exited") for line in lines):
            lines += bot.drain()
            time.sleep(0.05)
        self.assertEqual(lines[:2], ["one", "two"])
        self.assertEqual(lines[-1], "[bot exited with code 0]")
        self.assertFalse(bot.running)

    def test_stop_without_a_process_does_nothing(self):
        bot = dashboard.BotProcess(["unused"])
        bot.stop()
        self.assertFalse(dashboard.STOP_FLAG.exists())


class TextTest(unittest.TestCase):
    def test_account_and_price_lines(self):
        mt5 = SimpleNamespace(ACCOUNT_TRADE_MODE_DEMO=0)
        demo = SimpleNamespace(login=123, server="XM-Demo", trade_mode=0, balance=1000.0, currency="USD")
        self.assertEqual(dashboard.account_line(demo, mt5), "MT5: 123 @ XM-Demo (demo)  balance 1000.00 USD")
        self.assertIn("REAL MONEY", dashboard.account_line(SimpleNamespace(login=1, server="s", trade_mode=2, balance=0.0, currency="USD"), mt5))
        self.assertEqual(dashboard.account_line(None, mt5), "MT5: connected, no account logged in")
        tick = SimpleNamespace(bid=2650.20, ask=2650.50, time=3600)
        self.assertEqual(dashboard.price_line("GOLD", tick, 2), "GOLD: bid 2650.20  ask 2650.50  spread 30 pts  (01:00:00 server time)")
        self.assertIn("no price", dashboard.price_line("GOLD", None, 2))

    def test_stats_lines_from_an_empty_and_a_filled_journal(self):
        journal = Journal()
        lines = dashboard.stats_lines(journal)
        self.assertEqual(lines[0], "trades 0   wins 0   losses 0   win rate 0%")
        self.assertIn("playbook: none yet", lines[3])
        position = {"side": "buy", "lot": 0.01, "entry": 2000.0, "sl": 1995.0, "tp": 2010.0, "opened_at": 0, "reason": "r"}
        trade_id = journal.open_trade("paper", "run", "GOLD", position, brain="hybrid")
        journal.close_trade(trade_id, 2010.0, 900, 10.0, "tp")
        journal.add_lesson(trade_id, "Trend days pay.")
        journal.save_playbook("1. Follow the trend.\n2. Skip news.", trades_seen=1)
        lines = dashboard.stats_lines(journal)
        self.assertEqual(lines[0], "trades 1   wins 1   losses 0   win rate 100%")
        self.assertIn("playbook: 1. Follow the trend. | 2. Skip news.", lines[3])
        self.assertEqual(lines[4], "last lesson: Trend days pay.")


if __name__ == "__main__":
    unittest.main()
