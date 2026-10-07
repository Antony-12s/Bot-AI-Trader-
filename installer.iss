; Inno Setup script for TradeBot-Setup.exe. Build it with build_installer.bat, not by hand.
; Per-user install, no admin: the bot writes .env, journal.db and stop.flag next to itself,
; which Program Files would refuse.

[Setup]
AppName=TradeBot
AppVersion=1.2.0
DefaultDirName={localappdata}\Programs\TradeBot
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=TradeBot-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=tradebot.ico
UninstallDisplayIcon={app}\TradeBot.exe

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"

[Files]
Source: "dist\TradeBot\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "*.bat"; Excludes: "build_installer.bat,fix_github_login.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; The app window is the main entry; Start / Stop live in it. ui.py hides its console.
Name: "{autoprograms}\TradeBot\TradeBot"; Filename: "{app}\TradeBot.exe"; Parameters: "ui.py"; WorkingDir: "{app}"
; Console tools for power users, out of the way of everyone else.
Name: "{autoprograms}\TradeBot\Advanced\Start bot (console)"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Advanced\Setup wizard (console)"; Filename: "{app}\settings.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Advanced\Train AI"; Filename: "{app}\train.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Advanced\Run forever (VPS)"; Filename: "{app}\run_forever.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Uninstall TradeBot"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TradeBot"; Filename: "{app}\TradeBot.exe"; Parameters: "ui.py"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\TradeBot.exe"; Parameters: "ui.py"; WorkingDir: "{app}"; Description: "Launch TradeBot"; Flags: postinstall nowait skipifsilent

[Registry]
; Not created here (ValueType none): the app's "Start with Windows" switch writes it. Uninstall removes it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "TradeBot"; ValueType: none; Flags: uninsdeletevalue

[UninstallDelete]
; .env holds the Anthropic key and Telegram token: do not leave them behind. journal.db (trade history) stays.
Type: files; Name: "{app}\.env"
Type: files; Name: "{app}\.env.check"
Type: files; Name: "{app}\bot.log"
Type: files; Name: "{app}\bot.alive"
Type: files; Name: "{app}\stop.flag"

[UninstallRun]
; Drop the logon task install_autostart.bat may have created; harmless when it does not exist.
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Bot AI Trader"""; Flags: runhidden; RunOnceId: "DropAutostart"
