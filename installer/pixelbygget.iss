; The Pixelbygget installer for Windows. Built by installer\build.ps1, which passes the
; version, the staged folder and the model's folder names.
;
; It asks for no administrator rights, ever. Everything goes into the person's own
; %LOCALAPPDATA%\Pixelbygget, with one Start menu entry and one desktop shortcut. PATH is
; not touched, and the only registry entry is Inno Setup's own uninstall entry under HKCU.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Stage
  #define Stage "stage"
#endif
#ifndef ModelFolder
  #define ModelFolder ""
#endif
#ifndef LoraFolder
  #define LoraFolder ""
#endif
#ifndef ModelGb
  #define ModelGb "12"
#endif

[Setup]
; Fixed for ever: it is how a newer installer finds the old install and upgrades it in place.
AppId={{6F0D2B6E-4C1A-4F0B-9E57-2A8D3C5B71E4}
AppName=Pixelbygget
AppVersion={#AppVersion}
AppPublisher=opensverige
AppPublisherURL=https://karta.opensverige.se/pixelbygget
DefaultDirName={localappdata}\Pixelbygget
DefaultGroupName=Pixelbygget
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
; PrivilegesRequiredOverridesAllowed is left at its default, which is none: no dialog and
; no switch can raise the installer to administrator.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=Pixelbygget-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
UninstallDisplayName=Pixelbygget
; The app is asked to end by its own command (see the code below), not by Windows.
CloseApplications=no

[Languages]
Name: "sv"; MessagesFile: "compiler:Languages\Swedish.isl"

[Files]
; node\, the installed model stack (python\Lib\site-packages), chrome\ and work\ are not
; in the staged folder, so an upgrade leaves them as they are.
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; On an upgrade, code and wheels of the older version do not stay beside the new ones.
Type: filesandordirs; Name: "{app}\cli\rutor"
Type: filesandordirs; Name: "{app}\wheels"

[Icons]
Name: "{autoprograms}\Pixelbygget"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\start.pyw"""; WorkingDir: "{app}"
Name: "{autodesktop}\Pixelbygget"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\start.pyw"""; WorkingDir: "{app}"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\start.pyw"""; WorkingDir: "{app}"; Description: "Starta Pixelbygget"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Everything the app fetched into its own folder: Node, the model stack, Chrome, renders.
Type: filesandordirs; Name: "{app}"

[Code]
function AskTheAppToEnd(const Folder: String): Boolean;
var
  Python: String;
  Code: Integer;
begin
  // An app that is running holds its own files open. It is asked to end the way its page
  // ends it: the job is stopped and the cell's lock is given back first. -I keeps the
  // person's own Python settings away from the app's Python.
  Result := True;
  Python := Folder + '\python\python.exe';
  if FileExists(Python) then
    if Exec(Python, '-I -m rutor app --quit --quiet', Folder, SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // "Stang Pixelbygget och forsok igen.", with its Swedish letters as character codes.
  if AskTheAppToEnd(ExpandConstant('{app}')) then
    Result := ''
  else
    Result := 'St'#228'ng Pixelbygget och f'#246'rs'#246'k igen.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := AskTheAppToEnd(ExpandConstant('{app}'));
  if not Result then
    MsgBox('St'#228'ng Pixelbygget och f'#246'rs'#246'k igen.', mbError, MB_OK);
end;

function HubCache(): String;
begin
  // The same order as the Hugging Face library and rutor use.
  Result := GetEnv('HF_HUB_CACHE');
  if Result = '' then
    Result := GetEnv('HUGGINGFACE_HUB_CACHE');
  if (Result = '') and (GetEnv('HF_HOME') <> '') then
    Result := GetEnv('HF_HOME') + '\hub';
  if Result = '' then
    Result := GetEnv('USERPROFILE') + '\.cache\huggingface\hub';
end;

procedure RemoveModelFolder(const Name: String);
begin
  // A folder name of the model's own shape only, and nothing else in the cache.
  if (Name <> '') and (Pos('models--', Name) = 1) and (Pos('\', Name) = 0) and (Pos('/', Name) = 0) and (Pos('..', Name) = 0) then
    DelTree(HubCache() + '\' + Name, True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Model: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  // The login, the Cesium token and the app's log. A secret is not left on a machine the
  // app has been removed from.
  DelTree(GetEnv('USERPROFILE') + '\.config\pixelbygget', True, True, True);
  Model := '{#ModelFolder}';
  if (Model = '') or not DirExists(HubCache() + '\' + Model) then
    exit;
  // A silent uninstall cannot ask, so it keeps the model.
  if UninstallSilent then
    exit;
  // "Ta bort ritmodellen ocksa? Den ar N GB.", with its Swedish letters written as
  // character codes so that this file is plain ASCII whatever reads it.
  if MsgBox('Ta bort ritmodellen ocks'#229'? Den '#228'r {#ModelGb} GB.', mbConfirmation, MB_YESNO) = IDYES then
  begin
    RemoveModelFolder('{#ModelFolder}');
    RemoveModelFolder('{#LoraFolder}');
  end;
end;
