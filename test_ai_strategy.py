import unittest
from types import SimpleNamespace
from unittest import mock

import ai_strategy

CONFIG = {"ANTHROPIC_API_KEY": "", "SYMBOL": "XAUUSD", "TIMEFRAME": "M15"}
CLOSES = [2000 + index * 0.5 for index in range(ai_strategy.CANDLES_NEEDED)]
USAGE = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0, cache_read_input_tokens=0)
TRADE = {
    "symbol": "XAUUSD", "side": "sell", "lot": 0.01, "entry": 2000.0, "sl": 2005.0, "tp": 1990.0,
    "opened_at": 0, "closed_at": 2700, "exit": 2005.0, "profit": -5.0, "outcome": "sl",
    "reason": "AI: lower highs", "snapshot": "rsi_14: 61, 63, 65",
}


def fake_call(reply_text, stop_reason="end_turn", error=None, usage=None):
    response = SimpleNamespace(stop_reason=stop_reason, usage=usage, content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=reply_text),
    ])
    patcher = mock.patch.object(ai_strategy.anthropic, "Anthropic")
    client_class = patcher.start()
    create = client_class.return_value.beta.messages.create
    create.return_value = response
    create.side_effect = error
    return patcher, create


class DecideTest(unittest.TestCase):
    def ask(self, reply_text='{"action": "buy", "reason": "trend up"}', experience="", **kwargs):
        patcher, create = fake_call(reply_text, **kwargs)
        try:
            return ai_strategy.decide(CLOSES, CONFIG, experience), create
        finally:
            patcher.stop()

    def test_decision_passes_through_with_its_reason(self):
        decision, create = self.ask()
        self.assertEqual(decision, ("buy", "AI: trend up", 0.0))
        request = create.call_args.kwargs
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["fallbacks"], "default")
        self.assertEqual(request["output_config"]["format"]["schema"], ai_strategy.DECISION_SCHEMA)
        self.assertIn("symbol: XAUUSD", request["messages"][0]["content"])
        self.assertIn("rsi_14: ", request["messages"][0]["content"])

    def test_experience_comes_before_the_market(self):
        _, create = self.ask(experience="Your track record: 3 trades")
        content = create.call_args.kwargs["messages"][0]["content"]
        self.assertTrue(content.startswith("Your track record: 3 trades\n\nMarket now:\n"))

    def test_hold_means_no_signal(self):
        decision, _ = self.ask('{"action": "hold", "reason": "choppy"}')
        self.assertEqual(decision, (None, "AI: choppy", 0.0))

    def test_cost_is_estimated_from_usage(self):
        decision, _ = self.ask(usage=USAGE)
        self.assertEqual(decision.cost_usd, 0.014)  # 1000 in at $4/M + 500 out at $20/M
        self.assertEqual(ai_strategy.cost_usd(None), 0.0)
        self.assertEqual(ai_strategy.cost_usd(mock.MagicMock()), 0.0)  # unknown shapes count as free

    def test_every_failure_holds_but_still_bills(self):
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
            decision, _ = self.ask(**dict({"usage": USAGE}, **failure))
            self.assertIsNone(decision.signal, msg=failure)
            self.assertEqual(decision.cost_usd, 0.0 if "error" in failure else 0.014, msg=failure)

    def test_too_few_candles_skips_the_api_call(self):
        with mock.patch.object(ai_strategy.anthropic, "Anthropic") as client_class:
            self.assertIsNone(ai_strategy.decide([1.0] * 10, CONFIG)[0])
        client_class.assert_not_called()


class ReviewTest(unittest.TestCase):
    def test_reflect_describes_the_trade_and_returns_the_lesson(self):
        patcher, create = fake_call('{"lesson": "Do not short while RSI climbs."}', usage=USAGE)
        try:
            lesson, problem, cost = ai_strategy.reflect(TRADE, [2001.0, 2003.0, 2005.0], CONFIG)
        finally:
            patcher.stop()
        self.assertEqual((lesson, problem, cost), ("Do not short while RSI climbs.", None, 0.014))
        content = create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Trade: sell 0.01 XAUUSD at 2000.0, stop 2005.0, target 1990.0.", content)
        self.assertIn("Your reason at entry: AI: lower highs", content)
        self.assertIn("rsi_14: 61, 63, 65", content)
        self.assertIn("Closes after entry, oldest first: 2001, 2003, 2005", content)
        self.assertIn("closed at 2005.0 after 3 candles by the stop loss, profit -5.00.", content)
        self.assertEqual(create.call_args.kwargs["output_config"]["effort"], "low")

    def test_reflect_failure_gives_no_lesson(self):
        patcher, _ = fake_call("garbage")
        try:
            lesson, problem, _ = ai_strategy.reflect(TRADE, [], CONFIG)
        finally:
            patcher.stop()
        self.assertIsNone(lesson)
        self.assertIn("unreadable", problem)

    def test_distill_rewrites_the_playbook(self):
        patcher, create = fake_call('{"playbook": "1. Trade with the trend.\\n2. Skip wide spreads."}')
        try:
            playbook, problem, _ = ai_strategy.distill("Your track record: ...", CONFIG)
        finally:
            patcher.stop()
        self.assertEqual(playbook, "1. Trade with the trend.\n2. Skip wide spreads.")
        self.assertIsNone(problem)
        self.assertTrue(create.call_args.kwargs["messages"][0]["content"].startswith("Your track record: ..."))


if __name__ == "__main__":
    unittest.main()


class LearnTest(unittest.TestCase):
    """review(): one lesson per closed trade, a new playbook every DISTILL_EVERY trades."""

    def setUp(self):
        import journal
        self.journal = journal.Journal()
        self.config = dict(CONFIG, AI_BUDGET_USD=5.0)

    def close_trades(self, count):
        position = {"side": "buy", "lot": 0.01, "entry": 2000.0, "sl": 1995.0, "tp": 2010.0, "opened_at": 0, "reason": "r"}
        trade = None
        for index in range(count):
            trade_id = self.journal.open_trade("replay", "r", "XAUUSD", dict(position, opened_at=index * 900))
            trade = self.journal.close_trade(trade_id, 1995.0, (index + 1) * 900, -5.0, "sl")
        return trade

    def test_lesson_is_stored_and_billed(self):
        trade = self.close_trades(1)
        with mock.patch.object(ai_strategy, "reflect", return_value=("Lesson.", None, 0.01)), \
                mock.patch.object(ai_strategy, "distill") as distill:
            lesson, playbook = ai_strategy.review(self.journal, trade, [2001.0], self.config, "replay", "r")
        self.assertEqual((lesson, playbook), ("Lesson.", None))
        distill.assert_not_called()
        self.assertEqual(self.journal.trade(trade["id"])["lesson"], "Lesson.")
        self.assertEqual(self.journal.spend(run="r"), 0.01)

    def test_playbook_is_rewritten_every_distill_every_trades(self):
        trade = self.close_trades(ai_strategy.DISTILL_EVERY)
        with mock.patch.object(ai_strategy, "reflect", return_value=(None, "AI API error 500", 0.0)), \
                mock.patch.object(ai_strategy, "distill", return_value=("1. Stop buying dips.", None, 0.05)) as distill:
            lesson, playbook = ai_strategy.review(self.journal, trade, [], self.config, "replay", "r")
        self.assertEqual((lesson, playbook), (None, "1. Stop buying dips."))
        self.assertIn("Your track record:", distill.call_args.args[0])
        self.assertEqual(self.journal.playbook()["trades_seen"], ai_strategy.DISTILL_EVERY)
        self.assertEqual(self.journal.spend(run="r"), 0.05)
        # the next closed trade does not trigger another rewrite
        trade = self.close_trades(1)
        with mock.patch.object(ai_strategy, "reflect", return_value=("L", None, 0.0)), \
                mock.patch.object(ai_strategy, "distill") as distill:
            ai_strategy.review(self.journal, trade, [], self.config, "replay", "r")
        distill.assert_not_called()

    def test_spent_budget_skips_the_review(self):
        trade = self.close_trades(1)
        with mock.patch.object(ai_strategy, "reflect") as reflect:
            result = ai_strategy.review(self.journal, trade, [], self.config, "replay", "r", spent=5.0)
        self.assertEqual(result, (None, None))
        reflect.assert_not_called()
