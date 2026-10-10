import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agents
import bot
from journal import Journal
from test_bot import CANDLE, CONFIG, CROSS_UP, FLAT, GOLD, TICK, candles

GOOD = {"name": "Gold dip", "template": "ma_cross", "markets": ["XAUUSD", "EURUSD"], "timeframe": "M15",
        "runs": "candle", "min_confidence": 60, "mode": "watch", "analyst": "rules"}


class CleanTest(unittest.TestCase):
    def test_a_good_agent_gets_an_id_and_keeps_market_order(self):
        agent = agents.clean(dict(GOOD, markets=["EURUSD", "XAUUSD", "EURUSD"]))
        self.assertEqual(agent["markets"], ["EURUSD", "XAUUSD"])
        self.assertEqual(len(agent["id"]), 8)

    def test_says_what_to_fix(self):
        cases = {
            "name": dict(GOOD, name=" "),
            "market": dict(GOOD, markets=[]),
            "AI analyst": dict(GOOD, template="", strategy="buy dips", analyst="rules"),
            "describe": dict(GOOD, template="", strategy="", analyst="saved"),
            "timer": dict(GOOD, runs="timer"),
            "confidence": dict(GOOD, min_confidence=101),
            "watch, suggest or auto": dict(GOOD, mode="yolo"),
            "unknown template": dict(GOOD, template="nope"),
        }
        for expected, raw in cases.items():
            with self.assertRaises(ValueError, msg=expected) as caught:
                agents.clean(raw)
            self.assertIn(expected, str(caught.exception))

    def test_store_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agents.json")
            self.assertEqual(agents.load(path), [])
            agents.store([agents.clean(GOOD)], path)
            self.assertEqual(agents.load(path)[0]["name"], "Gold dip")


class BuiltInAnalystTest(unittest.TestCase):
    def test_unchanged_template_runs_its_code_and_edited_words_are_read(self):
        text = agents.strategies.PLAIN["trend_pullback"][2]
        self.assertTrue(agents.runs_template({"template": "trend_pullback", "strategy": text}))
        self.assertFalse(agents.runs_template({"template": "trend_pullback", "strategy": text + " Only on Mondays."}))
        written = agents.clean(dict(GOOD, template="", strategy="Buy when the 10-candle average crosses above the 30-candle average."))
        signal, confidence, reason, cost = agents.decide(written, candles(CROSS_UP)[:-1], CONFIG)
        self.assertEqual((signal, confidence, reason, cost), ("buy", 100, "rules: SMA(10) crosses above SMA(30)", 0.0))

    def test_rules_it_cannot_read_are_refused_with_what_to_fix(self):
        with self.assertRaises(ValueError) as caught:
            agents.clean(dict(GOOD, template="", strategy="Buy when the moon is full."))
        self.assertIn("not understood: the moon is full", str(caught.exception))


class LearnAndBrakeTest(unittest.TestCase):
    position = {"side": "buy", "lot": 0.01, "entry": 1.0, "sl": 0.9, "tp": 1.2}

    def close(self, journal, brain, profit, at):
        trade_id = journal.open_trade("paper", "r", "GOLD", dict(self.position, opened_at=at), brain=brain)
        return journal.close_trade(trade_id, 1.1, at + 60, profit, "sl" if profit < 0 else "tp")

    def test_auto_agent_pauses_after_losses_in_a_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agents.json")
            agent = agents.clean(dict(GOOD, mode="auto", brake_losses=2))
            other = agents.clean(dict(GOOD, name="Other", mode="auto"))
            agents.store([agent, other], path)
            journal, source = Journal(), "agent:" + agent["id"]
            self.close(journal, source, -1.0, 100)
            self.close(journal, "agent:" + other["id"], -1.0, 150)  # someone else's loss does not count
            trade = self.close(journal, source, 2.0, 200)
            self.assertEqual(agents.after_trade(agent, trade, journal, CONFIG, [], "r", store_path=path), [])  # a win resets it
            self.close(journal, source, -1.0, 300)
            trade = self.close(journal, source, -1.0, 400)
            (note,) = agents.after_trade(agent, trade, journal, CONFIG, [], "r", store_path=path)
            self.assertIn("lost 2 trades in a row: switched to Watch only", note)
            self.assertEqual([a["mode"] for a in agents.load(path)], ["watch", "auto"])
            (row,) = journal._rows("SELECT action FROM decisions WHERE kind = 'agent'")
            self.assertEqual(row["action"], "pause")
        with self.assertRaisesRegex(ValueError, "20 losses"):
            agents.clean(dict(GOOD, brake_losses=21))
        self.assertEqual(agents.clean(dict(GOOD, brake_losses=0))["brake_losses"], 0)  # 0 = never pause

    def test_ai_agent_writes_a_lesson_and_reads_its_own_record(self):
        agent = agents.clean(dict(GOOD, analyst="claude", brake_losses=0))
        journal, source = Journal(), "agent:" + agent["id"]
        trade = self.close(journal, source, -1.0, 100)
        self.close(journal, "rules", -5.0, 200)  # not this agent's
        with mock.patch.object(agents.ai_strategy, "reflect", return_value=("Wait for the close.", None, 0.01)) as reflect:
            notes = agents.after_trade(agent, trade, journal, dict(CONFIG, AI_BUDGET_USD=5.0), [1.0, 0.9], "r")
        self.assertEqual(reflect.call_args.args[2]["AI_PROVIDER"], "claude")
        self.assertEqual(notes, ["lesson: Wait for the close."])
        experience = journal.experience_text(brains=(source,))
        self.assertIn("1 closed trades", experience)
        self.assertIn("Wait for the close.", experience)
        with mock.patch.object(agents.ai_strategy, "ask", return_value=({"action": "hold", "confidence": 0, "reason": "x"}, None, 0.0)) as ask:
            agents.decide(agent, candles(FLAT)[:-1], dict(CONFIG, AI_BUDGET_USD=5.0), experience=experience)
        self.assertIn("Wait for the close.", ask.call_args.args[1])
        rules_agent = agents.clean(GOOD)
        with mock.patch.object(agents.ai_strategy, "reflect") as reflect:
            agents.after_trade(rules_agent, trade, journal, CONFIG, [], "r")
        reflect.assert_not_called()  # the free built-in analyst has nothing to learn with


class BotHandsClosedTradesToTheirAgentTest(unittest.TestCase):
    def test_finish_trade_runs_after_trade_and_tells_the_owner(self):
        agent = agents.clean(dict(GOOD, mode="auto"))
        journal, notices = Journal(), []
        trade_id = journal.open_trade("mt5", "r", "XAUUSD", {"side": "buy", "lot": 0.01, "entry": 1.0, "sl": 0.9, "tp": 1.2,
                                                            "opened_at": 100}, brain="agent:" + agent["id"])
        with mock.patch.object(bot.agents, "load", return_value=[agent]), \
                mock.patch.object(bot.agents, "after_trade", return_value=["Agent Gold dip paused."]) as after, \
                mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=None), \
                mock.patch.object(bot, "notify", side_effect=lambda config, text: notices.append(text)):
            bot.finish_trade(CONFIG, {"journal": journal, "run": "r"}, trade_id, 0.9, 1000, -1.0, "sl")
        self.assertEqual(after.call_args.args[0]["id"], agent["id"])
        self.assertTrue(notices[0].endswith("\nAgent Gold dip paused."))


class MigrateTest(unittest.TestCase):
    def test_armed_strategies_become_auto_agents_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agents.json")
            agents.store([agents.clean(GOOD)], path)  # one the user made already stays
            values = dict(SYMBOL="GOLD", TIMEFRAME="M15", BRAIN="rules", STRATEGY="ma_cross,rsi_reversion")
            made = agents.migrate(values, path)
            self.assertEqual([(a["template"], a["markets"], a["mode"], a["analyst"]) for a in made],
                             [("ma_cross", ["GOLD"], "auto", "rules"), ("rsi_reversion", ["GOLD"], "auto", "rules")])
            self.assertTrue(all(agents.runs_template(a) for a in made))  # they trade exactly as before
            self.assertEqual(len(agents.load(path)), 3)
            self.assertEqual(agents.migrate(values, path), [])  # only once
            self.assertEqual(len(agents.load(path)), 3)

    def test_fresh_install_and_ai_brain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agents.json")
            self.assertEqual(agents.migrate(None, path), [])  # no .env yet: nothing to move, and never later
            self.assertFalse(path.exists())
            self.assertEqual(agents.migrate(dict(SYMBOL="GOLD", TIMEFRAME="M15", STRATEGY="ma_cross"), path), [])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "agents.json")
            (made,) = agents.migrate(dict(SYMBOL="GOLD", TIMEFRAME="H1", BRAIN="hybrid", STRATEGY="ma_cross"), path)
            self.assertEqual((made["analyst"], made["timeframe"]), ("saved", "H1"))  # the AI still judges the setups


class RsiRangeTest(unittest.TestCase):
    def test_reads_the_ranges_people_write(self):
        self.assertEqual(agents.rsi_range("Buy when EMA(9) crosses EMA(21) and RSI is between 45 and 65."), (45, 65))
        self.assertEqual(agents.rsi_range("rsi 30-70 only"), (30, 70))
        self.assertEqual(agents.rsi_range("RSI between 65 and 45"), (45, 65))
        self.assertIsNone(agents.rsi_range("Buy when RSI drops below 30"))
        self.assertIsNone(agents.rsi_range("no indicator here"))

    def test_a_range_binds_only_the_side_its_sentence_is_about(self):
        template = ("Buy when the 50-candle average is rising, with RSI between 40 and 65. Sell on the mirror image.")
        self.assertEqual(agents.rsi_ranges(template), {"buy": (40, 65), "sell": None})
        both = "Only trade when RSI is between 30 and 70. Buy dips, sell rips."
        self.assertEqual(agents.rsi_ranges(both), {"buy": (30, 70), "sell": (30, 70)})
        split = "Buy when RSI is between 40 and 65; sell when RSI is between 35 and 60."
        self.assertEqual(agents.rsi_ranges(split), {"buy": (40, 65), "sell": (35, 60)})


class DecideTest(unittest.TestCase):
    config = dict(CONFIG, AI_PROVIDER="claude", AI_BUDGET_USD=5.0)

    def test_rules_template_fires_with_full_confidence(self):
        signal, confidence, reason, cost = agents.decide(agents.clean(GOOD), candles(CROSS_UP)[:-1], self.config)
        self.assertEqual((signal, confidence, cost), ("buy", 100, 0.0))
        self.assertTrue(reason.startswith("ma_cross"))

    def test_rsi_range_in_the_text_is_enforced(self):
        agent = agents.clean(dict(GOOD, strategy="Buy strength. Only when RSI is between 10 and 20.", analyst="claude"))
        with mock.patch.object(agents.ai_strategy, "ask", return_value=({"action": "buy", "confidence": 90, "reason": "up"}, None, 0.01)):
            signal, _, reason, _ = agents.decide(agent, candles(CROSS_UP)[:-1], self.config)
        self.assertIsNone(signal)
        self.assertIn("outside your 10-20", reason)

    def test_ai_answer_under_the_minimum_confidence_is_held(self):
        agent = agents.clean(dict(GOOD, template="", strategy="Buy strength.", analyst="openai", min_confidence=70))
        with mock.patch.object(agents.ai_strategy, "ask", return_value=({"action": "buy", "confidence": 55, "reason": "maybe"}, None, 0.01)) as ask:
            signal, confidence, reason, cost = agents.decide(agent, candles(FLAT * 10)[:-1], self.config)
        self.assertEqual((signal, confidence, cost), (None, 55, 0.01))
        self.assertIn("under 70", reason)
        self.assertEqual(ask.call_args.args[3]["AI_PROVIDER"], "openai")  # the agent's own analyst, not Settings'
        self.assertIn("Buy strength.", ask.call_args.args[1])

    def test_spent_budget_skips_the_ai_call(self):
        agent = agents.clean(dict(GOOD, template="", strategy="x", analyst="saved"))
        with mock.patch.object(agents.ai_strategy, "ask") as ask:
            signal, _, reason, _ = agents.decide(agent, candles(FLAT)[:-1], self.config, journal_spent=5.0)
        ask.assert_not_called()
        self.assertIn("budget", reason)


class RunAgentsTest(unittest.TestCase):
    """bot.run_agents with MT5 faked: two markets, each with its own prices."""

    def setUp(self):
        self.journal = Journal()
        self.config = dict(CONFIG, MODE="dry", SYMBOL="XAUUSD")
        self.state = {"paused": False, "journal": self.journal, "run": "t", "agent_seen": {}, "settle_seen": {}}
        self.notices = []

    def run_loop(self, agent_list, rates_by_market):
        eur = SimpleNamespace(point=0.0001, digits=5, trade_contract_size=100000)
        eur_tick = SimpleNamespace(ask=1.10010, bid=1.10000, time=TICK.time)
        info = {"XAUUSD": GOLD, "EURUSD": eur}
        ticks = {"XAUUSD": TICK, "EURUSD": eur_tick}
        with mock.patch.object(bot.agents, "load", return_value=agent_list), \
                mock.patch.object(bot.mt5, "symbol_select", create=True), \
                mock.patch.object(bot.mt5, "copy_rates_from_pos", side_effect=lambda m, f, s, n: rates_by_market[m]), \
                mock.patch.object(bot.mt5, "symbol_info", side_effect=lambda m: info[m]), \
                mock.patch.object(bot.mt5, "symbol_info_tick", side_effect=lambda m: ticks[m]), \
                mock.patch.object(bot.mt5, "positions_get", return_value=()), \
                mock.patch.object(bot.mt5, "history_deals_get", return_value=()), \
                mock.patch("builtins.print"), \
                mock.patch.object(bot, "notify", side_effect=lambda config, text: self.notices.append(text)):
            bot.run_agents(self.config, self.state)

    def test_watch_records_on_new_candles_only_and_never_trades(self):
        agent = agents.clean(dict(GOOD, markets=["XAUUSD"]))
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP)})  # first look: the candle closed before we started
        self.assertEqual(self.journal._rows("SELECT * FROM decisions"), [])
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP, shift=1)})
        (row,) = self.journal._rows("SELECT source, kind, action, reason FROM decisions")
        self.assertEqual((row["source"], row["kind"]), ("agent:" + agent["id"], "agent"))
        self.assertTrue(row["reason"].startswith("XAUUSD · 100%"))
        self.assertEqual(self.journal.open_trades(), [])

    def test_an_agent_paused_by_this_candles_settle_does_not_enter_on_it(self):
        agent = agents.clean(dict(GOOD, markets=["XAUUSD"], mode="auto"))
        paused = dict(agent, mode="watch")  # what agents.json says once that settle's loss tripped the brake
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP)})  # first look
        with mock.patch.object(agents, "load", side_effect=[[agent], [paused]]):
            with mock.patch.object(bot.mt5, "symbol_select", create=True), \
                    mock.patch.object(bot.mt5, "copy_rates_from_pos", return_value=candles(CROSS_UP, shift=1)), \
                    mock.patch.object(bot.mt5, "symbol_info", return_value=GOLD), \
                    mock.patch.object(bot.mt5, "symbol_info_tick", return_value=TICK), \
                    mock.patch.object(bot.mt5, "positions_get", return_value=()), \
                    mock.patch.object(bot.mt5, "history_deals_get", return_value=()), \
                    mock.patch("builtins.print"), mock.patch.object(bot, "notify"):
                bot.run_agents(self.config, self.state)
        self.assertEqual(self.journal.open_trades(), [])  # the cross still signalled, but it only watches now

    def test_suggest_tells_the_owner_and_never_trades(self):
        agent = agents.clean(dict(GOOD, markets=["XAUUSD"], mode="suggest"))
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP)})
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP, shift=1)})
        self.assertTrue(any("suggests BUY XAUUSD" in notice for notice in self.notices))
        self.assertEqual(self.journal.open_trades(), [])

    def test_auto_trades_each_market_under_its_own_prices_and_risk_slot(self):
        agent = agents.clean(dict(GOOD, mode="auto"))
        eur_up = [dict(c, open=c["close"] / 2400, high=c["close"] / 2400, low=c["close"] / 2400, close=c["close"] / 2400)
                  for c in candles(CROSS_UP)]
        eur_up2 = [dict(c, open=c["close"] / 2400, high=c["close"] / 2400, low=c["close"] / 2400, close=c["close"] / 2400)
                   for c in candles(CROSS_UP, shift=1)]
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP), "EURUSD": eur_up})
        self.run_loop([agent], {"XAUUSD": candles(CROSS_UP, shift=1), "EURUSD": eur_up2})
        trades = {t["symbol"]: t for t in self.journal.open_trades("paper")}
        self.assertEqual(set(trades), {"XAUUSD", "EURUSD"})  # one position per market, not one in total
        self.assertEqual(trades["EURUSD"]["brain"], "agent:" + agent["id"])
        self.assertAlmostEqual(trades["EURUSD"]["entry"], 1.10010)  # EURUSD's own ask, not gold's
        # a gold candle that crashes through EURUSD's stop price must not close the EURUSD trade
        gold_crash = candles(FLAT, shift=2)
        gold_crash[-2]["low"] = 1.0
        self.run_loop([agent], {"XAUUSD": gold_crash, "EURUSD": [dict(c, low=c["close"]) for c in eur_up2]})
        self.assertIn("EURUSD", {t["symbol"] for t in self.journal.open_trades("paper")})


if __name__ == "__main__":
    unittest.main()
