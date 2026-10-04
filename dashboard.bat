@echo off
rem Double-click for the one-window view: MT5 status, price, Start / Stop / Pause, stats, log.
setlocal
call "%~dp0setup.bat" || (
    echo Setup failed. Install Python 3.10 or newer from https://www.python.org and tick "Add to PATH".
    pause
    exit /b 1
)
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0dashboard.py"
) else (
    "%PY%" "%~dp0dashboard.py"
)
