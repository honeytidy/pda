# 个人助理知识库（PDA）

把电脑上散落的文档拖进窗口即入知识库，之后用自然语言提问，助理检索相关片段并生成带出处引用的答案，点击出处可打开原文件。

支持格式：**不限制文件类型**。txt / md / pdf / docx / xlsx / xlsm / pptx / 图片（png / jpg / jpeg / bmp / webp，OCR 提取文字）会深度解析全文；未知扩展名按纯文本尝试；提取不出文字的二进制文件（如 exe、zip、psd）也会归档原件并按文件名建索引，可检索到、可从出处打开原件。

## 安装

```bash
pip install -r requirements.txt
```

> 图片 OCR 为可选组件，单独安装：`pip install -r requirements-ocr.txt`。`rapidocr-onnxruntime`：如果它或其依赖在你的 Python 版本上装不上，其他功能不受影响，只是拖入图片时会提示"OCR 组件不可用"。

## 运行

```bash
python run.py
```

添加资料的六种方式（步骤越少越好）：

1. **拖放**：拖入文件或整个文件夹（递归收集）即自动入库；入库在后台线程进行，不卡界面。
2. **监控文件夹**：侧栏底部"设置"里添加文件夹（如 Downloads），之后其中的新文档/修改过的文档（含浏览器下载完成后改名出现的文件）会自动收录（约 2 秒防抖，大小与修改时间连续稳定、文件可打开后才入库）。只收录程序运行期间出现或修改的文件，已有文件请拖入收录。监控文件夹只收支持深度解析的格式（txt / md / pdf / docx / xlsx / xlsm / pptx / 图片），不收二进制文件，并跳过 Office 锁文件（`~$*`）、下载中的临时文件和隐藏文件；启动时不存在的文件夹（如未挂载的移动硬盘）每 60 秒重试。也可直接在 `pda_config.json` 里配置 `"watch_folders": ["D:/.../Download"]`。
3. **剪贴板热键**：复制任意文本后按 **Ctrl+Shift+Q**，文本存为笔记（`data/notes/`）并自动入库。剪贴板为空时状态栏会提示。
4. **资源管理器热键**：在资源管理器中选中文件/文件夹（可多选）后按 **Ctrl+Shift+A**，直接收录。前台不是资源管理器时会提示"请先在资源管理器中选中要收录的文件"。
   以上两个全局快捷键可以改：主界面欢迎区的"全局快捷键 · 修改"，或侧栏底部"快捷键"。清空表示不启用；组合键必须带 Ctrl、Alt 或 Win，不能占用 Ctrl+C/V 等常用键，被其他程序占用时会提示换一个。保存在 `pda_config.json` 的 `hotkeys` 里，改完立即生效。
5. **网页链接**：在输入框粘贴一个 http(s) 链接并发送（整句只有一个裸 URL 时），自动抓取网页正文存为 markdown 收录。抓取失败（超时/403/无正文）会弹 toast 说明原因。
6. **右键菜单**（需一次性安装，见下文）：文件/文件夹上右键 → "添加到知识库助理"，主程序开着时秒收并弹 toast 反馈；没开时先在后台（托盘）启动主程序再收录。

问答：底部输入框输入问题，回车发送；答案下方列出可点击的出处（文档名 + 片段），点击用系统默认程序打开归档文件。

**限定范围提问**：问题里带"在XX里 / 只看XX / 《XX》"这类限定词时，先按文档标题/标签过滤候选文档再检索；匹配不到会提示"未找到限定范围，已全库检索"。文档标签在入库后由 AI 自动生成（配置了 API Key 才启用），显示在侧栏文档卡片上。

**托盘常驻**：关闭主窗口最小化到系统托盘（首次有 toast 提示），双击托盘图标恢复窗口；托盘菜单"退出"才真正退出。"设置"里可勾选"开机自动启动"。

- 首次使用会自动下载 embedding 模型（BAAI/bge-small-zh-v1.5，约 90MB），请留意状态栏提示。绿色版发布包已内置模型。
- 解析、切块、embedding、检索都在本地完成。配置了 API Key 后有两处会把内容发给 LLM：问答时发送检索到的片段；入库时发送文档前 1500 字用于生成标签（可在 `pda_config.json` 里设 `"auto_tags": false` 关闭）。

### 重复收录行为

按"来源路径 + 修改时间 + 大小"判断：**完全相同的文件重复添加会跳过**；**同路径但内容有变化的文件会覆盖更新**（删除旧的索引与归档，重新入库），知识库中始终保留最新版本，不会产生重复条目。

## Windows 右键菜单

安装（per-user 注册表，不需要管理员权限）：

```bash
python scripts/install_context_menu.py
```

之后在任意文件或文件夹上右键即可看到"添加到知识库助理"。行为：

- **主程序开着**：路径通过 IPC 直接转发给主程序秒收，不弹任何窗口、无命令行黑窗；收录完成后屏幕中央弹出一条 toast 提示（✓ 成功 / ✘ 失败，2.5 秒自动消失），聊天区同时发收录通知。
- **主程序没开**：在后台启动主程序（只出现托盘图标，不弹主窗口），就绪后转发收录，首次约需 5-6 秒。多选右键时只有一个进程负责拉起，其余等待转发；全程只有主程序一个进程写库。60 秒内没能启动会弹 toast 提示。

卸载：

```bash
python scripts/uninstall_context_menu.py
```

另外，主程序运行中（包括启动过程中）再次双击 `run.py` 不会开第二个实例，只会激活已有窗口。单实例由命名互斥体（名字含数据目录哈希，不同数据目录的副本互不干扰）加 `data/.instance.lock` 文件锁保证。IPC 只监听 127.0.0.1 上系统分配的端口，端口与随机 token 写在 `data/ipc_token`（JSON，ACL 仅当前用户可读）；收到的路径必须是存在的绝对路径，拒绝 UNC 网络路径和整盘根目录。

注册的是经典右键菜单（`HKCU\Software\Classes\*\shell\...`），在 Win11 上出现在"显示更多选项"里（见下文"Windows 11 注意"）。项目不做 Win11 原生顶级菜单：那需要 MSIX 打包身份 + COM 服务器 + 正式代码签名证书，维护和分发成本远高于收益；拖放和 Ctrl+Shift+A 已经覆盖快速收录。

多选：经典菜单写了 `MultiSelectModel=Player`，去掉"超过 15 项就不显示菜单"的限制，但 Explorer 仍会每项起一个进程（各自经 IPC 转交后秒退）。一次上百个文件建议直接拖进主窗口。

## 打包 exe（绿色软件）

```bash
python scripts/build_exe.py
```

脚本优先用项目下 `.venv`（干净环境）打包；venv 里 PyPI 版 PySide6 在本机损坏（Qt6Core WinError 127，静态分析无解）时自动回退当前 anaconda 解释器（conda 版 Qt 工作正常）。产物布局：

```
dist/pda/
  pda.exe        # 原生启动器（C# WinForms，用 .NET Framework 自带 csc 编译，~26KB）：
                        # 双击毫秒级弹 splash（实测 ~230ms），拉起 main.exe，
                        # 主窗口出现后自动关闭；带参数（--add 等）时不弹 splash、直接转发秒退
  main.exe   # 应用本体（PyInstaller onedir）
  _internal/            # Python 运行时与依赖
```

`dist/pda/` 解压后约 460MB（安装包经 LZMA 压缩后约 166MB）。双击用 `pda.exe`；右键菜单指向 `main.exe --add`（不经启动器）。数据位置按下文「数据目录」的规则判定：刚构建出的 `dist/pda` 里有 `data/`（build_exe.py 放入的内置模型 `data/model_cache`），因此按便携版处理，数据写在 exe 旁 `data/`。

> 注意：在开发机上用过 `dist/pda` 后，`dist/pda/data/` 里就是你的真实知识库（pda.db、归档原件、笔记）。分发请用 `python scripts/make_release.py` 生成的 zip（只带 `model_cache`），不要直接拷贝/压缩 `dist/pda` 或整个项目目录。开发时建议设 `PDA_DATA_DIR` 把数据放到项目外。

构建只用 `scripts/build_exe.py`（不要直接跑 pyinstaller：会整目录覆盖 `dist/pda`，含 `data/`）。构建脚本在找不到 csc 时直接报错退出；项目 `.venv` 不可用时默认报错，需显式加 `--allow-conda` 才用当前解释器打包，且不会自动往全局环境装包。内置语义模型暂存在 `build_model_cache/`（缺失时先从 `dist/pda/data/model_cache` 复制，再不行用构建解释器下载），构建后放进 `dist/pda/data/model_cache`；安装包和便携 zip 缺模型时直接报错。

图标（眼睛 logo = 横放的 θ）：`pda/ui/icon.py` 用 QPainter 绘制，splash/窗口/托盘共用；`scripts/make_icon.py` 渲染多尺寸 PNG 并打包为 `src/pda.ico`（纯标准库 ICO 容器），启动器经 csc `-win32icon` 嵌入、应用本体经 PyInstaller `--icon` 嵌入。改动 logo 后依次跑 `make_icon.py` → `build_exe.py`。

体积构成与裁剪（1.1GB → 约 460MB）：MKL 换 PyPI numpy/OpenBLAS（`build_deps/` 通过 PYTHONPATH 优先，-330MB）、剔除 botocore（-114MB）与 chromadb 服务端依赖（kubernetes/uvicorn/fastapi/grpc 无关部分等）、opencv 换 headless 等。注意几个不能裁的：`opentelemetry`/`grpc`（chromadb/__init__ 模块层 import）、`posthog`（exclude 后缀匹配会误伤 chromadb.telemetry.product.posthog）、`hf_xet`（huggingface_hub 1.x 硬依赖，裁了模型下载失败）；chromadb 必须 `--collect-all`（api.rust 等懒加载子模块静态图收不全），图爆炸靠 exclude 清单切断。

- 启动体感：启动器 splash ~0.2s 出现，应用主窗口约 5-6s；IPC 类调用（`--add` 转发、二次激活）约 0.6s。
- conda Python 的 `_ssl` 等扩展依赖 `Library\bin` 的 OpenSSL 等 DLL，构建脚本已通过 `--add-binary` 打包；若升级 Python/依赖后 exe 静默退出（exit 1 无输出），先看 exe 旁 `data/pda_error.log`（未捕获异常会记录到那里），再检查是否有新的缺失 DLL。
- anaconda base 环境包多，构建脚本用一串 `--exclude-module` 切断 `fsspec.gui → panel → playwright` 这类无关依赖链，新增依赖时如图异常膨胀往这里加。`build_deps/` 的准备命令（版本固定，numpy 与 requirements.txt 一致）：`python -m pip install --target=build_deps numpy==2.5.3 opencv-python-headless==5.0.0.93`。

打包后要让右键菜单指向 exe，需显式指定：`python scripts/install_context_menu.py --exe dist/pda/main.exe`（脚本不自动探测构建目录，避免把开发机路径写进注册表；不带 `--exe` 时注册 pythonw + run.py）。绿色版用户直接双击包里的"安装右键菜单.bat"。

**Windows 11 注意**：Win11 的新版右键菜单会把本项折叠到"**显示更多选项**"里（或按住 Shift 再右键直接出经典菜单）。如想让它出现在顶级菜单，恢复经典右键菜单样式（影响所有右键菜单，随时可还原）：

```bash
python scripts/restore_classic_menu.py         # 启用经典菜单（会询问是否重启资源管理器）
python scripts/restore_classic_menu.py --off   # 还原为 Win11 新版菜单
```

启动器计时日志默认关闭：设环境变量 `PDA_MENU_LOG=1` 后写入 `%TEMP%\pda_launcher.log`。

旧版本装过 Win11 原生菜单（MSIX 包）的机器，可用 `powershell "Get-AppxPackage -Name PDA.KnowledgeAssistant | Remove-AppxPackage"` 卸载。

## 配置 LLM（可选）

不配置也能用：此时问答只返回检索到的原文片段。最简单的方式是在主窗口侧栏底部"设置"里选服务商、粘贴 API Key，点保存即可：

- 接口地址按服务商预设（Kimi / DeepSeek / 通义千问 / 智谱 / 硅基流动 / OpenAI / OpenRouter / Claude，见 `pda/llm.py` 的 `PROVIDERS`）；OpenRouter、Claude、OpenAI 项目 Key、智谱 Key 粘贴时会按格式自动选中服务商
- 保存时只向所选服务商验证一次 Key（优先调 `/models`，不消耗额度），并从账户可用模型里按偏好自动挑选；验证失败不关闭窗口、直接提示原因；断网时可选择先保存
- 保存后立即生效，不用重启
- 模型：默认"自动"，每次启动后按账户 /models 列表选最新的通用对话模型（服务商下线旧模型时自动换到新的）；想固定某个模型就从下拉框选，或直接输入
- 其他 OpenAI 兼容接口：服务商选"其他"，在"高级设置"里填接口地址

配置来源（优先级从高到低）：

1. 环境变量：`PDA_API_KEY`、`PDA_BASE_URL`、`PDA_MODEL`（设置界面会提示哪些项被环境变量覆盖）
2. `pda_config.json`（设置界面写入的就是它；源码运行/便携版在程序目录，安装版在 `%LOCALAPPDATA%\PDA\`）：

```json
{
  "api_key": "sk-...",
  "base_url": "https://api.moonshot.cn/v1",
  "model": "kimi-k2-0905-preview",
  "watch_folders": ["D:/Users/me/Downloads"]
}
```

默认 base_url 为 Moonshot（`https://api.moonshot.cn/v1`），默认模型 `kimi-k2-0905-preview`，任何 OpenAI 兼容接口均可。

## 数据

内容：归档文件 `data/files/`、剪贴板笔记 `data/notes/`、SQLite `data/pda.db`、向量库 `data/chroma/`、错误日志 `data/pda_error.log`。位置取决于运行方式（`pda/config.py`）：

| 运行方式 | 判定 | 数据与 `pda_config.json` 位置 |
|---|---|---|
| 源码 `python run.py` | 非打包 | 项目目录 `./data/` |
| 安装版 | exe 旁有 `installed.flag`（安装包写入，优先级最高） | `%LOCALAPPDATA%\PDA\` |
| 便携版 zip | exe 旁有 `portable.flag`（`make_release.py` 放入），或已有 `data/` 目录（老版本绿色包） | exe 旁 `data/` |
| 安装版 | 以上都没有 | `%LOCALAPPDATA%\PDA\`（卸载时询问是否删除） |

环境变量 `PDA_DATA_DIR` / `PDA_CONFIG_PATH` 可覆盖。chromadb 的匿名遥测已在代码里关闭。

## 发布给其他人

1. `python scripts/build_exe.py` 生成 `dist/pda/`（加 `--installer` 会在最后直接调用 ISCC 出安装包）
2. `python scripts/make_notices.py`（用打包时的同一个解释器）生成 `THIRD_PARTY_NOTICES.txt`
3. 二选一或都做：
   - 便携 zip：`python scripts/make_release.py` → `dist/知识库助理_portable.zip`
   - 安装包：安装 [Inno Setup 7](https://jrsoftware.org/isinfo.php) 后 `"D:\Programs\Inno Setup 7\ISCC.exe" /DAppVersion=0.1.0 packaging\installer.iss` → `dist/知识库助理_安装包_0.1.0.exe`（版本号应与 `pda/__init__.py` 的 `__version__` 一致，`build_exe.py --installer` 会自动传入）。卸载时先用 `main.exe --quit` 让程序正常退出，超时才强制结束。per-user 安装到 `%LOCALAPPDATA%\Programs\PDA`，无需管理员；带开始菜单、可选桌面快捷方式与右键菜单、"设置 → 应用"里的卸载项
4. 代码签名（强烈建议）：未签名的 exe 在别人电脑上会被 SmartScreen 拦截。拿到证书后先签 `dist/pda/pda.exe`、`main.exe`，再在 `installer.iss` 里启用 `SignTool=`
5. 在干净的 Windows 沙盒里走一遍：安装/解压 → 启动 → 拖入文件 → 右键收录 → 设置 API Key 问答 → 卸载

## 冒烟测试

```bash
python scripts/smoke_test.py        # 基础：txt/docx 入库 + 混合检索
python scripts/smoke_test_v11.py    # V1.1：xlsx/pptx/图片 OCR、重复收录、watcher、剪贴板笔记
```

均使用独立临时数据目录，不污染 `./data`，不需要 API Key。

UI 预览截图：`python scripts/ui_preview.py` 生成 `scripts/ui_preview_empty.png`（空状态欢迎面板）与 `scripts/ui_preview.png`（对话状态）。
