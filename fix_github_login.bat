@echo off
rem For a teammate who gets 403 / "Permission denied to <someone else>" on git push:
rem Windows remembered another GitHub account. This forgets every saved GitHub login,
rem then asks GitHub to sign you in again in the browser. Sign in with the account that
rem was invited to the repository.
setlocal
title Bot AI Trader - fix GitHub login
cd /d "%~dp0"
where git >nul 2>nul || (
    echo Git is not installed. Install it from https://git-scm.com/download/win ^(keep the defaults^) and run this again.
    goto :done
)
echo Forgetting saved GitHub logins ...
for /f "tokens=2 delims==" %%t in ('cmdkey /list ^| findstr /i "github.com"') do (
    echo   removing %%t
    cmdkey /delete:%%t >nul 2>nul
)
(echo protocol=https& echo host=github.com& echo.) | git credential reject 2>nul
git config --global credential.helper manager
echo.
echo Now a browser window should open. Sign in to GitHub with the invited account.
echo If a window asks how to sign in, pick "Sign in with your browser".
echo.
git fetch origin
if errorlevel 1 (
    echo.
    echo Still refused. Open https://github.com in the browser and check the account name at the top right:
    echo it must be the one the repository owner invited. If it is, ask the owner to check Settings ^> Collaborators.
) else (
    echo.
    echo Signed in. git push works now.
)

:done
echo.
pause
