; Inno Setup script for CiteFinder (Phase 16) — wraps the PyInstaller onedir
; (dist\CiteFinder) into a single one-click Windows installer.
;
; Build:  "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" CiteFinder.iss
; Output: installer\CiteFinder-Setup-<version>.exe
;
; Per-user install (no admin needed) — fits a "download & run" desktop app. The
; bundle already contains Postgres + pgvector + the e5 model; only the answer LLM
; is configured at runtime (cloud key or local Ollama). WebView2 runtime is assumed
; present (ships with current Windows 10/11 Edge).

#define AppName "CiteFinder"
#define AppVersion "1.1.0"
#define AppExe "CiteFinder.exe"

[Setup]
AppId={{8E1C6F2A-7C4B-4E2D-9A1F-CITEFINDER001}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=CiteFinder
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=installer
OutputBaseFilename=CiteFinder-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "dist\CiteFinder\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
