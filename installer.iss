[Setup]
AppName=Tim Roy Performer Arp
AppVersion=1.0.0
AppPublisher=Tim Roy
DefaultDirName={pf}\Tim Roy Performer Arp
DefaultGroupName=Tim Roy Performer Arp
UninstallDisplayIcon={app}\Tim Roy Performer Arp.exe
Compression=lzma
SolidCompression=yes
OutputBaseFilename=TimRoyPerformerArp_Setup
SetupIconFile=app-icon.ico

[Files]
Source: "dist\Tim Roy Performer Arp\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\Tim Roy Performer Arp"; Filename: "{app}\Tim Roy Performer Arp.exe"
Name: "{commondesktop}\Tim Roy Performer Arp"; Filename: "{app}\Tim Roy Performer Arp.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Tim Roy Performer Arp.exe"; Description: "Launch the application"; Flags: nowait postinstall skipifsilent 