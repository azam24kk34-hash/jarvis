; Інсталятор J.A.R.V.I.S. (Inno Setup 6). Збирається автоматично в GitHub Actions.
; Ставиться для поточного користувача, без прав адміністратора: %LOCALAPPDATA%\Programs\Jarvis

#ifndef AppVersion
  #define AppVersion "dev"
#endif

[Setup]
AppId={{6B7C2E1A-4F0B-4C2E-9A61-JARVIS000001}
AppName=J.A.R.V.I.S.
AppVersion={#AppVersion}
AppPublisher=Jarvis
DefaultDirName={localappdata}\Programs\Jarvis
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Jarvis-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\Jarvis.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Jarvis\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\J.A.R.V.I.S."; Filename: "{app}\Jarvis.exe"
Name: "{autodesktop}\J.A.R.V.I.S."; Filename: "{app}\Jarvis.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Jarvis.exe"; Description: "{cm:LaunchProgram,J.A.R.V.I.S.}"; Flags: nowait postinstall skipifsilent
; після тихого оновлення («онови себе») — одразу запускаємо нову версію
Filename: "{app}\Jarvis.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; прибираємо автозапуск, який Jarvis додає сам
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Jarvis /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
