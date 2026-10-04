#!/bin/sh
# Mac / Linux: run the bot. Same job as start.bat; MT5 is Windows only, so this is the cTrader route.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Creating a Python environment in .venv ..."
    python3 -m venv .venv || { echo "Python 3.10+ is needed: https://www.python.org"; exit 1; }
fi
.venv/bin/python -m pip install -q -r requirements.txt || exit 1
if [ ! -f .env ]; then
    echo
    echo "First run: a few questions, the rest is read from the broker."
    echo
    .venv/bin/python wizard.py || exit 1
fi
echo
echo "Starting the bot (MODE and BRAIN come from .env). Press Ctrl+C to stop."
echo
exec .venv/bin/python bot.py
