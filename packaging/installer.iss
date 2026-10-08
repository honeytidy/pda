; 知识库助理 —— Inno Setup 7 安装脚本（per-user，无需管理员）
;
; 构建：python scripts/build_exe.py --installer（构建 dist/pda 后自动调用 ISCC，
;   版本号取 pda/__init__.py 的 __version__）；或手动：
;   "D:\Programs\Inno Setup 7\ISCC.exe" /DAppVersion=0.2.0 packaging\installer.iss
; 产物：dist\知识库助理_安装包_<版本>.exe
;
; 布局：程序装到 %LOCALAPPDATA%\Programs\PDA；安装目录里没有 data\ 也没有 portable.flag，
; 所以 pda/config.py 判定为安装版，用户数据写 %LOCALAPPDATA%\PDA（卸载不删，升级不丢）。
; dist\pda\data\ 下只取 model_cache（内置语义模型，build_exe.py 负责放入）装到
; {app}\model_cache，缺模型时编译直接报错；开发机上的知识库（pda.db / chroma /
; files / notes）绝不进安装包。
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
VersionInfoVersion={#AppVersion}
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
Source: "{#DistDir}\data\model_cache\*"; DestDir: "{app}\model_cache"; Flags: ignoreversion recursesubdirs createallsubdirs
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
; 升级安装时取消了右键菜单任务：删掉旧版本留下的键
Root: HKCU; Subkey: "Software\Classes\*\shell\0AddToPDA"; ValueType: none; Tasks: not contextmenu; Flags: deletekey dontcreatekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\0AddToPDA"; ValueType: none; Tasks: not contextmenu; Flags: deletekey dontcreatekey
; 卸载时清掉开机自启（值由程序设置界面写入，安装时不创建）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "PDA"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\pda.exe"; Description: "立即运行{#AppName}"; Flags: nowait postinstall skipifsilent
; 程序内自动升级：pda/updater.py 以 /SILENT /RELAUNCH 启动安装包，装完自动重新打开
Filename: "{app}\pda.exe"; Flags: nowait; Check: ShouldRelaunch

[UninstallRun]
; 先让运行中的实例正常退出（托盘常驻），否则文件被占用删不掉：
; main.exe --quit 经 IPC 通知主程序走正常退出流程（等收录写完），最多等约 10 秒
Filename: "{app}\main.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitMain"
; 兜底：仍未退出的强制结束。只结束本安装目录下的 main.exe（别的软件也可能叫 main.exe）；
; 用 StartsWith 比较路径（-like 会把 [ ] 当通配符），单引号由 PsQuotedApp 转义
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -Command ""Get-Process main -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and $_.Path.StartsWith('{code:PsQuotedApp}\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force"""; Flags: runhidden; RunOnceId: "KillMain"

[UninstallDelete]
Type: files; Name: "{app}\installed.flag"

[Code]
// PowerShell 单引号字符串里的 ' 要写成 ''（Windows 路径里不会出现 "）
function PsQuotedApp(Param: String): String;
begin
  Result := ExpandConstant('{app}');
  StringChangeEx(Result, '''', '''''', True);
end;

// 命令行带 /RELAUNCH（程序内自动升级）且是静默安装时，装完重新启动程序
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (Pos('/RELAUNCH', UpperCase(GetCmdTail)) > 0);
end;

// 覆盖安装前先让运行中的旧版正常退出（等收录写完、释放 DLL），最多等约 10 秒；
// 仍未退出的由 CloseApplications（Restart Manager）兜底
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\main.exe')) then
    Exec(ExpandConstant('{app}\main.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

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
      // /SUPPRESSMSGBOXES 静默卸载时不弹框，按默认"否"保留数据
      if SuppressibleMsgBox('是否同时删除知识库数据（已收录的文档、索引、设置和 API Key）？' + #13#10 +
                DataDir + #13#10#13#10 + '选"否"则保留，重新安装后可继续使用。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
