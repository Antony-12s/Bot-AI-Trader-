import unittest

import journal

POSITION = {"side": "buy", "lot": 0.01, "entry": 2000.3, "sl": 1995.3, "tp": 2010.3, "opened_at": 1000, "reason": "trend"}


def closed(journal_, side, profit, outcome, closed_at, lesson=None):
    trade_id = journal_.open_trade("replay", "r1", "XAUUSD", dict(POSITION, side=side, opened_at=closed_at - 900))
    journal_.close_trade(trade_id, exit=2005.0, closed_at=closed_at, profit=profit, outcome=outcome)
    if lesson:
        journal_.add_lesson(trade_id, lesson)
    return trade_id


class TradeTest(unittest.TestCase):
    def setUp(self):
        self.journal = journal.Journal()

    def test_open_then_close_round_trip(self):
        trade_id = self.journal.open_trade("paper", "dry-1", "XAUUSD", POSITION, snapshot="rsi 70")
        self.assertEqual([t["id"] for t in self.journal.open_trades()], [trade_id])
        self.assertEqual(self.journal.open_trades(source="mt5"), [])
        trade = self.journal.close_trade(trade_id, exit=1995.3, closed_at=2800, profit=-5.004, outcome="sl")
        self.assertEqual((trade["profit"], trade["outcome"], trade["snapshot"]), (-5.0, "sl", "rsi 70"))
        self.assertEqual(self.journal.open_trades(), [])
        self.assertEqual(len(self.journal.closed_trades(source="paper")), 1)

    def test_profit_since_counts_only_trades_closed_in_the_window(self):
        closed(self.journal, "buy", -5.0, "sl", closed_at=100)
        closed(self.journal, "buy", 10.0, "tp", closed_at=200)
        self.assertEqual(self.journal.profit_since(150), 10.0)
        self.assertEqual(self.journal.profit_since(0), 5.0)
        self.assertEqual(self.journal.profit_since(0, source="paper"), 0.0)

    def test_spend_filters_by_source_run_and_time(self):
        self.journal.record_decision("bot", "dry-1", "decide", at=100, action="buy", reason="x", cost_usd=0.5)
        self.journal.record_decision("bot", "dry-1", "reflect", at=200, cost_usd=0.25)
        self.journal.record_decision("replay", "r1", "decide", at=300, action="hold", cost_usd=1.0)
        self.assertEqual(self.journal.spend(), 1.75)
        self.assertEqual(self.journal.spend(source="bot"), 0.75)
        self.assertEqual(self.journal.spend(run="r1"), 1.0)
        self.assertEqual(self.journal.spend(source="bot", since=150), 0.25)
        self.assertEqual(self.journal.decision_counts(), {"buy": 1, "hold": 1})

    def test_lessons_and_playbook(self):
        closed(self.journal, "sell", -5.0, "sl", closed_at=100, lesson="old")
        closed(self.journal, "buy", 10.0, "tp", closed_at=200, lesson="new")
        self.assertEqual([t["lesson"] for t in self.journal.recent_lessons(1)], ["new"])
        self.assertIsNone(self.journal.playbook())
        self.journal.save_playbook("1. do not short uptrends", trades_seen=2)
        self.journal.save_playbook("1. updated", trades_seen=12)
        self.assertEqual(self.journal.playbook()["text"], "1. updated")

    def test_experience_text(self):
        self.assertIn("no closed trades yet", self.journal.experience_text())
        closed(self.journal, "sell", -5.0, "sl", closed_at=100, lesson="shorted an uptrend")
        closed(self.journal, "buy", 10.0, "tp", closed_at=200)
        self.journal.save_playbook("1. trade with the trend", trades_seen=2)
        text = self.journal.experience_text()
        self.assertIn("2 closed trades, 1 wins / 1 losses (50% win rate), net +5.00, profit factor 2.00", text)
        self.assertIn("Sells: 1 trades, 0% wins, net -5.00.", text)
        self.assertIn("1. trade with the trend", text)
        self.assertIn("- (sell sl -5.00) shorted an uptrend", text)


class SummarizeTest(unittest.TestCase):
    def test_figures(self):
        trades = [
            {"id": 1, "side": "buy", "profit": 10.0, "outcome": "tp", "closed_at": 1},
            {"id": 2, "side": "buy", "profit": -5.0, "outcome": "sl", "closed_at": 2},
            {"id": 3, "side": "sell", "profit": -5.0, "outcome": "sl", "closed_at": 3},
            {"id": 4, "side": "sell", "profit": -5.0, "outcome": "sl", "closed_at": 4},
            {"id": 5, "side": "sell", "profit": 20.0, "outcome": "tp", "closed_at": 5},
        ]
        totals = journal.summarize(trades)
        self.assertEqual((totals["trades"], totals["wins"], totals["losses"]), (5, 2, 3))
        self.assertEqual((totals["net"], totals["gross_win"], totals["gross_loss"]), (15.0, 30.0, 15.0))
        self.assertEqual((totals["profit_factor"], totals["avg_win"], totals["avg_loss"]), (2.0, 15.0, -5.0))
        self.assertEqual((totals["max_drawdown"], totals["longest_losing_streak"]), (15.0, 3))
        self.assertEqual(totals["by_side"]["sell"], {"trades": 3, "wins": 1, "win_rate": 1 / 3, "net": 10.0})
        self.assertEqual(totals["outcomes"], {"tp": 2, "sl": 3})

    def test_no_losses_has_no_profit_factor(self):
        totals = journal.summarize([{"id": 1, "side": "buy", "profit": 3.0, "outcome": "tp", "closed_at": 1}])
        self.assertIsNone(totals["profit_factor"])
        self.assertEqual(journal.summarize([])["trades"], 0)


if __name__ == "__main__":
    unittest.main()
