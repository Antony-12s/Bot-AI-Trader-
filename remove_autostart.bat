@echo off
rem Undo install_autostart.bat.
schtasks /delete /f /tn "Bot AI Trader"
echo.
pause
