@echo off
rem Double-click to open the dashboard in your browser. Close this window to stop it.
setlocal
title Bot AI Trader - dashboard
call "%~dp0setup.bat" || goto :fail
"%PY%" ui.py
goto :done

:fail
echo.
echo Setup failed. Install Python 3.10 or newer from https://www.python.org and tick "Add to PATH".

:done
echo.
pause
