"""Run: python -m unittest   (no MT5 terminal needed, every MT5 call is faked)"""
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
    "MAX_TRADES_PER_DAY": 0,
    "SIGNAL_WEBHOOK": "off",
    "WEBHOOK_TOPIC": "",
    "SIGNAL_TELEGRAM": "off",
}
CANDLE = 900  # M15
TICK = SimpleNamespace(ask=2650.50, bid=2650.20, time=10 * SECONDS_PER_DAY + 3600)
GOLD = SimpleNamespace(point=0.01, digits=2)
DEMO_ACCOUNT = SimpleNamespace(trade_mode=bot.mt5.ACCOUNT_TRADE_MODE_DEMO)
REAL_ACCOUNT = SimpleNamespace(trade_mode=bot.mt5.ACCOUNT_TRADE_MODE_REAL)


def candles(closes, shift=0):
    """Fake rates whose last row is the candle forming at TICK.time; shift moves them on by whole candles."""
    count = len(closes)
    return [
        {
            "time": TICK.time - (count - index - shift) * CANDLE, "open": close, "high": close,
            "low": close, "close": close, "spread": 30,
        }
        for index, close in enumerate(closes)
    ]


# Prices sit near TICK so paper stops at 2645.50 / 2660.50 are not hit by accident.
CROSS_UP = [2650.0] * 30 + [2655.0, 2655.0]  # last row = forming candle
FLAT = [2650.0] * 32


class RiskTest(unittest.TestCase):
    def test_block_reasons(self):
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, CONFIG))
        self.assertEqual(bot.block_reason(True, 0, 0.0, 30, CONFIG), "paused")
        self.assertIn("already open", bot.block_reason(False, 1, 0.0, 30, CONFIG))
        self.assertIn("daily loss", bot.block_reason(False, 0, -20.0, 30, CONFIG))
        self.assertIn("spread", bot.block_reason(False, 0, 0.0, 51, CONFIG))

    def test_daily_trade_limit(self):
        capped = dict(CONFIG, MAX_TRADES_PER_DAY=3)
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, capped, trades_today=2))
        self.assertIn("trade limit", bot.block_reason(False, 0, 0.0, 30, capped, trades_today=3))
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, dict(CONFIG, MAX_TRADES_PER_DAY=0), trades_today=999))

    def test_trades_opened_since_counts_open_and_closed(self):
        journal = bot.Journal()
        position = {"side": "buy", "lot": 0.01, "entry": 1.0, "sl": 0.9, "tp": 1.1}
        for opened_at in (50, 150, 250):
            journal.open_trade("paper", "r", "X", dict(position, opened_at=opened_at))
        journal.close_trade(2, 1.1, 200, 1.0, "tp")
        self.assertEqual(journal.trades_opened_since(100, "paper"), 2)
        self.assertEqual(journal.trades_opened_since(100, "mt5"), 0)

    def test_ai_budget_blocks_every_brain_that_calls_the_ai(self):
        for brain_name in ("ai", "hybrid"):
            ai_config = dict(CONFIG, BRAIN=brain_name)
            self.assertIn("AI budget", bot.block_reason(False, 0, 0.0, 30, ai_config, ai_spent_today=5.0), brain_name)
            self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, ai_config, ai_spent_today=4.99), brain_name)
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, CONFIG, ai_spent_today=99.0))  # rules never pay

    def test_demo_mode_refuses_real_account(self):
        self.assertIsNotNone(bot.account_error("demo", is_demo_account=False))
        self.assertIsNone(bot.account_error("demo", is_demo_account=True))
        self.assertIsNone(bot.account_error("live", is_demo_account=False))

    def test_pnl_today_skips_deposits_and_yesterday(self):
        today = TICK.time - 60
        yesterday = TICK.time - SECONDS_PER_DAY
        buy, balance = bot.mt5.DEAL_TYPE_BUY, bot.mt5.DEAL_TYPE_BALANCE
        deals = [
            SimpleNamespace(time=today, type=buy, profit=-12.0, commission=-0.5, swap=0.0),
            SimpleNamespace(time=today, type=balance, profit=1000.0, commission=0.0, swap=0.0),
            SimpleNamespace(time=yesterday, type=buy, profit=-99.0, commission=0.0, swap=0.0),
        ]
        with mock.patch.object(bot.mt5, "history_deals_get", return_value=deals):
            self.assertEqual(bot.pnl_today(TICK.time), -12.5)


class OrderTest(unittest.TestCase):
    def test_buy_uses_ask_with_sl_below_and_tp_above(self):
        order = bot.build_order("buy", TICK, GOLD, CONFIG)
        self.assertEqual((order["price"], order["sl"], order["tp"]), (2650.50, 2645.50, 2660.50))
        self.assertEqual(order["type"], bot.mt5.ORDER_TYPE_BUY)

    def test_sell_uses_bid_with_sl_above_and_tp_below(self):
        order = bot.build_order("sell", TICK, GOLD, CONFIG)
        self.assertEqual((order["price"], order["sl"], order["tp"]), (2650.20, 2655.20, 2640.20))
        self.assertEqual(order["type"], bot.mt5.ORDER_TYPE_SELL)

    def test_atr_stops_respect_the_brokers_minimum_distance(self):
        atr_config = dict(CONFIG, SL_ATR=1.5, TP_ATR=3.0)
        order = bot.build_order("buy", TICK, GOLD, atr_config, atr_value=2.0, spread_points=30)
        self.assertEqual((order["sl"], order["tp"]), (2647.50, 2656.50))  # 300 / 600 points
        strict = SimpleNamespace(point=0.01, digits=2, trade_stops_level=400)
        order = bot.build_order("buy", TICK, strict, atr_config, atr_value=2.0, spread_points=30)
        self.assertEqual((order["sl"], order["tp"]), (2646.50, 2656.50))  # stop pushed out to 400 points

    def place(self, mode, account, retcode=bot.mt5.TRADE_RETCODE_DONE, deals=()):
        result = SimpleNamespace(retcode=retcode, comment="fake", order=501, deal=901)
        with mock.patch.object(bot.mt5, "account_info", return_value=account), \
                mock.patch.object(bot.mt5, "order_send", return_value=result) as order_send, \
                mock.patch.object(bot.mt5, "history_deals_get", return_value=deals), \
                mock.patch.object(bot, "notify") as notify:
            try:
                opened = bot.place_order("buy", TICK, GOLD, dict(CONFIG, MODE=mode))
            except SystemExit:
                return order_send, notify, True, None
        return order_send, notify, False, opened

    def test_dry_mode_never_sends_an_order_but_reports_a_paper_position(self):
        order_send, notify, _, opened = self.place("dry", REAL_ACCOUNT)
        order_send.assert_not_called()
        self.assertIn("[paper] buy", notify.call_args.args[1])
        self.assertEqual(opened["price"], 2650.50)
        self.assertNotIn("position_id", opened)

    def test_demo_mode_on_real_account_stops_without_ordering(self):
        order_send, _, exited, _ = self.place("demo", REAL_ACCOUNT)
        order_send.assert_not_called()
        self.assertTrue(exited)

    def test_demo_mode_on_unknown_account_stops_without_ordering(self):
        order_send, _, exited, _ = self.place("demo", None)
        order_send.assert_not_called()
        self.assertTrue(exited)

    def test_demo_mode_on_demo_account_sends_once_and_keeps_the_position_id(self):
        order_send, notify, _, opened = self.place("demo", DEMO_ACCOUNT, deals=[SimpleNamespace(position_id=777)])
        order_send.assert_called_once()
        self.assertIn("opened", notify.call_args.args[1])
        self.assertEqual(opened["position_id"], 777)
        _, _, _, opened = self.place("demo", DEMO_ACCOUNT, deals=())
        self.assertEqual(opened["position_id"], 501)  # no deal visible yet: the order ticket

    def test_rejected_order_is_reported_not_retried(self):
        order_send, notify, _, opened = self.place("demo", DEMO_ACCOUNT, retcode=10027)
        order_send.assert_called_once()
        self.assertIn("FAILED", notify.call_args.args[1])
        self.assertIsNone(opened)


class CheckMarketTest(unittest.TestCase):
    def run_check(self, rates, state, config=CONFIG):
        with mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=rates), \
                mock.patch("builtins.print"), \
                mock.patch.object(bot.mt5, "symbol_info_tick", return_value=TICK), \
                mock.patch.object(bot.mt5, "symbol_info", return_value=GOLD), \
                mock.patch.object(bot.mt5, "positions_get", return_value=()), \
                mock.patch.object(bot.mt5, "history_deals_get", return_value=()), \
                mock.patch.object(bot, "notify"), \
                mock.patch.object(bot, "place_order") as place_order:
            bot.check_market(config, state)
        return place_order

    def test_trades_only_candles_that_close_while_running(self):
        state = {"paused": False, "last_candle_time": None}
        self.run_check(candles(CROSS_UP), state).assert_not_called()  # closed before start
        self.run_check(candles(CROSS_UP), state).assert_not_called()  # same candle again
        state["last_candle_time"] -= 1  # pretend that candle just closed
        self.assertEqual(self.run_check(candles(CROSS_UP), state).call_args.args[0], "buy")

    def test_rules_brain_fetches_enough_candles_and_names_the_setup(self):
        state = {"paused": False, "last_candle_time": -1}
        with mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=candles(CROSS_UP)) as fetch:
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

    def test_ai_brain_is_asked_only_when_a_trade_is_allowed(self):
        ai_config = dict(CONFIG, BRAIN="ai")
        rates = candles([100.0] * 5)
        decision = ai_strategy.Decision("sell", "AI: weak", 0.01)
        with mock.patch.object(bot.ai_strategy, "decide", return_value=decision) as decide:
            self.run_check(rates, {"paused": True, "last_candle_time": -1}, ai_config).assert_not_called()
            decide.assert_not_called()  # a blocked candle must not cost an API call
            place_order = self.run_check(rates, {"paused": False, "last_candle_time": -1}, ai_config)
        decide.assert_called_once()
        self.assertEqual((place_order.call_args.args[0], place_order.call_args.args[4]), ("sell", "AI: weak"))


class PaperTest(unittest.TestCase):
    """Dry mode: virtual positions filled from the live candles, recorded in the journal."""

    def setUp(self):
        self.journal = Journal()
        self.config = dict(CONFIG, MODE="dry")
        self.state = {"paused": False, "last_candle_time": -1, "update_offset": 0, "journal": self.journal, "run": "test"}
        self.notices = []

    def run_check(self, rates, config=None):
        with mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=rates), \
                mock.patch("builtins.print"), \
                mock.patch.object(bot.mt5, "symbol_info_tick", return_value=TICK), \
                mock.patch.object(bot.mt5, "symbol_info", return_value=GOLD), \
                mock.patch.object(bot.mt5, "positions_get", return_value=()), \
                mock.patch.object(bot.mt5, "history_deals_get", return_value=()), \
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
        self.assertEqual(trade["opened_at"], TICK.time)
        self.assertIn("symbol: XAUUSD", trade["snapshot"])
        self.assertEqual(self.journal.decision_counts(), {"buy": 1})
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

    def run_signals(self, config, webhook_texts=()):
        rates = candles(FLAT)[:-1]  # closed candles only, as outside_signals asks for
        with mock.patch.object(bot.signals, "poll", return_value=(list(webhook_texts), "cursor")) as poll, \
                mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=rates), \
                mock.patch("builtins.print"), \
                mock.patch.object(bot.mt5, "symbol_info_tick", return_value=TICK), \
                mock.patch.object(bot.mt5, "symbol_info", return_value=GOLD), \
                mock.patch.object(bot.mt5, "positions_get", return_value=()), \
                mock.patch.object(bot.mt5, "history_deals_get", return_value=()), \
                mock.patch.object(bot, "notify", side_effect=lambda config, text: self.notices.append(text)):
            bot.outside_signals(config, self.state)
        return poll

    def signal_rows(self):
        return self.journal._rows("SELECT source, action, reason FROM decisions WHERE kind = 'signal' ORDER BY id")

    def test_webhook_signals_trade_under_the_same_risk_rules(self):
        self.state.update(pending=[], webhook_since="0")
        config = dict(self.config, SIGNAL_WEBHOOK="on", WEBHOOK_TOPIC="t" * 24)
        poll = self.run_signals(config, ['{"action": "sell"}', "buy", "hello"])
        poll.assert_called_once_with("t" * 24, "0")
        self.assertEqual(self.state["webhook_since"], "cursor")
        (trade,) = self.journal.open_trades("paper")
        self.assertEqual((trade["side"], trade["brain"]), ("sell", "webhook"))
        rows = self.signal_rows()
        self.assertEqual([(row["action"], row["reason"].split(":")[0]) for row in rows],
                         [("sell", "traded"), ("buy", "skipped"), ("invalid", "skipped")])
        self.assertIn("position already open", rows[1]["reason"])

    def test_mid_candle_entry_is_not_stopped_by_prices_from_before_it(self):
        self.journal.open_trade("paper", "r", "XAUUSD", {
            "side": "buy", "lot": 0.01, "entry": 2650.5, "sl": 2645.5, "tp": 2660.5, "opened_at": TICK.time + 600})
        entry_candle = {"time": TICK.time, "high": 2651.0, "low": 2640.0, "spread": 30}  # dipped before the entry
        with mock.patch.object(bot, "notify"):
            bot.settle_paper(self.config, self.state, entry_candle, GOLD)
            self.assertEqual(len(self.journal.open_trades("paper")), 1)
            bot.settle_paper(self.config, self.state, dict(entry_candle, time=TICK.time + CANDLE), GOLD)
        self.assertEqual(self.journal.closed_trades()[0]["outcome"], "sl")  # the next candle still counts

    def test_webhook_off_is_never_polled(self):
        self.state.update(pending=[], webhook_since="0")
        self.run_signals(self.config).assert_not_called()

    def test_telegram_buy_needs_signals_on_and_then_trades(self):
        self.state["pending"] = []
        self.assertIn("off", bot.handle_command("/buy", self.config, self.state))
        self.assertEqual(self.state["pending"], [])
        config = dict(self.config, SIGNAL_TELEGRAM="on")
        self.assertIn("queued", bot.handle_command("/buy", config, self.state))
        self.run_signals(config)
        (trade,) = self.journal.open_trades("paper")
        self.assertEqual((trade["side"], trade["brain"]), ("buy", "telegram"))
        self.assertIn("buy signal from telegram: traded", self.notices)

    def test_paper_losses_count_against_the_daily_limit(self):
        self.run_check(candles(CROSS_UP))
        self.run_check(self.stop_out(shift=2))  # the first candle that opens after the entry at TICK.time
        self.run_check(candles(CROSS_UP, shift=3), dict(self.config, MAX_DAILY_LOSS=4.0))
        self.assertEqual(self.state["last_decision"], "skipped: daily loss limit hit (-5.00)")

    def test_ai_brain_learns_from_a_paper_trade(self):
        ai_config = dict(self.config, BRAIN="ai")
        decision = ai_strategy.Decision("buy", "AI: momentum", 0.02)
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide, \
                mock.patch.object(ai_strategy, "reflect", return_value=("Wait for a pullback.", None, 0.01)) as reflect:
            self.run_check(candles(CROSS_UP), ai_config)
            self.assertEqual(decide.call_args.args[2], "Your track record: no closed trades yet. Trade cautiously and build one.")
            self.run_check(self.stop_out(shift=2), ai_config)  # the first candle after the entry at TICK.time
        reflect.assert_called_once()
        (closed,) = self.journal.closed_trades()
        self.assertEqual(closed["lesson"], "Wait for a pullback.")
        self.assertEqual(self.journal.spend(source="bot"), 0.05)  # two decisions and one reflection
        self.assertTrue(any(notice.endswith("profit -5.00\nlesson: Wait for a pullback.") for notice in self.notices))
        # the brain bought again right after the stop-out; stop that one too, then the budget is gone
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide, \
                mock.patch.object(ai_strategy, "reflect", return_value=(None, "AI API error 500", 0.01)):
            self.run_check(self.stop_out(shift=3), dict(ai_config, AI_BUDGET_USD=0.06))
        decide.assert_not_called()
        self.assertEqual(len(self.journal.closed_trades()), 2)
        self.assertEqual(self.journal.spend(source="bot"), 0.06)
        self.assertIn("AI budget for today spent", self.state["last_decision"])


class SettleMt5Test(unittest.TestCase):
    POSITION = {"side": "buy", "lot": 0.01, "entry": 2650.5, "sl": 2645.5, "tp": 2660.5, "opened_at": TICK.time - 1800, "reason": "x"}

    def settle(self, positions, deals):
        journal = Journal()
        journal.open_trade("mt5", "t", "XAUUSD", self.POSITION, position_id=42)
        state = {"paused": False, "last_candle_time": -1, "journal": journal, "run": "t"}

        def deals_for(*args, **kwargs):
            return deals if kwargs.get("position") == 42 else ()

        with mock.patch.object(bot.mt5, "positions_get", return_value=positions), \
                mock.patch.object(bot.mt5, "history_deals_get", side_effect=deals_for), \
                mock.patch.object(bot, "notify"), mock.patch("builtins.print"):
            bot.settle_mt5(dict(CONFIG, MODE="demo"), state)
        return journal

    def test_closed_position_is_settled_from_the_brokers_deals(self):
        deals = [
            SimpleNamespace(entry=bot.mt5.DEAL_ENTRY_IN, time=TICK.time - 1800, price=2650.5, profit=0.0, commission=-0.1, swap=0.0, reason=bot.mt5.DEAL_REASON_EXPERT),
            SimpleNamespace(entry=bot.mt5.DEAL_ENTRY_OUT, time=TICK.time - 100, price=2645.5, profit=-5.0, commission=-0.1, swap=0.0, reason=bot.mt5.DEAL_REASON_SL),
        ]
        (trade,) = self.settle((), deals).closed_trades()
        self.assertEqual((trade["exit"], trade["profit"], trade["outcome"], trade["closed_at"]), (2645.5, -5.2, "sl", TICK.time - 100))

    def test_still_open_or_not_yet_in_history_waits(self):
        self.assertEqual(self.settle((SimpleNamespace(ticket=42),), ()).closed_trades(), [])
        self.assertEqual(self.settle((), ()).closed_trades(), [])


class ConnectionTest(unittest.TestCase):
    def test_lost_and_recovered_connection_are_reported_once_each(self):
        state = {}
        with mock.patch.object(bot.mt5, "terminal_info", return_value=None), \
                mock.patch.object(bot.mt5, "initialize", return_value=False) as initialize, \
                mock.patch.object(bot, "notify") as notify:
            self.assertFalse(bot.ensure_connected(CONFIG, state))
            self.assertFalse(bot.ensure_connected(CONFIG, state))
        self.assertEqual(notify.call_count, 1)
        self.assertIn("connection lost", notify.call_args.args[1])
        self.assertEqual(initialize.call_count, 2)
        with mock.patch.object(bot.mt5, "terminal_info", return_value=SimpleNamespace()), \
                mock.patch.object(bot, "notify") as notify:
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
        self.assertEqual(self.read(111, "/pause")[0], {"paused": True, "update_offset": 6})

    def test_stranger_is_ignored(self):
        self.assertFalse(self.read(999, "/pause")[0]["paused"])

    def test_backlog_is_dropped_at_startup(self):
        self.assertEqual(self.read(111, "/pause", skip_only=True)[0], {"paused": False, "update_offset": 6})

    def test_stop_asks_the_loop_to_end(self):
        state, notify = self.read(111, "/stop")
        self.assertTrue(state["stopping"])
        self.assertIn("watchdog will not restart", notify.call_args.args[1])

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
        self.assertEqual((config["SYMBOL"], config["LOT"], config["MODE"]), ("GOLD", 0.02, "dry"))
        self.assertEqual((config["AI_BUDGET_USD"], config["CONTRACT_SIZE"]), (5.0, 100.0))

    def test_bad_settings_stop_the_bot(self):
        for text in ("MODE=yolo", "BRAIN=robot", "STRATEGY=nope", "SL_ATR=-1", "SL_POINTS=0", "TIMEFRAME=M7", "FILLING=NOPE", "CONTRACT_SIZE=0", "AI_BUDGET_USD=-1"):
            with self.assertRaises(SystemExit, msg=text):
                self.load(text)


if __name__ == "__main__":
    unittest.main()
