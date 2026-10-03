"""Training report from the journal: how the bot has traded so far and what it has learned.

    python report.py                      # every trade on record
    python report.py --source replay      # replay | paper | mt5
    python report.py --run replay-20261003-213000
"""
import argparse
from datetime import datetime, timezone

from config import JOURNAL_PATH
from journal import Journal, summarize

DECISION_SOURCE = {"paper": "bot", "mt5": "bot", "replay": "replay"}


def stamp(server_time):
    return datetime.fromtimestamp(server_time, timezone.utc).strftime("%Y-%m-%d %H:%M")


def format_report(journal, source=None, run=None, lessons=5):
    trades = journal.closed_trades(source, run)
    totals = summarize(trades)
    decision_source = DECISION_SOURCE.get(source) if run is None else None
    counts = journal.decision_counts(decision_source, run)
    scope = " ".join(part for part in (f"source={source}" if source else "", f"run={run}" if run else "") if part) or "everything"
    lines = [f"Report ({scope})", ""]
    if trades:
        lines += [
            f"period: {stamp(trades[0]['opened_at'])} to {stamp(trades[-1]['closed_at'])}",
            f"trades: {totals['trades']} ({totals['wins']} wins, {totals['losses']} losses, win rate {totals['win_rate']:.0%})",
            f"net: {totals['net']:+.2f}   gross win {totals['gross_win']:.2f}   gross loss {totals['gross_loss']:.2f}"
            f"   profit factor {'n/a' if totals['profit_factor'] is None else totals['profit_factor']}",
            f"average win {totals['avg_win']:+.2f}   average loss {totals['avg_loss']:+.2f}",
            f"max drawdown {totals['max_drawdown']:.2f}   longest losing streak {totals['longest_losing_streak']}",
        ]
        for side, part in totals["by_side"].items():
            lines.append(f"{side}s: {part['trades']} trades, {part['win_rate']:.0%} wins, net {part['net']:+.2f}")
        lines.append("outcomes: " + ", ".join(f"{name} {count}" for name, count in sorted(totals["outcomes"].items())))
    else:
        lines.append("trades: none closed yet")
    open_count = len([t for t in journal.open_trades(source) if run is None or t["run"] == run])
    if open_count:
        lines.append(f"open positions: {open_count}")
    if counts:
        lines.append("decisions: " + ", ".join(f"{action} {count}" for action, count in sorted(counts.items())))
    lines.append(f"AI spend: ${journal.spend(decision_source, run):.2f}")
    playbook = journal.playbook()
    if playbook:
        lines += ["", f"playbook (after {playbook['trades_seen']} trades):", playbook["text"]]
    recent = journal.recent_lessons(lessons)
    if recent:
        lines += ["", f"last {len(recent)} lessons:"]
        lines += [f"- {stamp(t['closed_at'])} {t['side']} {t['outcome']} {t['profit']:+.2f}: {t['lesson']}" for t in recent]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("replay", "paper", "mt5"))
    parser.add_argument("--run")
    parser.add_argument("--journal", default=JOURNAL_PATH, help="journal file (default: journal.db next to the code)")
    args = parser.parse_args()
    journal = Journal(args.journal)
    try:
        print(format_report(journal, args.source, args.run))
    finally:
        journal.close()


if __name__ == "__main__":
    main()
