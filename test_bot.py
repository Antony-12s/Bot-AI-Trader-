"""Run: python -m unittest   (no broker needed: every broker call goes through FakeBroker)"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ai_strategy
import bot
from journal import Journal
from risk import SECONDS_PER_DAY

CONFIG = {
    "BROKER": "mt5",
    "MODE": "demo",
    "BRAIN": "rules",
    "STRATEGY": "ma_cross",
    "ANTHROPIC_API_KEY": "",
    "AI_BUDGET_USD": 5.0,
    "SYMBOL": "XAUUSD",
    "TIMEFRAME": "M15",
    "LOT": 0.01,
    "CONTRACT_SIZE": 100.0,
    "SL_ATR": 0.0,
    "TP_ATR": 0.0,
    "ATR_PERIOD": 14,
    "SL_POINTS": 500,
    "TP_POINTS": 1000,
    "MAX_DAILY_LOSS": 20.0,
    "MAX_SPREAD_POINTS": 50,
    "MAGIC": 7,
    "FILLING": "IOC",
    "TELEGRAM_TOKEN": "token",
    "TELEGRAM_CHAT_ID": "111",
}
CANDLE = 900  # M15
TICK = SimpleNamespace(ask=2650.50, bid=2650.20, time=10 * SECONDS_PER_DAY + 3600)
GOLD = SimpleNamespace(name="XAUUSD", point=0.01, digits=2, stops_level=0, contract_size=100.0, min_lot=0.01, spread_points=30, filling="IOC")
DEMO_ACCOUNT = SimpleNamespace(login=1, server="demo", is_demo=True, balance=1000.0, currency="USD")
REAL_ACCOUNT = SimpleNamespace(login=2, server="real", is_demo=False, balance=1000.0, currency="USD")


class FakeBroker:
    """Answers every broker call from attributes the test sets; records the orders it was given."""
    name = "mt5"

    def __init__(self, candles=None, tick=TICK, symbol=GOLD, account=DEMO_ACCOUNT, positions=(), realized=0.0,
                 results=None, order=None, alive=True):
        self.candle_data, self.tick_data, self.symbol_data, self.account_data = candles, tick, symbol, account
        self.positions_data, self.realized, self.results = list(positions), realized, results or {}
        self.order_result = order or SimpleNamespace(ok=True, position_id=777, detail="")
        self.alive_flag, self.orders, self.connects = alive, [], 0

    def connect(self):
        self.connects += 1
        return self.alive_flag

    def connection_hint(self):
        return "fake broker is down"

    def alive(self):
        return self.alive_flag

    def shutdown(self):
        pass

    def account(self):
        return self.account_data

    def symbols(self):
        return [GOLD.name]

    def select_symbol(self, name):
        return True

    def symbol(self, name):
        return self.symbol_data

    def tick(self, name):
        return self.tick_data

    def candles(self, name, timeframe, count):
        return self.candle_data

    def open_positions(self, name, magic):
        return self.positions_data

    def position_result(self, position_id):
        return self.results.get(position_id)

    def realized_since(self, server_time):
        return self.realized

    def market_order(self, side, name, lot, price, sl, tp, magic, filling):
        self.orders.append({"side": side, "name": name, "lot": lot, "price": price, "sl": sl, "tp": tp, "magic": magic, "filling": filling})
        return self.order_result


def candles(closes, shift=0):
    """Fake candles whose last one is forming at TICK.time; shift moves them on by whole candles."""
    count = len(closes)
    return [
        {
            "time": TICK.time - (count - index - shift) * CANDLE, "open": close, "high": close,
            "low": close, "close": close, "spread": 30,
        }
        for index, close in enumerate(closes)
    ]


# Prices sit near TICK so paper stops at 2645.50 / 2660.50 are not hit by accident.
CROSS_UP = [2650.0] * 30 + [2655.0, 2655.0]  # last one = forming candle
FLAT = [2650.0] * 32


class RiskTest(unittest.TestCase):
    def test_block_reasons(self):
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, CONFIG))
        self.assertEqual(bot.block_reason(True, 0, 0.0, 30, CONFIG), "paused")
        self.assertIn("already open", bot.block_reason(False, 1, 0.0, 30, CONFIG))
        self.assertIn("daily loss", bot.block_reason(False, 0, -20.0, 30, CONFIG))
        self.assertIn("spread", bot.block_reason(False, 0, 0.0, 51, CONFIG))

    def test_ai_budget_blocks_only_the_ai_brain(self):
        ai_config = dict(CONFIG, BRAIN="ai")
        self.assertIn("AI budget", bot.block_reason(False, 0, 0.0, 30, ai_config, ai_spent_today=5.0))
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, ai_config, ai_spent_today=4.99))
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, CONFIG, ai_spent_today=99.0))

    def test_demo_mode_refuses_real_account(self):
        self.assertIsNotNone(bot.account_error("demo", is_demo_account=False))
        self.assertIsNone(bot.account_error("demo", is_demo_account=True))
        self.assertIsNone(bot.account_error("live", is_demo_account=False))

    def test_pnl_today_asks_the_broker_from_server_midnight(self):
        broker = FakeBroker(realized=-12.5)
        with mock.patch.object(broker, "realized_since", wraps=broker.realized_since) as realized:
            self.assertEqual(bot.pnl_today(broker, TICK.time), -12.5)
        realized.assert_called_once_with(10 * SECONDS_PER_DAY)


class OrderTest(unittest.TestCase):
    def test_buy_uses_ask_with_sl_below_and_tp_above(self):
        plan = bot.plan_order("buy", TICK, GOLD, CONFIG)
        self.assertEqual((plan["price"], plan["sl"], plan["tp"], plan["lot"]), (2650.50, 2645.50, 2660.50, 0.01))

    def test_sell_uses_bid_with_sl_above_and_tp_below(self):
        plan = bot.plan_order("sell", TICK, GOLD, CONFIG)
        self.assertEqual((plan["price"], plan["sl"], plan["tp"]), (2650.20, 2655.20, 2640.20))

    def test_atr_stops_respect_the_brokers_minimum_distance(self):
        atr_config = dict(CONFIG, SL_ATR=1.5, TP_ATR=3.0)
        plan = bot.plan_order("buy", TICK, GOLD, atr_config, atr_value=2.0, spread_points=30)
        self.assertEqual((plan["sl"], plan["tp"]), (2647.50, 2656.50))  # 300 / 600 points
        strict = SimpleNamespace(point=0.01, digits=2, stops_level=400)
        plan = bot.plan_order("buy", TICK, strict, atr_config, atr_value=2.0, spread_points=30)
        self.assertEqual((plan["sl"], plan["tp"]), (2646.50, 2656.50))  # stop pushed out to 400 points

    def place(self, mode, account, order=None):
        broker = FakeBroker(account=account, order=order)
        with mock.patch.object(bot, "notify") as notify:
            try:
                opened = bot.place_order("buy", TICK, GOLD, dict(CONFIG, MODE=mode), {"broker": broker})
            except SystemExit:
                return broker, notify, True, None
        return broker, notify, False, opened

    def test_dry_mode_never_sends_an_order_but_reports_a_paper_position(self):
        broker, notify, _, opened = self.place("dry", REAL_ACCOUNT)
        self.assertEqual(broker.orders, [])
        self.assertIn("[paper] buy", notify.call_args.args[1])
        self.assertEqual(opened["price"], 2650.50)
        self.assertNotIn("position_id", opened)

    def test_demo_mode_on_real_account_stops_without_ordering(self):
        broker, _, exited, _ = self.place("demo", REAL_ACCOUNT)
        self.assertEqual((broker.orders, exited), ([], True))

    def test_demo_mode_on_unknown_account_stops_without_ordering(self):
        broker, _, exited, _ = self.place("demo", None)
        self.assertEqual((broker.orders, exited), ([], True))

    def test_demo_mode_on_demo_account_sends_once_and_keeps_the_position_id(self):
        broker, notify, _, opened = self.place("demo", DEMO_ACCOUNT)
        self.assertEqual(len(broker.orders), 1)
        self.assertEqual(broker.orders[0], {"side": "buy", "name": "XAUUSD", "lot": 0.01, "price": 2650.50, "sl": 2645.50, "tp": 2660.50, "magic": 7, "filling": "IOC"})
        self.assertIn("opened", notify.call_args.args[1])
        self.assertEqual(opened["position_id"], 777)

    def test_rejected_order_is_reported_not_retried(self):
        rejected = SimpleNamespace(ok=False, position_id=None, detail="retcode=10027 AutoTrading disabled")
        broker, notify, _, opened = self.place("demo", DEMO_ACCOUNT, order=rejected)
        self.assertEqual(len(broker.orders), 1)
        self.assertIn("FAILED", notify.call_args.args[1])
        self.assertIn("10027", notify.call_args.args[1])
        self.assertIsNone(opened)


class CheckMarketTest(unittest.TestCase):
    def run_check(self, rates, state, config=CONFIG):
        state.setdefault("broker", FakeBroker())
        state["broker"].candle_data = rates
        with mock.patch("builtins.print"), mock.patch.object(bot, "notify"), \
                mock.patch.object(bot, "place_order") as place_order:
            bot.check_market(config, state)
        return place_order

    def test_trades_only_candles_that_close_while_running(self):
        state = {"paused": False, "last_candle_time": None}
        self.run_check(candles(CROSS_UP), state).assert_not_called()  # closed before start
        self.run_check(candles(CROSS_UP), state).assert_not_called()  # same candle again
        state["last_candle_time"] -= 1  # pretend that candle just closed
        self.assertEqual(self.run_check(candles(CROSS_UP), state).call_args.args[0], "buy")

    def test_rules_brain_names_the_setup(self):
        state = {"paused": False, "last_candle_time": -1}
        self.run_check(candles(CROSS_UP), state)
        self.assertEqual(state["last_decision"], "buy: ma_cross: fast MA crossed above slow MA")

    def test_paused_bot_does_not_order(self):
        state = {"paused": True, "last_candle_time": -1}
        self.run_check(candles(CROSS_UP), state).assert_not_called()
        self.assertEqual(state["last_decision"], "skipped: paused")

    def test_candle_that_closed_long_ago_is_not_traded(self):
        state = {"paused": False, "last_candle_time": -1}
        weekend_old = candles(CROSS_UP, shift=-3 * 96)  # three days of M15 candles ago
        self.run_check(weekend_old, state).assert_not_called()
        self.assertIn("market was shut", state["last_decision"])

    def test_no_price_from_the_broker_skips_the_candle(self):
        state = {"paused": False, "last_candle_time": -1, "broker": FakeBroker(tick=None)}
        self.run_check(candles(CROSS_UP), state).assert_not_called()
        self.assertEqual(state["last_decision"], "skipped: no price from the broker")

    def test_ai_brain_is_asked_only_when_a_trade_is_allowed(self):
        ai_config = dict(CONFIG, BRAIN="ai")
        rates = candles([100.0] * 5)
        decision = ai_strategy.Decision("sell", "AI: weak", 0.01)
        with mock.patch.object(bot.ai_strategy, "decide", return_value=decision) as decide:
            self.run_check(rates, {"paused": True, "last_candle_time": -1}, ai_config).assert_not_called()
            decide.assert_not_called()  # a blocked candle must not cost an API call
            place_order = self.run_check(rates, {"paused": False, "last_candle_time": -1}, ai_config)
        decide.assert_called_once()
        self.assertEqual((place_order.call_args.args[0], place_order.call_args.args[5]), ("sell", "AI: weak"))


class PaperTest(unittest.TestCase):
    """Dry mode: virtual positions filled from the live candles, recorded in the journal."""

    def setUp(self):
        self.journal = Journal()
        self.broker = FakeBroker()
        self.config = dict(CONFIG, MODE="dry")
        self.state = {"paused": False, "last_candle_time": -1, "update_offset": 0, "journal": self.journal,
                      "broker": self.broker, "run": "test"}
        self.notices = []

    def run_check(self, rates, config=None):
        self.broker.candle_data = rates
        with mock.patch("builtins.print"), \
                mock.patch.object(bot, "notify", side_effect=lambda config, text: self.notices.append(text)):
            bot.check_market(config or self.config, self.state)

    def stop_out(self, shift):
        rates = candles(FLAT, shift=shift)
        rates[-2]["low"] = 2640.0  # the candle that just closed went through the stop at 2645.50
        return rates

    def test_paper_position_opens_blocks_and_closes_on_the_stop(self):
        self.run_check(candles(CROSS_UP))
        (trade,) = self.journal.open_trades("paper")
        self.assertEqual((trade["side"], trade["entry"], trade["sl"], trade["tp"]), ("buy", 2650.50, 2645.50, 2660.50))
        self.assertEqual((trade["opened_at"], trade["brain"]), (TICK.time, "rules"))
        self.assertIn("symbol: XAUUSD", trade["snapshot"])
        self.assertEqual(self.journal.decision_counts(), {"buy": 1})
        self.assertEqual(self.broker.orders, [])  # paper: nothing reached the broker
        self.assertTrue(self.notices[-1].startswith("[paper] buy 0.01 XAUUSD @ 2650.5"))

        self.run_check(candles(CROSS_UP, shift=1))
        self.assertEqual(self.state["last_decision"], "skipped: position already open")

        rates = self.stop_out(shift=2)
        self.run_check(rates)
        (closed,) = self.journal.closed_trades()
        self.assertEqual((closed["exit"], closed["profit"], closed["outcome"]), (2645.50, -5.0, "sl"))
        self.assertEqual(closed["closed_at"], rates[-2]["time"] + CANDLE)  # that candle's close time
        self.assertIn("closed buy 0.01 XAUUSD @ 2645.5 by sl, profit -5.00", self.notices)
        self.assertEqual(self.journal.open_trades(), [])  # flat prices: no new cross, no re-entry

    def test_paper_losses_count_against_the_daily_limit(self):
        self.run_check(candles(CROSS_UP))
        self.run_check(self.stop_out(shift=1))
        self.run_check(candles(CROSS_UP, shift=2), dict(self.config, MAX_DAILY_LOSS=4.0))
        self.assertEqual(self.state["last_decision"], "skipped: daily loss limit hit (-5.00)")

    def test_ai_brain_learns_from_a_paper_trade(self):
        ai_config = dict(self.config, BRAIN="ai")
        decision = ai_strategy.Decision("buy", "AI: momentum", 0.02)
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide, \
                mock.patch.object(ai_strategy, "reflect", return_value=("Wait for a pullback.", None, 0.01)) as reflect:
            self.run_check(candles(CROSS_UP), ai_config)
            self.assertEqual(decide.call_args.args[2], "Your track record: no closed trades yet. Trade cautiously and build one.")
            self.run_check(self.stop_out(shift=1), ai_config)
        reflect.assert_called_once()
        (closed,) = self.journal.closed_trades()
        self.assertEqual((closed["lesson"], closed["brain"]), ("Wait for a pullback.", "ai"))
        self.assertEqual(self.journal.spend(source="bot"), 0.05)  # two decisions and one reflection
        self.assertTrue(any(notice.endswith("profit -5.00\nlesson: Wait for a pullback.") for notice in self.notices))
        # the brain bought again right after the stop-out; stop that one too, then the budget is gone
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide, \
                mock.patch.object(ai_strategy, "reflect", return_value=(None, "AI error: API status 500", 0.01)):
            self.run_check(self.stop_out(shift=2), dict(ai_config, AI_BUDGET_USD=0.06))
        decide.assert_not_called()
        self.assertEqual(len(self.journal.closed_trades()), 2)
        self.assertEqual(self.journal.spend(source="bot"), 0.06)
        self.assertIn("AI budget for today spent", self.state["last_decision"])


class SettleBrokerTest(unittest.TestCase):
    POSITION = {"side": "buy", "lot": 0.01, "entry": 2650.5, "sl": 2645.5, "tp": 2660.5, "opened_at": TICK.time - 1800, "reason": "x"}

    def settle(self, results):
        journal = Journal()
        journal.open_trade("mt5", "t", "XAUUSD", self.POSITION, position_id=42, brain="rules")
        state = {"paused": False, "last_candle_time": -1, "journal": journal, "run": "t", "broker": FakeBroker(results=results)}
        with mock.patch.object(bot, "notify"), mock.patch("builtins.print"):
            bot.settle_broker(dict(CONFIG, MODE="demo"), state)
        return journal

    def test_closed_position_is_booked_from_the_brokers_result(self):
        result = {"exit": 2645.5, "closed_at": TICK.time - 100, "profit": -5.2, "outcome": "sl"}
        (trade,) = self.settle({42: result}).closed_trades()
        self.assertEqual((trade["exit"], trade["profit"], trade["outcome"], trade["closed_at"]), (2645.5, -5.2, "sl", TICK.time - 100))

    def test_still_open_waits(self):
        self.assertEqual(self.settle({}).closed_trades(), [])


class FlagTest(unittest.TestCase):
    def test_dashboard_files_pause_and_stop_the_bot(self):
        with tempfile.TemporaryDirectory() as folder:
            pause, stop = Path(folder, "pause.flag"), Path(folder, "stop.flag")
            with mock.patch.object(bot, "PAUSE_FLAG", pause), mock.patch.object(bot, "STOP_FLAG", stop):
                state = {"telegram_paused": False}
                self.assertFalse(bot.apply_flags(state))
                self.assertFalse(state["paused"])
                pause.touch()
                self.assertFalse(bot.apply_flags(state))
                self.assertTrue(state["paused"])
                pause.unlink()
                state["telegram_paused"] = True
                bot.apply_flags(state)
                self.assertTrue(state["paused"])  # Telegram's pause holds on its own
                stop.touch()
                self.assertTrue(bot.apply_flags(state))


class ConnectionTest(unittest.TestCase):
    def test_lost_and_recovered_connection_are_reported_once_each(self):
        broker = FakeBroker(alive=False)
        state = {"broker": broker}
        with mock.patch.object(bot, "notify") as notify:
            self.assertFalse(bot.ensure_connected(CONFIG, state))
            self.assertFalse(bot.ensure_connected(CONFIG, state))
        self.assertEqual(notify.call_count, 1)
        self.assertIn("connection lost", notify.call_args.args[1])
        self.assertEqual(broker.connects, 2)
        broker.alive_flag = True
        with mock.patch.object(bot, "notify") as notify:
            self.assertTrue(bot.ensure_connected(CONFIG, state))
            self.assertTrue(bot.ensure_connected(CONFIG, state))
        self.assertEqual(notify.call_count, 1)
        self.assertIn("is back", notify.call_args.args[1])


class TelegramTest(unittest.TestCase):
    def read(self, chat_id, text, state=None, **kwargs):
        update = {"update_id": 5, "message": {"chat": {"id": chat_id}, "text": text}}
        state = state or {"paused": False, "update_offset": 0}
        with mock.patch.object(bot, "telegram", return_value=[update]), \
                mock.patch.object(bot, "notify") as notify, \
                mock.patch("builtins.print"):
            bot.read_commands(CONFIG, state, **kwargs)
        return state, notify

    def test_owner_can_pause(self):
        self.assertEqual(self.read(111, "/pause")[0], {"paused": True, "telegram_paused": True, "update_offset": 6})

    def test_stranger_is_ignored(self):
        self.assertFalse(self.read(999, "/pause")[0]["paused"])

    def test_backlog_is_dropped_at_startup(self):
        self.assertEqual(self.read(111, "/pause", skip_only=True)[0], {"paused": False, "update_offset": 6})

    def test_stop_asks_the_loop_to_end(self):
        state, notify = self.read(111, "/stop")
        self.assertTrue(state["stopping"])
        self.assertIn("watchdog will not restart", notify.call_args.args[1])

    def test_status_reads_the_broker_and_the_journal(self):
        journal = Journal()
        state = {"paused": False, "update_offset": 0, "journal": journal, "broker": FakeBroker(realized=-3.5)}
        _, notify = self.read(111, "/status", state)
        text = notify.call_args.args[1]
        self.assertIn("pnl_today=-3.50", text)
        self.assertIn("open_positions=0", text)
        self.assertIn("trades=0", text)

    def test_playbook_and_lessons_come_from_the_journal(self):
        journal = Journal()
        state = {"paused": False, "update_offset": 0, "journal": journal}
        _, notify = self.read(111, "/playbook", state)
        self.assertIn("no playbook yet", notify.call_args.args[1])
        journal.save_playbook("1. trend only", trades_seen=10)
        _, notify = self.read(111, "/playbook", state)
        self.assertEqual(notify.call_args.args[1], "1. trend only")
        _, notify = self.read(111, "/lessons", state)
        self.assertEqual(notify.call_args.args[1], "no lessons yet")


class ConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            env_path = Path(folder, ".env")
            env_path.write_text(text, encoding="utf-8")
            return bot.load_config(env_path)

    def test_env_overrides_defaults_and_converts_numbers(self):
        config = self.load("# comment\nSYMBOL=GOLD\nLOT = 0.02\n")
        self.assertEqual((config["SYMBOL"], config["LOT"], config["MODE"], config["BROKER"]), ("GOLD", 0.02, "dry", "mt5"))
        self.assertEqual((config["AI_BUDGET_USD"], config["CONTRACT_SIZE"]), (5.0, 100.0))

    def test_bad_settings_stop_the_bot(self):
        for text in ("BROKER=robinhood", "MODE=yolo", "BRAIN=robot", "STRATEGY=nope", "SL_ATR=-1", "SL_POINTS=0", "TIMEFRAME=M7", "FILLING=NOPE", "CONTRACT_SIZE=0", "AI_BUDGET_USD=-1"):
            with self.assertRaises(SystemExit, msg=text):
                self.load(text)


if __name__ == "__main__":
    unittest.main()
