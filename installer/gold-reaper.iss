; ═══════════════════════════════════════════════════════════════
;  GOLD REAPER :: Inno Setup script  ->  GoldReaper-Setup.exe
;═════════════════════════════════════════════════════════════════
;  Build:
;    iscc installer\gold-reaper.iss          (or via CI release.yml)
;  Produces:
;    output\GoldReaper-Setup-{version}.exe
;
;  What the built installer does:
;    - payload -> C:\Program Files\GoldReaper
;    - detects / silently installs Python 3.12 if missing
;    - on finish: runs install_windows.ps1 -FromInstaller
;      (copies runtime to %LOCALAPPDATA%\GoldReaper, venv, pip,
;       Desktop + Start Menu "Gold Reaper" shortcuts, Task
;       Scheduler tasks, hidden windows, auto-restart)
;    - offers "Launch Gold Reaper now" (wizard runs on first launch)
;    - uninstaller in Add/Remove Programs (also cleans LOCALAPPDATA)
;═════════════════════════════════════════════════════════════════

#define MyAppName "Gold Reaper"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "gold-reaper project"
#define MyAppURL "https://github.com/muhammadwhizz-web/gold-reaper"
#define MyAppExeName "start_windows.bat"

[Setup]
AppId={{7E3F2C90-5B41-4D6A-9F2E-1A0D66666666}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\GoldReaper
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\assets\icon.ico
LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename=GoldReaper-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; full repo payload -> Program Files\GoldReaper (paths relative to THIS .iss)
Source: "..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
    Excludes: ".git,.gitignore,.gitattributes,.venv,venv,__pycache__,*.pyc,data,data\*,logs,logs\*,.env,installer\output,*.iss.bak"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\assets\icon.ico"; WorkingDir: "{app}"; \
    Comment: "Autonomous XAU/USD hunter"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{app}\assets\icon.ico"; WorkingDir: "{app}"; \
    Tasks: desktopicon; Comment: "Autonomous XAU/USD hunter"

[Run]
; the real installer: venv + deps + shortcuts + Task Scheduler
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install_windows.ps1"" -FromInstaller -PayloadDir ""{app}"""; \
  WorkingDir: "{app}"; Flags: runasoriginaluser waituntilterminated skipifnotsilent; \
  Description: "Configure runtime, shortcuts and autostart"; \
  StatusMsg: "Configuring Gold Reaper runtime (venv + tasks)..."

; optional immediate launch (console wizard runs on first launch)
Filename: "{app}\{#MyAppExeName}"; \
  Description: "{cm:LaunchProgram,{#MyAppName}}"; \
  Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; stop tasks + kill bot before file removal
Filename: "schtasks"; Parameters: "/End /TN GOLD-REAPER"; Flags: runhidden; RunOnceId: "EndBot"
Filename: "schtasks"; Parameters: "/End /TN GOLD-REAPER-DASHBOARD"; Flags: runhidden; RunOnceId: "EndDash"
Filename: "schtasks"; Parameters: "/End /TN GOLD-REAPER-WATCHDOG"; Flags: runhidden; RunOnceId: "EndWatch"
Filename: "schtasks"; Parameters: "/Delete /TN GOLD-REAPER /F"; Flags: runhidden; RunOnceId: "DelBot"
Filename: "schtasks"; Parameters: "/Delete /TN GOLD-REAPER-DASHBOARD /F"; Flags: runhidden; RunOnceId: "DelDash"
Filename: "schtasks"; Parameters: "/Delete /TN GOLD-REAPER-WATCHDOG /F"; Flags: runhidden; RunOnceId: "DelWatch"
Filename: "schtasks"; Parameters: "/Delete /TN GOLD-REAPER-RETRAIN /F"; Flags: runhidden; RunOnceId: "DelRetrain"

[Code]
// remove the per-user runtime (%LOCALAPPDATA%\GoldReaper) on uninstall
// after asking - it contains .env keys, logs and the audit trail.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    UserDir := ExpandConstant('{userappdata}') + '\..\Local\GoldReaper';
    if DirExists(UserDir) then
      if MsgBox('Delete the Gold Reaper runtime data too?' #13#10 +
                '(contains .env credentials, logs and the audit trail)' #13#10 #13#10 +
                UserDir, mbConfirmation, MB_YESNO) = IDYES then
        DelTree(UserDir, True, True, True);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;
