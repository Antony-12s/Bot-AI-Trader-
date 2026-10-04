@echo off
rem Watchdog for unattended running (VPS): restarts the bot after a crash, stops after /stop.
rem Double-click it, or let install_autostart.bat start it at logon.
setlocal
title Bot AI Trader (watchdog)
call "%~dp0setup.bat" || goto :fail
if exist "%~dp0stop.flag" del "%~dp0stop.flag"
:loop
echo.
echo [%date% %time%] starting the bot ^(MODE and BRAIN come from .env^)
"%PY%" bot.py
if exist "%~dp0stop.flag" (
    del "%~dp0stop.flag"
    echo [%date% %time%] stopped by /stop, not restarting
    goto :done
)
echo [%date% %time%] the bot exited with code %errorlevel%, restarting in 30 seconds. Close this window to stop.
timeout /t 30 /nobreak >nul
goto :loop

:fail
echo.
echo Setup failed. Install Python 3.10 or newer from https://www.python.org and tick "Add to PATH".

:done
echo.
pause
