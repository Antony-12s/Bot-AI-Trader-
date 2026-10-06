@echo off
rem Builds dist\TradeBot-Setup.exe: TradeBot.exe (PyInstaller) wrapped by Inno Setup 6.
rem Needs Python 3.10+ and Inno Setup 6 (winget install JRSoftware.InnoSetup).
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PYLAUNCH=py -3") || (set "PYLAUNCH=python")
if not exist ".venv\Scripts\python.exe" %PYLAUNCH% -m venv .venv || goto :fail
set "PY=%~dp0.venv\Scripts\python.exe"
"%PY%" -m pip install -q -r requirements.txt pyinstaller || goto :fail

rem launcher.py loads the scripts by name at run time, so list them for PyInstaller.
rem MetaTrader5 imports numpy from C, which PyInstaller cannot see either.
"%PY%" -m PyInstaller --noconfirm --clean --console --name TradeBot --hidden-import numpy ^
    --hidden-import bot --hidden-import wizard --hidden-import replay ^
    --hidden-import export_history --hidden-import report --hidden-import ui ^
    --add-data "ui.html;." launcher.py || goto :fail

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
"%ISCC%" /Q installer.iss || goto :fail
echo.
echo Done: dist\TradeBot-Setup.exe
goto :done

:fail
echo.
echo Build failed, read the message above.

:done
endlocal
