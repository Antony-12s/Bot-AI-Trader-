@echo off
rem Double-click to train the AI: export candles from MT5, replay them on paper, show the report.
setlocal
title Bot AI Trader - training
call "%~dp0setup.bat" || goto :fail
set "DAYS=90"
set /p DAYS=How many days of history to replay? [90] 
echo.
echo 1/4 exporting %DAYS% days of candles from MT5 ...
"%PY%" export_history.py --days %DAYS% --out history.csv || goto :fail
echo.
echo 2/4 comparing the rule strategies on those candles, free, no AI ...
"%PY%" replay.py history.csv --compare || goto :fail
echo.
echo 3/4 replaying on paper with BRAIN=hybrid, stops at AI_BUDGET_USD from .env ...
"%PY%" replay.py history.csv --brain hybrid || goto :fail
echo.
echo 4/4 report of everything on record
"%PY%" report.py
goto :done

:fail
echo.
echo Training stopped. Read the message above: MT5 not open, no API key, or Python missing.

:done
echo.
pause
