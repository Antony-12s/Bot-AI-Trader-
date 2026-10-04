@echo off
rem Start run_forever.bat whenever this Windows user logs on (Task Scheduler), for a VPS.
rem Pair it with: the MT5 terminal in the Startup folder (Win+R, shell:startup) and
rem automatic logon on the VPS, so a reboot brings everything back without you.
setlocal
schtasks /create /f /tn "Bot AI Trader" /sc onlogon /tr "\"%~dp0run_forever.bat\"" || goto :fail
echo.
echo Installed: "Bot AI Trader" runs run_forever.bat at every logon of this user.
echo Remove it later with remove_autostart.bat. Test it now: log off and on, or reboot.
goto :done

:fail
echo.
echo Could not create the scheduled task. Right-click this file and "Run as administrator".

:done
echo.
pause
