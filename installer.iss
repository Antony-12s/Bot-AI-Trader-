; Inno Setup script for TradeBot-Setup.exe. Build it with build_installer.bat, not by hand.
; Per-user install, no admin: the bot writes .env, journal.db and stop.flag next to itself,
; which Program Files would refuse.

[Setup]
AppName=TradeBot
AppVersion=1.0.0
DefaultDirName={localappdata}\Programs\TradeBot
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=TradeBot-Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\TradeBot.exe

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"

[Files]
Source: "dist\TradeBot\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "*.bat"; Excludes: "build_installer.bat,fix_github_login.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\TradeBot\Start bot"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Settings"; Filename: "{app}\settings.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Train AI"; Filename: "{app}\train.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Run forever (VPS)"; Filename: "{app}\run_forever.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\TradeBot\Uninstall TradeBot"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TradeBot"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\start.bat"; WorkingDir: "{app}"; Description: "Launch TradeBot"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Drop the logon task install_autostart.bat may have created; harmless when it does not exist.
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Bot AI Trader"""; Flags: runhidden; RunOnceId: "DropAutostart"
