@echo off
rem Double-click to run the bot. Keep the MT5 terminal open and logged in.
setlocal
title Bot AI Trader
call "%~dp0setup.bat" || goto :fail
echo.
echo Starting the bot ^(MODE and BRAIN come from .env^). Press Ctrl+C to stop.
echo.
"%PY%" bot.py
goto :done

:fail
echo.
echo Setup failed. Install Python 3.10 or newer from https://www.python.org and tick "Add to PATH".

:done
echo.
pause
