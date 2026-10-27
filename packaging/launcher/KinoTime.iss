; Compile with ISCC /DAppFiles=... /DReleaseDirectory=... /DIconFile=... KinoTime.iss
#ifndef AppVersion
  #define AppVersion "1.3.6"
#endif
[Setup]
AppId={{8FB1B599-E889-4A60-8ABA-0D1CB7D1D2B2}
AppName=KinoTime Launcher
AppVersion={#AppVersion}
AppPublisher=KinoTime
DefaultDirName={localappdata}\Programs\KinoTime
DefaultGroupName=KinoTime
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
OutputDir={#ReleaseDirectory}
OutputBaseFilename=KinoTime-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\KinoTime.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"; Flags: unchecked

[Files]
Source: "{#AppFiles}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#ReleaseDirectory}\README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\KinoTime\KinoTime"; Filename: "{app}\KinoTime.exe"; WorkingDir: "{app}"
Name: "{userprograms}\KinoTime\Инструкция"; Filename: "{app}\README.txt"
Name: "{userdesktop}\KinoTime"; Filename: "{app}\KinoTime.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\KinoTime.exe"; Description: "Запустить KinoTime"; Flags: nowait postinstall skipifsilent unchecked
; Settings in %LOCALAPPDATA%\KinoTime\Launcher and media folders are deliberately
; outside the app directory. The uninstaller removes only installed program files.
