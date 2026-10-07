import unittest
from types import SimpleNamespace
from unittest import mock

import broker
import ui

ACCOUNT = SimpleNamespace(
    login=123456, server="XMGlobal-MT5 6", name="Test", company="XM", currency="USD",
    balance=105.5, equity=104.0, leverage=500, trade_mode=broker.mt5.ACCOUNT_TRADE_MODE_DEMO,
)
TERMINAL = SimpleNamespace(connected=True)


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

    def test_dashboard_refuses_to_switch_accounts_under_a_running_bot(self):
        mocks = self.patched()
        with mock.patch.object(ui, "bot_running", return_value=True), self.assertRaises(ValueError):
            ui.broker_login({"login": "1", "password": "p", "server": "s"})
        mocks["login"].assert_not_called()


if __name__ == "__main__":
    unittest.main()
