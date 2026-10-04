@echo off
rem Change the bot's settings by answering questions again (rewrites .env). Keep MT5 open.
setlocal
title Bot AI Trader - settings
cd /d "%~dp0"
if exist ".env" (
    set /p SURE=This replaces your current .env. Continue? (y/N) 
    if /i not "%SURE%"=="y" goto :done
    del .env
)
call "%~dp0setup.bat"
:done
echo.
pause
