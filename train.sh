#!/bin/sh
# Mac / Linux: train the AI. Same job as train.bat: export candles, replay on paper, show the report.
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || python3 -m venv .venv || exit 1
.venv/bin/python -m pip install -q -r requirements.txt || exit 1
[ -f .env ] || .venv/bin/python wizard.py || exit 1
printf "How many days of history to replay? [90] "
read -r DAYS
DAYS=${DAYS:-90}
echo
echo "1/4 exporting $DAYS days of candles from the broker ..."
.venv/bin/python export_history.py --days "$DAYS" --out history.csv || exit 1
echo
echo "2/4 comparing the rule strategies on those candles, free, no AI ..."
.venv/bin/python replay.py history.csv --compare || exit 1
echo
echo "3/4 replaying on paper with BRAIN=hybrid, stops at AI_BUDGET_USD from .env ..."
.venv/bin/python replay.py history.csv --brain hybrid || exit 1
echo
echo "4/4 report of everything on record"
.venv/bin/python report.py
