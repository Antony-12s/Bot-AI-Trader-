"""Run: python -m unittest   (no MT5 terminal needed, every MT5 call is faked)"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ai_strategy
import bot
import indicators
import strategy

CONFIG = {
    "MODE": "demo",
    "BRAIN": "rules",
    "ANTHROPIC_API_KEY": "",
    "SYMBOL": "XAUUSD",
    "TIMEFRAME": "M15",
    "LOT": 0.01,
    "SL_POINTS": 500,
    "TP_POINTS": 1000,
    "MAX_DAILY_LOSS": 20.0,
    "MAX_SPREAD_POINTS": 50,
    "MAGIC": 7,
    "FILLING": "IOC",
    "TELEGRAM_TOKEN": "token",
    "TELEGRAM_CHAT_ID": "111",
}
TICK = SimpleNamespace(ask=2650.50, bid=2650.20, time=10 * bot.SECONDS_PER_DAY + 3600)
GOLD = SimpleNamespace(point=0.01, digits=2)
DEMO_ACCOUNT = SimpleNamespace(trade_mode=bot.mt5.ACCOUNT_TRADE_MODE_DEMO)
REAL_ACCOUNT = SimpleNamespace(trade_mode=bot.mt5.ACCOUNT_TRADE_MODE_REAL)


def candles(closes):
    return [{"time": index, "close": close} for index, close in enumerate(closes)]


class StrategyTest(unittest.TestCase):
    def test_cross_up_buys_cross_down_sells(self):
        self.assertEqual(strategy.decide([100] * 30 + [110])[0], "buy")
        self.assertEqual(strategy.decide([100] * 30 + [90])[0], "sell")

    def test_no_cross_or_too_little_data_gives_nothing(self):
        self.assertIsNone(strategy.decide([100] * 31)[0])
        self.assertIsNone(strategy.decide([100, 110])[0])


class AiBrainTest(unittest.TestCase):
    CLOSES = [2000 + index * 0.5 for index in range(ai_strategy.CANDLES_NEEDED)]

    def ask(self, reply_text='{"action": "buy", "reason": "trend up"}', stop_reason="end_turn", error=None):
        response = SimpleNamespace(stop_reason=stop_reason, content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=reply_text),
        ])
        with mock.patch.object(ai_strategy.anthropic, "Anthropic") as client_class:
            create = client_class.return_value.beta.messages.create
            create.return_value = response
            create.side_effect = error
            return ai_strategy.decide(self.CLOSES, CONFIG), create

    def test_decision_passes_through_with_its_reason(self):
        decision, create = self.ask()
        self.assertEqual(decision, ("buy", "AI: trend up"))
        request = create.call_args.kwargs
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertIn("symbol: XAUUSD", request["messages"][0]["content"])
        self.assertIn("rsi_14: ", request["messages"][0]["content"])

    def test_hold_means_no_signal(self):
        decision, _ = self.ask('{"action": "hold", "reason": "choppy"}')
        self.assertEqual(decision, (None, "AI: choppy"))

    def test_every_failure_holds(self):
        api_error = ai_strategy.anthropic.APIConnectionError
        failures = (
            {"stop_reason": "refusal"},
            {"stop_reason": "max_tokens"},
            {"reply_text": "not json"},
            {"reply_text": '{"action": "buy"}'},
            {"error": api_error.__new__(api_error)},
            {"error": TypeError("no credentials")},
        )
        for failure in failures:
            self.assertIsNone(self.ask(**failure)[0][0], msg=failure)

    def test_too_few_candles_skips_the_api_call(self):
        with mock.patch.object(ai_strategy.anthropic, "Anthropic") as client_class:
            self.assertIsNone(ai_strategy.decide([1.0] * 10, CONFIG)[0])
        client_class.assert_not_called()


class IndicatorTest(unittest.TestCase):
    def test_sma_and_ema_line_up_with_the_last_price(self):
        self.assertEqual(indicators.sma([1, 2, 3, 4, 5], 3), [2, 3, 4])
        self.assertEqual(indicators.ema([1, 2, 3, 4, 5], 3), [2, 3, 4])  # seed 2, weight 0.5
        self.assertEqual(indicators.ema([1, 2], 3), [])

    def test_rsi_hand_computed(self):
        # changes +1 -1 +1: first window gain 0.5 / loss 0.5, then gain 0.75 / loss 0.25
        self.assertEqual(indicators.rsi([1, 2, 1, 2], 2), [50.0, 75.0])
        self.assertEqual(indicators.rsi([1, 2, 3, 4], 2), [100.0, 100.0])
        self.assertEqual(indicators.rsi([4, 3, 2, 1], 2), [0.0, 0.0])
        self.assertEqual(indicators.rsi([5, 5, 5, 5], 2), [50.0, 50.0])
        self.assertEqual(indicators.rsi([1, 2], 2), [])

    def test_macd_lengths_and_direction(self):
        macd_line, signal_line, histogram = indicators.macd(list(range(40)))
        self.assertEqual((len(macd_line), len(signal_line), len(histogram)), (15, 7, 7))
        self.assertTrue(all(value > 0 for value in macd_line))  # steady rise: fast above slow
        self.assertEqual(indicators.macd([7.0] * 40)[2], [0.0] * 7)
        self.assertEqual(indicators.macd([1, 2, 3]), ([], [], []))

    def test_bollinger_bands(self):
        lower, middle, upper = indicators.bollinger([1, 2, 3, 4, 5], period=5, deviations=2)
        self.assertEqual(middle, [3])
        self.assertAlmostEqual(lower[0], 3 - 2 * 2 ** 0.5)
        self.assertAlmostEqual(upper[0], 3 + 2 * 2 ** 0.5)
        self.assertEqual(indicators.bollinger([9.0] * 6, period=5), ([9.0, 9.0],) * 3)


class RiskTest(unittest.TestCase):
    def test_block_reasons(self):
        self.assertIsNone(bot.block_reason(False, 0, 0.0, 30, CONFIG))
        self.assertEqual(bot.block_reason(True, 0, 0.0, 30, CONFIG), "paused")
        self.assertIn("already open", bot.block_reason(False, 1, 0.0, 30, CONFIG))
        self.assertIn("daily loss", bot.block_reason(False, 0, -20.0, 30, CONFIG))
        self.assertIn("spread", bot.block_reason(False, 0, 0.0, 51, CONFIG))

    def test_demo_mode_refuses_real_account(self):
        self.assertIsNotNone(bot.account_error("demo", is_demo_account=False))
        self.assertIsNone(bot.account_error("demo", is_demo_account=True))
        self.assertIsNone(bot.account_error("live", is_demo_account=False))

    def test_pnl_today_skips_deposits_and_yesterday(self):
        today = TICK.time - 60
        yesterday = TICK.time - bot.SECONDS_PER_DAY
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

    def place(self, mode, account, retcode=bot.mt5.TRADE_RETCODE_DONE):
        result = SimpleNamespace(retcode=retcode, comment="fake")
        with mock.patch.object(bot.mt5, "account_info", return_value=account), \
                mock.patch.object(bot.mt5, "order_send", return_value=result) as order_send, \
                mock.patch.object(bot, "notify") as notify:
            try:
                bot.place_order("buy", TICK, GOLD, dict(CONFIG, MODE=mode))
            except SystemExit:
                return order_send, notify, True
        return order_send, notify, False

    def test_dry_mode_never_sends_an_order(self):
        order_send, notify, _ = self.place("dry", REAL_ACCOUNT)
        order_send.assert_not_called()
        self.assertIn("[dry]", notify.call_args.args[1])

    def test_demo_mode_on_real_account_stops_without_ordering(self):
        order_send, _, exited = self.place("demo", REAL_ACCOUNT)
        order_send.assert_not_called()
        self.assertTrue(exited)

    def test_demo_mode_on_unknown_account_stops_without_ordering(self):
        order_send, _, exited = self.place("demo", None)
        order_send.assert_not_called()
        self.assertTrue(exited)

    def test_demo_mode_on_demo_account_sends_once(self):
        order_send, notify, _ = self.place("demo", DEMO_ACCOUNT)
        order_send.assert_called_once()
        self.assertIn("opened", notify.call_args.args[1])

    def test_rejected_order_is_reported_not_retried(self):
        order_send, notify, _ = self.place("demo", DEMO_ACCOUNT, retcode=10027)
        order_send.assert_called_once()
        self.assertIn("FAILED", notify.call_args.args[1])


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
        cross_up = candles([100] * 30 + [110, 110])  # last row = forming candle
        self.run_check(cross_up, state).assert_not_called()  # closed before start
        self.run_check(cross_up, state).assert_not_called()  # same candle again
        state["last_candle_time"] -= 1  # pretend that candle just closed
        self.assertEqual(self.run_check(cross_up, state).call_args.args[0], "buy")

    def test_paused_bot_does_not_order(self):
        state = {"paused": True, "last_candle_time": -1}
        self.run_check(candles([100] * 30 + [110, 110]), state).assert_not_called()
        self.assertEqual(state["last_decision"], "skipped: paused")

    def test_ai_brain_is_asked_only_when_a_trade_is_allowed(self):
        ai_config = dict(CONFIG, BRAIN="ai")
        rates = candles([100.0] * 5)
        with mock.patch.object(bot.ai_strategy, "decide", return_value=("sell", "AI: weak")) as decide:
            self.run_check(rates, {"paused": True, "last_candle_time": -1}, ai_config).assert_not_called()
            decide.assert_not_called()  # a blocked candle must not cost an API call
            place_order = self.run_check(rates, {"paused": False, "last_candle_time": -1}, ai_config)
        decide.assert_called_once()
        self.assertEqual((place_order.call_args.args[0], place_order.call_args.args[4]), ("sell", "AI: weak"))


class TelegramTest(unittest.TestCase):
    def read(self, chat_id, text, **kwargs):
        update = {"update_id": 5, "message": {"chat": {"id": chat_id}, "text": text}}
        state = {"paused": False, "update_offset": 0}
        with mock.patch.object(bot, "telegram", return_value=[update]), \
                mock.patch("builtins.print"):
            bot.read_commands(CONFIG, state, **kwargs)
        return state

    def test_owner_can_pause(self):
        self.assertEqual(self.read(111, "/pause"), {"paused": True, "update_offset": 6})

    def test_stranger_is_ignored(self):
        self.assertFalse(self.read(999, "/pause")["paused"])

    def test_backlog_is_dropped_at_startup(self):
        self.assertEqual(self.read(111, "/pause", skip_only=True), {"paused": False, "update_offset": 6})


class ConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            env_path = Path(folder, ".env")
            env_path.write_text(text, encoding="utf-8")
            return bot.load_config(env_path)

    def test_env_overrides_defaults_and_converts_numbers(self):
        config = self.load("# comment\nSYMBOL=GOLD\nLOT = 0.02\n")
        self.assertEqual((config["SYMBOL"], config["LOT"], config["MODE"]), ("GOLD", 0.02, "dry"))

    def test_bad_settings_stop_the_bot(self):
        for text in ("MODE=yolo", "BRAIN=robot", "SL_POINTS=0", "TIMEFRAME=M7", "FILLING=NOPE"):
            with self.assertRaises(SystemExit, msg=text):
                self.load(text)


if __name__ == "__main__":
    unittest.main()
