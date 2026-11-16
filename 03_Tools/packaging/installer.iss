; Inno Setup script for RDR Modding Studio.
;
; NOT BUILT OR TESTED -- Inno Setup is not installed on this machine. Everything
; above this line in the plan was measured; this file is a specification, and it
; is marked ? inferred until somebody runs ISCC over it and installs the result.
;
;   1. Install Inno Setup 6 (free, jrsoftware.org)
;   2. python packaging/build.py --numpy
;   3. "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; Produces packaging\dist\RDR-Modding-Studio-Setup-<version>.exe
;
; Three rules this installer follows, all of them consequences of the app
; needing no admin rights and owning no game data:
;
;   * PrivilegesRequired=lowest -- installs per-user into %LOCALAPPDATA%\Programs.
;     No UAC prompt, no Program Files, and therefore no chance of the app being
;     unable to write beside itself. It never writes beside itself anyway, but
;     a per-user install also means a non-admin can install it at all.
;   * The uninstaller removes the PROGRAM and nothing else. Settings, mods,
;     checkpoints and the working ISO are the user's, and an uninstall that
;     silently deletes a 2.2 GB working copy plus every mod they made would be
;     indefensible. The uninstaller asks, once, and defaults to keeping them.
;   * The licence page is the disclaimer. It is the one thing a user must see
;     before the app exists on their disk, because it is where "this ships with
;     no game data and you need your own disc" is said.

#define AppName    "RDR Modding Studio"
#define AppVersion "2.0.0"
#define AppExe     "RDR Modding Studio.exe"
; build.py --build release writes here. The release build is the one that gets
; an installer; the internal edition is a zip and stays a zip.
#define SrcDir     "dist\release\RDR Modding Studio"

[Setup]
AppId={{8A3D2C10-5E4F-4C7B-9A21-RDRSTUDIO0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=RDR Modding Studio contributors
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=RDR-Modding-Studio-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=README.txt
UninstallDisplayIcon={app}\{#AppExe}
; The app needs 2.2 GB of free space for the working copy, but that copy does
; NOT go in the install dir -- the user chooses its drive on first run. So the
; installer asks only for its own footprint.
ExtraDiskSpaceRequired=0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Nothing. Deliberately. See the note at the top -- user data is not ours to
; remove, and the code below asks before touching any of it.

[Code]
// The app draws its whole window with the Edge WebView2 runtime. Without it,
// the installed app opens a blank rectangle -- so the installer says so at
// install time, when the user is still in a frame of mind to fix it, rather
// than leaving them to discover it as "your program is broken". It warns and
// continues rather than blocking, because the runtime can be installed after.
function WebView2Present: Boolean;
var
  V: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', V);
  if Result then
    Result := (V <> '') and (V <> '0.0.0.0');
end;

function InitializeSetup: Boolean;
var
  Ignore: Integer;
begin
  Result := True;
  if not WebView2Present then
  begin
    if MsgBox('This computer does not have the Microsoft Edge WebView2 runtime.'
      + #13#10#13#10
      + 'RDR Modding Studio draws its window with it, so it will open blank '
      + 'until the runtime is installed. It is a free Microsoft download and '
      + 'takes about a minute.' + #13#10#13#10
      + 'Open the download page now? (Setup will carry on either way.)',
      mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/',
                '', '', SW_SHOW, ewNoWait, Ignore);
  end;
end;

procedure CurUninstallStepChanged(CurStep: TUninstallStep);
var
  AppData, Docs: String;
begin
  if CurStep = usPostUninstall then
  begin
    AppData := ExpandConstant('{localappdata}\RDR Modding Studio');
    Docs := ExpandConstant('{userdocs}\RDR Mods');
    if DirExists(AppData) then
    begin
      if MsgBox('Also delete your settings and the 2.2 GB working copy of the disc?'
                + #13#10#13#10 + AppData + #13#10#13#10
                + 'Your mods and checkpoints in' + #13#10 + Docs + #13#10
                + 'are NOT affected either way.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(AppData, True, True, True);
    end;
  end;
end;
