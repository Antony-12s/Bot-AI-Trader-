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


class AgentsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.env = Path(folder.name, ".env")
        self.env.write_text("STRATEGY=trend_pullback,ma_cross\n", encoding="utf-8")

    def test_cards_split_trades_by_the_strategy_named_in_the_reason(self):
        journal = Journal()
        position = {"side": "buy", "lot": 0.01, "entry": 1.0, "sl": 0.9, "tp": 1.1, "opened_at": 1}
        for source, reason, profit in (("paper", "ma_cross: crossed", 2.0), ("replay", "ma_cross: crossed", -1.0), ("paper", "hybrid AI words", 5.0)):
            journal.close_trade(journal.open_trade(source, "r", "X", dict(position, reason=reason)), 1.1, 2, profit, "tp")
        journal.record_decision("bot", "r", "decide", 3, "buy", "ma_cross: crossed")
        cards = {card["name"]: card for card in ui.agents(journal, ui.read_env(self.env))}
        self.assertEqual((cards["ma_cross"]["totals"]["trades"], cards["ma_cross"]["live_trades"]), (2, 1))
        self.assertEqual(len(cards["ma_cross"]["decisions"]), 1)
        self.assertTrue(cards["trend_pullback"]["armed"])
        self.assertFalse(cards["rsi_reversion"]["armed"])
        self.assertEqual(cards["trend_pullback"]["totals"]["trades"], 0)

    def test_arm_and_disarm_rewrite_strategy_in_registry_order(self):
        ui.arm("rsi_reversion", True, self.env)
        self.assertEqual(ui.read_env(self.env)["STRATEGY"], "ma_cross,trend_pullback,rsi_reversion")
        ui.arm("ma_cross", False, self.env)
        ui.arm("trend_pullback", False, self.env)
        self.assertEqual(ui.read_env(self.env)["STRATEGY"], "rsi_reversion")
        with self.assertRaises(ValueError):
            ui.arm("rsi_reversion", False, self.env)  # the last one stays
        with self.assertRaises(ValueError):
            ui.arm("nope", True, self.env)


class SignalsPageTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.env = Path(folder.name, ".env")
        self.env.write_text("MODE=dry\n", encoding="utf-8")

    def test_webhook_gets_a_secret_topic_once_and_rotates_on_request(self):
        ui.switch_source("webhook", True, self.env)
        topic = ui.read_env(self.env)["WEBHOOK_TOPIC"]
        self.assertGreater(len(topic), 20)
        ui.switch_source("webhook", False, self.env)
        ui.switch_source("webhook", True, self.env)
        self.assertEqual(ui.read_env(self.env)["WEBHOOK_TOPIC"], topic)  # same URL, TradingView keeps working
        ui.rotate_topic(self.env)
        self.assertNotEqual(ui.read_env(self.env)["WEBHOOK_TOPIC"], topic)
        page = ui.signal_sources(Journal(), self.env)
        self.assertTrue(page["webhook"]["on"])
        self.assertTrue(page["webhook"]["url"].endswith(ui.read_env(self.env)["WEBHOOK_TOPIC"]))

    def test_telegram_source_needs_a_telegram_bot(self):
        with self.assertRaises(ValueError):
            ui.switch_source("telegram", True, self.env)
        with self.assertRaises(ValueError):
            ui.switch_source("email", True, self.env)

    def test_history_lists_outside_signals_only(self):
        journal = Journal()
        journal.record_decision("webhook", "r", "signal", 5, "buy", "traded: buy")
        journal.record_decision("bot", "r", "decide", 6, "hold", "no setup")
        self.assertEqual([row["reason"] for row in ui.signal_sources(journal, self.env)["history"]], ["traded: buy"])

    def test_ping_posts_a_harmless_message_to_the_topic(self):
        sent = []
        ui.switch_source("webhook", True, self.env)
        ui.ping_webhook(self.env, opener=lambda request, timeout: sent.append(request) or mock.MagicMock())
        self.assertEqual((sent[0].data, sent[0].get_method()), (b"test ping", "POST"))
        self.assertIsNone(__import__("signals").parse("test ping")[0])  # a running bot will not trade it


class WindowTest(unittest.TestCase):
    def test_no_webview2_falls_back_to_the_browser(self):
        broken = SimpleNamespace(create_window=mock.Mock(side_effect=RuntimeError("WebView2 runtime missing")))
        with mock.patch.dict("sys.modules", webview=broken), mock.patch("builtins.print"), \
                mock.patch.object(ui.sys, "platform", "linux"):
            self.assertFalse(ui.open_window("http://127.0.0.1:1"))

    def test_window_is_pinned_to_webview2(self):
        fake = SimpleNamespace(create_window=mock.Mock(), start=mock.Mock())
        with mock.patch.dict("sys.modules", webview=fake), mock.patch.object(ui.sys, "platform", "linux"):
            self.assertTrue(ui.open_window("http://127.0.0.1:1"))
        self.assertEqual(fake.start.call_args.kwargs["gui"], "edgechromium")


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
