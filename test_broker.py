import unittest
from types import SimpleNamespace
from unittest import mock

import broker
import mt5_proxy
import ui

mt5_proxy.INLINE = True  # these tests patch broker functions in this process

ACCOUNT = SimpleNamespace(
    login=123456, server="XMGlobal-MT5 6", name="Test", company="XM", currency="USD",
    balance=105.5, equity=104.0, leverage=500, trade_mode=broker.mt5.ACCOUNT_TRADE_MODE_DEMO,
)
TERMINAL = SimpleNamespace(connected=True, path=r"C:\Program Files\MetaTrader 5")


def fake_mt5(**overrides):
    calls = dict(terminal_info=TERMINAL, account_info=ACCOUNT, initialize=True, login=True,
                 last_error=(-6, "Authorization failed"), symbols_get=())
    calls.update(overrides)
    return [mock.patch.object(broker.mt5, name, return_value=value) for name, value in calls.items()]


class BrokerTest(unittest.TestCase):
    def patched(self, **overrides):
        patches = fake_mt5(**overrides)
        mocks = {patch.attribute: patch.start() for patch in patches}
        for patch in patches:
            self.addCleanup(patch.stop)
        return mocks

    def test_status_reports_the_logged_in_account(self):
        self.patched()
        info = broker.status()
        self.assertEqual((info["login"], info["server"], info["demo"], info["online"]), (123456, "XMGlobal-MT5 6", True, True))

    def test_status_without_terminal_says_why(self):
        self.patched(terminal_info=None, initialize=False)
        info = broker.status()
        self.assertFalse(info["connected"])
        self.assertIn("Authorization failed", info["error"])

    def test_login_passes_the_password_to_mt5_only(self):
        mocks = self.patched()
        broker.login(" 123456 ", "s3cret", " XMGlobal-MT5 6 ")
        mocks["login"].assert_called_once_with(123456, password="s3cret", server="XMGlobal-MT5 6", timeout=60000)

    def test_login_errors_carry_the_terminal_reason_not_the_password(self):
        self.patched(login=False)
        with self.assertRaises(ValueError) as caught:
            broker.login("123456", "s3cret", "XMGlobal-MT5 6")
        self.assertIn("Authorization failed", str(caught.exception))
        self.assertNotIn("s3cret", str(caught.exception))
        for account, password, server in (("12ab", "p", "s"), ("123", "", "s"), ("123", "p", " ")):
            with self.assertRaises(ValueError):
                broker.login(account, password, server)

    def test_symbols_list_gold_first(self):
        names = ["EURUSD", "BarrickGold", "GOLD", "AUDUSD", "XAUUSDm"]
        self.patched(symbols_get=[SimpleNamespace(name=name) for name in names])
        self.assertEqual(broker.symbols(), ["GOLD", "XAUUSDm", "BarrickGold", "AUDUSD", "EURUSD"])

    def test_install_downloads_the_official_setup_then_opens_it(self):
        import io, os, tempfile, time
        seen, launched = [], []

        def opener(url, timeout):
            seen.append(url)
            return io.BytesIO(b"MZ fake installer")

        broker.install.update(state="idle", error="")
        broker.start_install(opener, launched.append)
        for _ in range(100):
            if broker.install["state"] != "downloading":
                break
            time.sleep(0.01)
        self.assertEqual(seen, [broker.MT5_SETUP_URL])
        self.assertTrue(broker.MT5_SETUP_URL.startswith("https://download.mql5.com/"))
        self.assertEqual(launched, [os.path.join(tempfile.gettempdir(), "mt5setup.exe")])
        self.assertEqual(broker.install["state"], "launched")

    def test_failed_download_is_reported_not_raised(self):
        import time

        def opener(url, timeout):
            raise OSError("no internet")

        broker.install.update(state="idle", error="")
        broker.start_install(opener, lambda path: None)
        for _ in range(100):
            if broker.install["state"] != "downloading":
                break
            time.sleep(0.01)
        self.assertEqual((broker.install["state"], broker.install["error"]), ("failed", "no internet"))

    def test_own_terminal_copies_the_program_and_server_list_only(self):
        import tempfile, time
        from pathlib import Path
        import config
        with tempfile.TemporaryDirectory() as folder:
            installed, own_dir = Path(folder, "XM MT5"), Path(folder, "TradeBot", "mt5")
            (installed / "Config").mkdir(parents=True)
            (installed / "terminal64.exe").write_bytes(b"MZ terminal")
            (installed / "Config" / "servers.dat").write_bytes(b"servers")
            (installed / "MetaEditor64.exe").write_bytes(b"MZ editor")
            mocks = self.patched()
            with mock.patch.object(broker, "MT5_DIR", own_dir), mock.patch.object(config, "MT5_DIR", own_dir), \
                    mock.patch.object(broker.mt5, "shutdown") as shutdown:
                self.assertEqual(config.terminal_args(), {})  # before: the PC's default MT5
                broker.own.update(state="idle", error="")
                broker.setup_own_terminal(str(installed / "terminal64.exe"))
                for _ in range(200):
                    if broker.own["state"] != "copying":
                        break
                    time.sleep(0.01)
                self.assertEqual(broker.own["state"], "done", broker.own["error"])
                self.assertEqual(sorted(p.name for p in own_dir.iterdir()), ["Config", "terminal64.exe"])
                self.assertEqual(config.terminal_args(), {"path": str(own_dir / "terminal64.exe"), "portable": True})
                shutdown.assert_called_once()  # the next attach switches to the new terminal

    def test_with_its_own_mt5_the_app_never_stays_on_the_owners_terminal(self):
        import tempfile
        from pathlib import Path
        import bot
        with tempfile.TemporaryDirectory() as folder:
            own_dir = Path(folder, "mt5")
            own_dir.mkdir()
            (own_dir / "terminal64.exe").write_bytes(b"MZ")
            owners = SimpleNamespace(path=r"C:\Program Files\XM Global MT5", connected=True)
            ours = SimpleNamespace(path=str(own_dir), connected=True)
            with mock.patch.object(broker, "MT5_DIR", own_dir), mock.patch("config.MT5_DIR", own_dir):
                broker.last_failure["at"] = 0.0
                mocks = self.patched(terminal_info=owners)
                with mock.patch.object(broker.mt5, "shutdown") as shutdown:
                    self.assertFalse(broker._attach())  # attached to the owner's MT5: leave it, refuse
                shutdown.assert_called()
                mocks["initialize"].assert_called_once_with(timeout=broker.ATTACH_TIMEOUT_MS, path=str(own_dir / "terminal64.exe"), portable=True)
                self.assertIn("wrong MT5", bot.wrong_terminal(owners))
                self.assertIsNone(bot.wrong_terminal(ours))
                broker.last_failure["at"] = 0.0
                mocks["terminal_info"].return_value = ours
                self.assertTrue(broker._attach())

    def test_login_reports_what_the_broker_says_the_account_is(self):
        self.patched()
        self.assertEqual(broker.login("123456", "pw", "S"), "demo")
        self.patched(account_info=SimpleNamespace(**dict(vars(ACCOUNT), trade_mode=broker.mt5.ACCOUNT_TRADE_MODE_REAL)))
        self.assertEqual(broker.login("123456", "pw", "S"), "live")

    def test_switch_uses_the_saved_password_and_checks_the_account_type(self):
        mocks = self.patched(account_info=SimpleNamespace(**dict(vars(ACCOUNT), login=999)))
        broker.switch("123456", "XM-Demo", "demo")
        mocks["login"].assert_called_once_with(123456, server="XM-Demo", timeout=60000)  # no password
        real = SimpleNamespace(**dict(vars(ACCOUNT), trade_mode=broker.mt5.ACCOUNT_TRADE_MODE_REAL))
        self.patched(account_info=real)
        with self.assertRaisesRegex(ValueError, "not a demo account"):
            broker.switch("123456", "XM", "demo")
        broker.switch("123456", "XM", "live")  # already on it: no login call needed
        with self.assertRaisesRegex(ValueError, "no live account yet"):
            broker.switch("", "", "live")

    def test_own_terminal_needs_an_installed_mt5(self):
        self.patched()
        with mock.patch.object(broker, "installed_terminals", return_value=[]), self.assertRaises(ValueError):
            broker.setup_own_terminal()

    def test_open_terminal_starts_mt5_when_closed_and_raises_it_when_open(self):
        import tempfile
        from pathlib import Path
        folder = tempfile.mkdtemp()
        exe = Path(folder, "terminal64.exe")
        exe.write_bytes(b"")
        self.patched(terminal_info=SimpleNamespace(connected=True, path=folder))
        user32 = mock.MagicMock()
        with mock.patch.object(broker, "own_terminal", return_value=None),                 mock.patch("ctypes.windll", SimpleNamespace(user32=user32), create=True),                 mock.patch.object(broker.subprocess, "Popen") as popen:
            self.assertEqual(broker.terminal_in_use(), exe)  # the attached terminal
            with mock.patch.object(broker, "_terminal_windows", return_value=[]):
                self.assertEqual(broker.open_terminal(), "started")
            popen.assert_called_once_with([str(exe)], cwd=folder)
            with mock.patch.object(broker, "_terminal_windows", return_value=[7, 8]),                     mock.patch.object(broker, "_class_of", side_effect=["MetaQuotes::MetaTrader::5.00", "GDI+ Window"]):
                self.assertEqual(broker.open_terminal(), "shown")
            user32.ShowWindow.assert_called_once_with(7, 9)  # the helper window stays hidden
            user32.SetForegroundWindow.assert_called_once_with(7)

    def test_open_terminal_without_mt5_says_to_install_it(self):
        self.patched(terminal_info=None)
        with mock.patch.object(broker, "own_terminal", return_value=None),                 mock.patch.object(broker, "installed_terminals", return_value=[]),                 self.assertRaisesRegex(ValueError, "not installed"):
            broker.open_terminal()

    def test_dashboard_files_each_login_under_its_real_type_and_keeps_the_mode(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder, ".env")
            env.write_text("MODE=demo\n", encoding="utf-8")
            with mock.patch.object(ui, "ENV_PATH", env), mock.patch.object(ui, "bot_running", return_value=False), \
                    mock.patch.object(broker, "login", return_value="live"), mock.patch.object(broker, "status", return_value={}):
                result = ui.broker_login({"login": "450272430", "password": "pw", "server": "XMGlobal-MT5 20"}, env)
            saved = ui.read_env(env)
            self.assertEqual(result["saved_as"], "live")
            self.assertEqual((saved["LIVE_LOGIN"], saved["LIVE_SERVER"], saved["DEMO_LOGIN"]), ("450272430", "XMGlobal-MT5 20", ""))
            self.assertEqual(saved["MODE"], "demo")  # logging in to a real account never switches the bot to Live
            self.assertNotIn("pw", env.read_text(encoding="utf-8"))
            with mock.patch.object(ui, "bot_running", return_value=True), self.assertRaisesRegex(ValueError, "Stop the bot"):
                ui.switch_mode("live", env)

    def test_dashboard_refuses_to_switch_accounts_under_a_running_bot(self):
        mocks = self.patched()
        with mock.patch.object(ui, "bot_running", return_value=True), self.assertRaises(ValueError):
            ui.broker_login({"login": "1", "password": "p", "server": "s"})
        mocks["login"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
