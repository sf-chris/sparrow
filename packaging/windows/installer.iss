#define AppVersion "0.2.0-alpha.1"
[Setup]
AppId=SparrowNode
AppName=Sparrow Node
AppVersion={#AppVersion}
VersionInfoVersion=0.2.0.1
DefaultDirName={autopf}\Sparrow Node
DefaultGroupName=Sparrow
OutputDir=..\output
OutputBaseFilename=SparrowNode-Setup-x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
[Files]
Source: "..\..\dist\SparrowNode\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\build\inputs\WinSW-x64.exe"; DestDir: "{app}"; DestName: "SparrowService.exe"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"
Source: "..\build\inputs\WinSW-LICENSE.txt"; DestDir: "{app}\licenses"
Source: "..\build\inputs\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
[Icons]
Name: "{group}\Connect Sparrow storage"; Filename: "{app}\SparrowNodeSetup.exe"
Name: "{group}\Uninstall Sparrow Node"; Filename: "{uninstallexe}"
[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing the media processing runtime…"; Flags: waituntilterminated
Filename: "{app}\SparrowNodeSetup.exe"; Description: "Connect this storage to Sparrow"; Flags: postinstall nowait skipifsilent
[UninstallRun]
Filename: "{app}\SparrowService.exe"; Parameters: "stop"; Flags: runhidden; RunOnceId: "StopNode"
Filename: "{app}\SparrowService.exe"; Parameters: "uninstall"; Flags: runhidden; RunOnceId: "RemoveService"
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\SparrowService.exe')) then
    Exec(ExpandConstant('{app}\SparrowService.exe'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
