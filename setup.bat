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
    echo.
    echo First run: a few questions, the rest is read from MT5. Keep MT5 open and logged in.
    echo.
    "%PY%" wizard.py || exit /b 1
)
exit /b 0
