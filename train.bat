@echo off
rem Double-click to train the AI: export candles from MT5, replay them on paper, show the report.
setlocal
title Bot AI Trader - training
call "%~dp0setup.bat" || goto :fail
set "DAYS=90"
set /p DAYS=How many days of history to replay? [90] 
echo.
echo 1/3 exporting %DAYS% days of candles from MT5 ...
"%PY%" export_history.py --days %DAYS% --out history.csv || goto :fail
echo.
echo 2/3 replaying on paper with the AI brain, stops at AI_BUDGET_USD from .env ...
"%PY%" replay.py history.csv --brain ai || goto :fail
echo.
echo 3/3 report of everything on record
"%PY%" report.py
goto :done

:fail
echo.
echo Training stopped. Read the message above: MT5 not open, no API key, or Python missing.

:done
echo.
pause
