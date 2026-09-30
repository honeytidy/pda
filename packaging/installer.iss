; 知识库助理 —— Inno Setup 6 安装脚本（per-user，无需管理员）
;
; 构建：先 python scripts/build_exe.py 生成 dist/pda，然后
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;   可覆盖版本号：ISCC /DAppVersion=0.2.0 packaging\installer.iss
; 产物：dist\知识库助理_安装包_<版本>.exe
;
; 布局：程序装到 %LOCALAPPDATA%\Programs\PDA；安装目录里没有 data\ 也没有 portable.flag，
; 所以 pda/config.py 判定为安装版，用户数据写 %LOCALAPPDATA%\PDA（卸载不删，升级不丢）。
; dist\pda\data\ 下只取 model_cache（内置语义模型）装到 {app}\model_cache，
; 开发机上的知识库（pda.db / chroma / files / notes）绝不进安装包。
;
; 代码签名：设置 SignTool 后取消下面 SignTool= 一行的注释，例如在 Inno IDE
; 「Tools → Configure Sign Tools」里登记 name=pdasign，命令：
;   signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f 证书.pfx /p 密码 $f
; 同时应先用同一证书对 dist\pda\pda.exe、main.exe 签名，再打安装包。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "知识库助理"
#define DistDir "..\dist\pda"

[Setup]
; AppId 固定不变：升级安装靠它识别同一产品
AppId={{3F6B2A4E-7C1D-4E8B-9A25-5D0C8E1F4B72}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PDA
DefaultDirName={localappdata}\Programs\PDA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=知识库助理_安装包_{#AppVersion}
SetupIconFile=..\src\pda.ico
UninstallDisplayIcon={app}\pda.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 运行中的主程序会锁住 _internal 下的 DLL：安装/卸载前提示关闭
CloseApplications=yes
RestartApplications=no
;SignTool=pdasign

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "contextmenu"; Description: "添加右键菜单「添加到知识库助理」"; GroupDescription: "附加任务："

[Files]
Source: "{#DistDir}\pda.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\main.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistDir}\data\model_cache\*"; DestDir: "{app}\model_cache"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[InstallDelete]
; 升级时清掉旧版 _internal，避免残留旧依赖
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\pda.exe"
Name: "{group}\卸载{#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\pda.exe"; Tasks: desktopicon

[Registry]
; 经典右键菜单（与 scripts/install_context_menu.py、make_release.py 的 bat 同一键名）
Root: HKCU; Subkey: "Software\Classes\*\shell\0AddToPDA"; ValueType: string; ValueName: ""; ValueData: "添加到知识库助理"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\0AddToPDA"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\main.exe"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\*\shell\0AddToPDA"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\*\shell\0AddToPDA\command"; ValueType: string; ValueName: ""; ValueData: """{app}\main.exe"" --add ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\0AddToPDA"; ValueType: string; ValueName: ""; ValueData: "添加到知识库助理"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\0AddToPDA"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\main.exe"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\0AddToPDA"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\0AddToPDA\command"; ValueType: string; ValueName: ""; ValueData: """{app}\main.exe"" --add ""%1"""; Tasks: contextmenu
; 卸载时清掉开机自启（值由程序设置界面写入，安装时不创建）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PDA"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\pda.exe"; Description: "立即运行{#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 先让运行中的实例退出（托盘常驻），否则文件被占用删不掉
; 只结束本安装目录下的 main.exe（别的软件也可能叫 main.exe，不能按进程名一刀切）
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process main -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\*' } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "KillMain"

[UninstallDelete]
Type: files; Name: "{app}\installed.flag"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // 安装版标记：pda/config.py 见到它就把数据放 %LOCALAPPDATA%\PDA（优先于"exe 旁有 data\"的便携判定）
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\installed.flag'),
      'Installed by setup. User data lives in %LOCALAPPDATA%\PDA.' + #13#10, False);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\PDA');
    if DirExists(DataDir) then
      if MsgBox('是否同时删除知识库数据（已收录的文档、索引、设置和 API Key）？' + #13#10 +
                DataDir + #13#10#13#10 + '选"否"则保留，重新安装后可继续使用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
