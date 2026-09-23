#define MyAppName "Gamepad Signal Lab"
#define MyAppVersion "0.5.0"
#define MyAppPublisher "SensoredRooster"
#define MyAppExeName "GamepadSignalLab.exe"

[Setup]
AppId={{E51620C4-3372-4BCE-9A2B-963D9DB6A460}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Gamepad Signal Lab
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename=GamepadSignalLab-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\GamepadSignalLab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
