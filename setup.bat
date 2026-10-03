@echo off
rem Shared by start.bat and train.bat: Python environment, packages, first-run .env.
rem No setlocal here on purpose, the caller needs PY afterwards.
cd /d "%~dp0"
where py >nul 2>nul && (set "PYLAUNCH=py -3") || (set "PYLAUNCH=python")
if not exist ".venv\Scripts\python.exe" (
    echo Creating a Python environment in .venv ...
    %PYLAUNCH% -m venv .venv || exit /b 1
)
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" -m pip install -q -r requirements.txt || exit /b 1
if not exist ".env" (
    copy .env.example .env >nul
    echo.
    echo First run: .env was created. Fill in MODE, SYMBOL, LOT, CONTRACT_SIZE, the API key
    echo and Telegram, then save and close Notepad. The program continues afterwards.
    echo.
    notepad .env
)
exit /b 0
