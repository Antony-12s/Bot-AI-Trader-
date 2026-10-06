import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

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
