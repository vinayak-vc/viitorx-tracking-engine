; Inno Setup 6 script of the Viitorx Tracking Engine installer. Built by build/build_engine.py (installer step), which passes:
;   /DSourceDir=<the release folder>  /DAppVersion=<x.y.z>  /DFileVersion=<a.b.c.d>  /DOutputDir=<where the setup exe goes>
;
; What it does: installs the compiled engine folder to Program Files (needs administrator rights, asked once), makes `models\` writable by every user
; (the engine downloads models there, and a user may replace them), records the install folder for hosts (registry + the VIITORX_TRACKING_ENGINE
; machine environment variable) and, if the user ticks the box, downloads the hands / face / cutout models with `viitorx-tracking fetch-models`.
; The installer is NOT code-signed: Windows SmartScreen shows "unknown publisher" until it is.

#ifndef SourceDir
  #error SourceDir is required: iscc /DSourceDir=...
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef FileVersion
  #define FileVersion "0.0.0.0"
#endif
#ifndef OutputDir
  #define OutputDir "out"
#endif

#define AppName "Viitorx Tracking Engine"
#define Publisher "ViitorCloud"
#define ExeName "viitorx-tracking.exe"

[Setup]
; Never change AppId: it is how a later setup finds and upgrades this install.
AppId={{6F0C3E2A-8B54-4D1E-9A7B-5C2D8E41F7A3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
VersionInfoVersion={#FileVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} setup
DefaultDirName={autopf}\Viitorx\TrackingEngine
DisableProgramGroupPage=yes
DefaultGroupName=Viitorx
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=viitorx-tracking-setup-{#AppVersion}
LicenseFile={#SourceDir}\LICENSE
Compression=lzma2/normal
SolidCompression=yes
ChangesEnvironment=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "fetchmodels"; Description: "Download the hands, face and cutout models now (about 50 MB, checked against SHA-256)"; Flags: unchecked

[Dirs]
; Writable by every user: fetch-models and model replacement run without administrator rights.
Name: "{app}\models"; Permissions: users-modify

[Files]
; The program files are read-only for users (inherited from Program Files). models\ is created above and is not part of the source folder's manifest.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "models\*"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#SourceDir}\models\README.txt"; DestDir: "{app}\models"; Flags: ignoreversion

[Registry]
; How a host (Unity, Unreal, ...) finds the machine-wide install without a path setting.
Root: HKLM; Subkey: "Software\Viitorx\TrackingEngine"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Viitorx\TrackingEngine"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "VIITORX_TRACKING_ENGINE"; ValueData: "{app}\{#ExeName}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#ExeName}"; Parameters: "fetch-models"; Description: "Download the models"; StatusMsg: "Downloading models..."; Tasks: fetchmodels; Flags: runhidden waituntilterminated

[UninstallDelete]
; Models and anything the engine wrote beside itself are removed with the program: nothing else lives in this folder.
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\logs"
