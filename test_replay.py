import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_strategy
import export_history
import replay
import report
import strategies
from journal import Journal

CONFIG = {
    "BRAIN": "rules", "STRATEGY": "ma_cross", "ANTHROPIC_API_KEY": "", "AI_BUDGET_USD": 5.0, "SYMBOL": "XAUUSD",
    "TIMEFRAME": "M15", "LOT": 0.01, "CONTRACT_SIZE": 100.0, "SL_ATR": 0.0, "TP_ATR": 0.0, "ATR_PERIOD": 14,
    "SL_POINTS": 500, "TP_POINTS": 1000, "MAX_DAILY_LOSS": 20.0, "MAX_SPREAD_POINTS": 50,
}
START = 1_700_000_000


def candle(index, close, high=None, low=None, open_=None, spread=30):
    return {
        "time": START + index * 900, "open": open_ if open_ is not None else close,
        "high": high if high is not None else close, "low": low if low is not None else close,
        "close": close, "spread": spread,
    }


def flat_then_breakout(length=None, needed=strategies.CANDLES_NEEDED):
    """Flat at 2000, one candle up to 2010 (MA cross), the next candle runs to the target."""
    length = length or needed + 60
    candles = [candle(index, 2000.0) for index in range(length)]
    jump = needed + 5
    candles[jump] = candle(jump, 2010.0, high=2010.0, low=2000.0)
    candles[jump + 1] = candle(jump + 1, 2024.0, open_=2010.0, high=2025.0, low=2009.0)
    for index in range(jump + 2, length):
        candles[index] = candle(index, 2024.0)
    return candles, jump


class ReplayTest(unittest.TestCase):
    def test_rules_brain_breakout_hits_the_target(self):
        candles, jump = flat_then_breakout()
        journal = Journal()
        log = []
        stopped = replay.replay(candles, CONFIG, journal, "r1", digits=2, log=log.append)
        self.assertEqual(stopped, "end")
        (trade,) = journal.closed_trades(run="r1")
        self.assertEqual((trade["side"], trade["entry"], trade["sl"], trade["tp"]), ("buy", 2010.30, 2005.30, 2020.30))
        self.assertEqual((trade["opened_at"], trade["closed_at"]), (candles[jump + 1]["time"], candles[jump + 1]["time"] + 900))
        self.assertEqual((trade["exit"], trade["profit"], trade["outcome"]), (2020.30, 10.0, "tp"))
        self.assertIn("symbol: XAUUSD", trade["snapshot"])
        self.assertEqual(journal.decision_counts(run="r1")["buy"], 1)
        self.assertEqual(journal.spend(run="r1"), 0.0)
        self.assertTrue(any("closed buy @ 2020.3 by tp, profit +10.00" in line for line in log))
        text = report.format_report(journal, run="r1")
        self.assertIn("trades: 1 (1 wins, 0 losses, win rate 100%)", text)
        self.assertIn("net: +10.00", text)

    def test_position_left_open_at_the_end_is_booked_at_the_last_close(self):
        candles, jump = flat_then_breakout(length=strategies.CANDLES_NEEDED + 7)
        candles[-1] = candle(len(candles) - 1, 2012.0, open_=2010.0, high=2013.0, low=2009.0)  # never reaches 2020.30
        journal = Journal()
        replay.replay(candles, CONFIG, journal, "r2", digits=2, log=lambda line: None)
        (trade,) = journal.closed_trades(run="r2")
        self.assertEqual((trade["outcome"], trade["exit"], trade["profit"]), ("closed", 2012.0, 1.7))
        self.assertEqual(journal.open_trades(), [])

    def test_ai_brain_stops_at_the_budget_and_learns_from_each_trade(self):
        candles, _ = flat_then_breakout(needed=ai_strategy.CANDLES_NEEDED)
        journal = Journal()
        config = dict(CONFIG, BRAIN="ai", AI_BUDGET_USD=2.5)
        decision = ai_strategy.Decision(None, "AI: waiting", 1.0)
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide:
            stopped = replay.replay(candles, config, journal, "r3", digits=2, log=lambda line: None)
        self.assertEqual((stopped, decide.call_count), ("budget", 3))
        self.assertEqual(journal.spend(run="r3"), 3.0)

        journal = Journal()
        decisions = iter([ai_strategy.Decision("sell", "AI: fading", 0.1)] + [ai_strategy.Decision(None, "AI: no", 0.1)] * 100)
        with mock.patch.object(ai_strategy, "decide", side_effect=lambda *args: next(decisions)), \
                mock.patch.object(ai_strategy, "reflect", return_value=("Never fade a breakout.", None, 0.05)):
            replay.replay(candles, dict(CONFIG, BRAIN="ai"), journal, "r4", digits=2, log=lambda line: None)
        (trade,) = journal.closed_trades(run="r4")  # sold the flat market, stopped out by the breakout candle
        self.assertEqual((trade["side"], trade["outcome"], trade["lesson"]), ("sell", "sl", "Never fade a breakout."))
        self.assertEqual((trade["entry"], trade["exit"], trade["profit"], trade["brain"]), (2000.0, 2005.0, -5.0, "ai"))
        self.assertIn("1 closed trades", journal.experience_text())

    def test_hybrid_asks_only_when_a_setup_fires(self):
        candles, jump = flat_then_breakout(needed=ai_strategy.CANDLES_NEEDED)
        journal = Journal()
        decision = ai_strategy.Decision("buy", "AI: taking the cross", 0.2)
        with mock.patch.object(ai_strategy, "decide", return_value=decision) as decide, \
                mock.patch.object(ai_strategy, "reflect", return_value=("Fine trade.", None, 0.05)):
            replay.replay(candles, dict(CONFIG, BRAIN="hybrid"), journal, "r6", digits=2, log=lambda line: None)
        self.assertEqual(decide.call_count, 1)  # one MA cross in the whole file
        self.assertEqual(decide.call_args.kwargs["candidates"], [("ma_cross", "buy", "fast MA crossed above slow MA")])
        (trade,) = journal.closed_trades(run="r6")
        self.assertEqual((trade["outcome"], trade["profit"], trade["brain"], trade["lesson"]), ("tp", 10.0, "hybrid", "Fine trade."))
        self.assertEqual(journal.decision_counts(run="r6")["hold"], len(candles) - ai_strategy.CANDLES_NEEDED - 2)

    def test_atr_stops_follow_the_breakout_candles_range(self):
        candles, jump = flat_then_breakout()
        journal = Journal()
        replay.replay(candles, dict(CONFIG, SL_ATR=1.5, TP_ATR=3.0), journal, "r7", digits=2, log=lambda line: None)
        (trade,) = journal.closed_trades(run="r7")
        # ATR(14) after one 10-point candle among flat ones = 10/14: stop 107 points, target 214 points
        self.assertEqual((trade["entry"], trade["sl"], trade["tp"]), (2010.30, 2009.23, 2012.44))
        self.assertEqual((trade["outcome"], trade["profit"]), ("sl", -1.07))  # the wide candle spans both: stop first

    def test_compare_runs_every_strategy_on_throwaway_journals(self):
        candles, _ = flat_then_breakout()
        rows = replay.compare(candles, CONFIG, digits=2)
        self.assertEqual([name for name, _ in rows], list(strategies.STRATEGIES) + ["all"])
        by_name = dict(rows)
        self.assertEqual(by_name["ma_cross"]["trades"], 1)
        self.assertEqual(by_name["bollinger_breakout"]["trades"], 1)  # flat bands, then the jump
        text = replay.format_comparison(rows, CONFIG)
        self.assertIn("stops: 500 / 1000 points", text)
        self.assertIn("ma_cross", text)

    def test_repeated_ai_failures_abort_the_replay(self):
        candles, _ = flat_then_breakout(needed=ai_strategy.CANDLES_NEEDED)
        failure = ai_strategy.Decision(None, "AI error: key rejected, check ANTHROPIC_API_KEY in .env", 0.0)
        with mock.patch.object(ai_strategy, "decide", return_value=failure) as decide:
            stopped = replay.replay(candles, dict(CONFIG, BRAIN="ai"), Journal(), "r5", digits=2, log=lambda line: None)
        self.assertEqual((stopped, decide.call_count), ("ai failures", replay.MAX_AI_FAILURES))


class CsvTest(unittest.TestCase):
    def test_export_and_read_round_trip(self):
        rates = [
            {"time": START, "open": 2000.5, "high": 2001, "low": 1999.25, "close": 2000.75, "spread": 28},
            {"time": START + 900, "open": 2000.75, "high": 2002, "low": 2000, "close": 2001.5, "spread": 31.0},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "history.csv")
            self.assertEqual(export_history.write_csv(rates, path, digits=2), 2)
            first_line = path.read_text(encoding="utf-8").splitlines()[1]
            self.assertEqual(first_line, "2023-11-14 22:13:20,2000.50,2001.00,1999.25,2000.75,28")
            candles, digits = replay.read_candles(path)
        self.assertEqual(digits, 2)
        self.assertEqual(candles[0], {"time": START, "open": 2000.5, "high": 2001.0, "low": 1999.25, "close": 2000.75, "spread": 28})
        self.assertEqual(candles[1]["spread"], 31)

    def test_missing_spread_uses_the_default_and_disorder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "h.csv")
            path.write_text("time,open,high,low,close\n2024-01-01 00:00:00,1.1,1.2,1.0,1.15\n", encoding="utf-8")
            candles, digits = replay.read_candles(path, default_spread=12)
            self.assertEqual((candles[0]["spread"], digits), (12, 2))
            path.write_text(
                "time,open,high,low,close\n2024-01-01 00:15:00,1,1,1,1\n2024-01-01 00:00:00,1,1,1,1\n", encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                replay.read_candles(path)


class ReportTest(unittest.TestCase):
    def test_empty_journal_reports_nothing_closed(self):
        text = report.format_report(Journal())
        self.assertIn("trades: none closed yet", text)
        self.assertIn("AI spend: $0.00", text)


if __name__ == "__main__":
    unittest.main()
