; Inno Setup script - makes a one-click installer from the dist\PortfolioManager folder
; 1) run build_exe.bat   2) open this file in Inno Setup   3) Build > Compile
; The installer file is created in the Output folder.

[Setup]
AppName=Portfolio Manager
AppVersion=1.0
; Per-user install folder: the app saves its DB next to the exe, so it must be writable
DefaultDirName={localappdata}\Programs\PortfolioManager
DefaultGroupName=Portfolio Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=PortfolioManager_Setup
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Dirs]
; Keep users' saved portfolios when updating or uninstalling
Name: "{app}\DB"; Flags: uninsneveruninstall

[Files]
Source: "dist\PortfolioManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Portfolio Manager"; Filename: "{app}\PortfolioManager.exe"
Name: "{autodesktop}\Portfolio Manager"; Filename: "{app}\PortfolioManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PortfolioManager.exe"; Description: "Launch Portfolio Manager"; Flags: nowait postinstall skipifsilent
