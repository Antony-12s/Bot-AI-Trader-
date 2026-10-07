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
        agent = agents.clean(dict(GOOD, strategy="Only when RSI is between 10 and 20."))
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
