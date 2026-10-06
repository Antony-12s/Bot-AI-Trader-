import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import bot
import ui
from journal import Journal

DAY = 86400


class DashboardTest(unittest.TestCase):
    def test_totals_equity_and_today_cover_the_range_only(self):
        journal = Journal()
        now = 100 * DAY + 3600
        position = {"side": "buy", "lot": 0.01, "entry": 1.0, "sl": 0.9, "tp": 1.2}
        for closed_at, profit in ((now - 40 * DAY, 50.0), (now - 2 * DAY, 3.0), (now - 60, -1.0)):
            trade_id = journal.open_trade("paper", "r", "XAUUSD", dict(position, opened_at=closed_at - 60))
            journal.close_trade(trade_id, 1.1, closed_at, profit, "tp")
        journal.open_trade("paper", "r", "XAUUSD", dict(position, opened_at=now))
        data = ui.dashboard(journal, 30, now)
        self.assertEqual(data["totals"]["trades"], 2)
        self.assertEqual([point[1] for point in data["equity"]], [3.0, 2.0])
        self.assertEqual(data["today"], -1.0)
        self.assertEqual(data["open"], 1)


class SettingsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.env = Path(folder.name, ".env")
        self.env.write_text("# keep me\nMODE=dry\nANTHROPIC_API_KEY=sk-secret\nSYMBOL=XAUUSD\n", encoding="utf-8")

    def test_saves_changes_keeps_comments_and_hides_secrets(self):
        ui.save_settings({"SYMBOL": "GOLD", "LOT": "0.02", "ANTHROPIC_API_KEY": ""}, self.env)
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("# keep me", text)
        self.assertIn("SYMBOL=GOLD", text)
        self.assertIn("LOT=0.02", text)
        self.assertIn("ANTHROPIC_API_KEY=sk-secret", text)
        self.assertEqual(ui.public_settings(self.env)["ANTHROPIC_API_KEY"], "set")

    def test_rejects_invalid_values_without_touching_env(self):
        before = self.env.read_text(encoding="utf-8")
        for changes in ({"MODE": "yolo"}, {"LOT": "0"}, {"SYMBOL": "X\nMODE=live"}, {"NOPE": "1"}, ["MODE"]):
            with self.assertRaises(ValueError):
                ui.save_settings(changes, self.env)
        self.assertEqual(self.env.read_text(encoding="utf-8"), before)
        self.assertFalse(self.env.with_name(".env.check").exists())


    def test_concurrent_saves_both_succeed(self):
        errors = []

        def save(lot):
            try:
                ui.save_settings({"LOT": lot}, self.env)
            except Exception as error:  # noqa: BLE001 - any failure is the bug
                errors.append(error)

        threads = [threading.Thread(target=save, args=(str(0.01 * (i + 1)),)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])


class BotControlTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        for name, path in (("ALIVE", base / "bot.alive"), ("STOP_FLAG", base / "stop.flag"), ("LOG_PATH", base / "bot.log"), ("APP_DIR", base)):
            patcher = mock.patch.object(ui, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(ui, "bot_process", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_heartbeat_tells_a_running_bot_from_a_dead_one(self):
        self.assertFalse(ui.bot_running())
        ui.ALIVE.touch()
        self.assertTrue(ui.bot_running())
        self.assertFalse(ui.bot_running(now=time.time() + ui.ALIVE_SECONDS + 1))

    def test_start_launches_bot_once_and_clears_an_old_stop_request(self):
        ui.STOP_FLAG.touch()
        with mock.patch.object(ui.subprocess, "Popen") as popen:
            popen.return_value.poll.return_value = None  # still running
            ui.start_bot()
            with self.assertRaises(ValueError):
                ui.start_bot()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0][1:], ["bot.py"])
        self.assertFalse(ui.STOP_FLAG.exists())

    def test_start_refuses_while_a_bot_from_start_bat_is_alive(self):
        ui.ALIVE.touch()
        with mock.patch.object(ui.subprocess, "Popen") as popen, self.assertRaises(ValueError):
            ui.start_bot()
        popen.assert_not_called()

    def test_stop_asks_the_bot_to_exit_through_stop_flag(self):
        ui.stop_bot()
        self.assertTrue(ui.STOP_FLAG.exists())
        with mock.patch.object(bot, "STOP_FLAG", ui.STOP_FLAG):
            self.assertTrue(bot.should_stop({}))
        ui.STOP_FLAG.unlink()
        with mock.patch.object(bot, "STOP_FLAG", ui.STOP_FLAG):
            self.assertFalse(bot.should_stop({}))
            self.assertTrue(bot.should_stop({"stopping": True}))


class HistoryTest(unittest.TestCase):
    def test_newest_first_with_open_trades_and_lessons(self):
        journal = Journal()
        position = {"side": "sell", "lot": 0.01, "entry": 1.0, "sl": 1.1, "tp": 0.8, "reason": "rsi high"}
        first = journal.open_trade("paper", "r", "XAUUSD", dict(position, opened_at=100))
        journal.close_trade(first, 0.8, 200, 2.0, "tp")
        journal.add_lesson(first, "sold the top")
        journal.open_trade("paper", "r", "XAUUSD", dict(position, opened_at=300))
        rows = ui.history(journal)
        self.assertEqual([row["closed_at"] for row in rows], [None, 200])
        self.assertEqual((rows[1]["reason"], rows[1]["lesson"]), ("rsi high", "sold the top"))


class TrustTest(unittest.TestCase):
    def trusted(self, headers):
        return ui.Handler.trusted(SimpleNamespace(headers=headers))

    def test_only_this_page_may_call_the_api(self):
        self.assertTrue(self.trusted({"Host": "127.0.0.1:8765"}))
        self.assertTrue(self.trusted({"Host": "localhost:8765", "Origin": "http://localhost:8765"}))
        self.assertFalse(self.trusted({"Host": "127.0.0.1:8765", "Origin": "https://evil.example"}))
        self.assertFalse(self.trusted({"Host": "evil.example:8765"}))


if __name__ == "__main__":
    unittest.main()
