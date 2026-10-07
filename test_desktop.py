import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import desktop
import ui


class RunAtLoginTest(unittest.TestCase):
    def test_reads_the_installers_startup_shortcut(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict("os.environ", APPDATA=folder):
            self.assertFalse(desktop.run_at_login())
            desktop.startup_shortcut().parent.mkdir(parents=True)
            desktop.startup_shortcut().touch()
            self.assertTrue(desktop.run_at_login())

    def test_the_app_never_adds_itself_to_windows_startup(self):
        # Defender quarantined the exe (Behavior:Win32/Persistence.A!ml) when it wrote the Run key itself.
        source = Path(desktop.__file__).read_text(encoding="utf-8") + Path(ui.__file__).read_text(encoding="utf-8")
        self.assertNotIn("SetValueEx", source)
        self.assertNotIn("CurrentVersion\\\\Run", source)


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        for name, value in (("STOP_FLAG", base / "stop.flag"), ("LOG_PATH", base / "bot.log"), ("ALIVE", base / "bot.alive"),
                            ("bot_process", None), ("watchdog", {"crashes": 0, "started": 0.0, "restart_at": None})):
            patcher = mock.patch.object(ui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.starts = []
        patcher = mock.patch.object(ui, "start_bot", side_effect=lambda append=False: self.starts.append(append))
        patcher.start()
        self.addCleanup(patcher.stop)

    def died(self, code=1):
        ui.bot_process = SimpleNamespace(poll=lambda: code, returncode=code)

    def test_a_crash_restarts_after_a_pause_and_keeps_the_log(self):
        self.died()
        self.assertEqual(ui.watch_bot(1000.0), "restarting")
        self.assertEqual(ui.watch_bot(1000.0 + ui.RESTART_SECONDS - 1), "ok")
        self.assertEqual(ui.watch_bot(1000.0 + ui.RESTART_SECONDS), "restarted")
        self.assertEqual(self.starts, [True])  # append: the crash stays in bot.log
        self.assertIn("restarting it in", ui.LOG_PATH.read_text(encoding="utf-8"))

    def test_a_killed_bots_heartbeat_does_not_block_its_restart(self):
        ui.ALIVE.touch()  # a hard-killed bot.py never got to delete it
        self.died(4294967295)
        ui.watch_bot(1000.0)
        self.assertFalse(ui.ALIVE.exists())
        self.assertFalse(ui.bot_running(now=1001.0))

    def test_a_requested_stop_is_not_restarted(self):
        ui.STOP_FLAG.touch()
        self.died(0)
        self.assertEqual(ui.watch_bot(1000.0), "stopped")
        self.assertEqual(self.starts, [])

    def test_stop_pressed_while_waiting_cancels_the_restart(self):
        self.died()
        ui.watch_bot(1000.0)
        ui.STOP_FLAG.touch()
        self.assertEqual(ui.watch_bot(1000.0 + ui.RESTART_SECONDS), "stopped")
        self.assertEqual(self.starts, [])

    def test_gives_up_after_repeated_quick_crashes(self):
        now = 1000.0
        results = []
        for _ in range(ui.CRASH_LIMIT + 1):
            ui.watchdog["started"] = now  # it just started, then died at once
            self.died()
            results.append(ui.watch_bot(now))
            now += ui.RESTART_SECONDS
            ui.watchdog["restart_at"] = None
        self.assertEqual(results[:-1], ["restarting"] * ui.CRASH_LIMIT)
        self.assertEqual(results[-1], "gave up")
        self.assertIn("not restarting it", ui.LOG_PATH.read_text(encoding="utf-8"))

    def test_a_crash_after_a_long_run_starts_the_count_again(self):
        ui.watchdog.update(crashes=ui.CRASH_LIMIT, started=0.0)
        self.died()
        self.assertEqual(ui.watch_bot(ui.QUICK_CRASH_SECONDS + 1), "restarting")
        self.assertEqual(ui.watchdog["crashes"], 1)


if __name__ == "__main__":
    unittest.main()
