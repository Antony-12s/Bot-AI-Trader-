@echo off
rem Change the bot's settings by answering questions again (rewrites .env). Keep MT5 open.
rem No ( ) block around the prompt: the ")" in "(y/N)" would close it early.
setlocal
title Bot AI Trader - settings
cd /d "%~dp0"
if not exist ".env" goto :setup
set /p SURE=This replaces your current .env. Continue? (y/N) 
if /i not "%SURE%"=="y" goto :done
del .env
:setup
call "%~dp0setup.bat"
:done
echo.
pause
