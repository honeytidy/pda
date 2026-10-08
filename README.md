# 个人助理知识库（Personal Digital Assistant）

把电脑上散落的文档拖进窗口即入知识库，之后用自然语言提问，助理检索相关片段并生成带出处引用的答案，点击出处可打开原文件。

![PDA 功能演示：拖入文档收录、自然语言提问、带出处的回答](docs/pda-intro.webp)

▶ 完整介绍视频（含声音）：[docs/pda-intro.mp4](docs/pda-intro.mp4)

## 下载体验

Windows 安装包（约 166 MB，无需安装 Python，下载后双击安装）：

**[⬇ 下载最新版（v0.1.3）](https://github.com/honeytidy/pda/releases/download/v0.1.3/pda-v0.1.3.exe)**　·　[更新说明](https://github.com/honeytidy/pda/releases/latest)　·　[查看所有版本](https://github.com/honeytidy/pda/releases)

已安装的程序会自动检查新版本（启动时及每 6 小时），有新版时侧栏底部出现"新版本"入口，点击即可一键升级。

从源码运行见下文"安装"一节。

支持格式：**不限制文件类型**。

- txt / md / pdf / docx / xlsx / xlsm / xls / pptx / 图片（png / jpg / jpeg / bmp / webp，OCR 提取文字）会深度解析全文；
- 未知扩展名按纯文本尝试；
- 提取不出文字的二进制文件（如 exe、zip、psd）也会归档原件并按文件名建索引，可检索到、可从出处打开原件。

## 添加资料的六种方式

1. **剪贴板热键**：复制任意文本后按 **Ctrl+Shift+Q**，文本存为笔记（`data/notes/`）并自动入库。剪贴板为空时状态栏会提示。
2. **拖放**：拖入文件或整个文件夹（递归收集）即自动入库；入库在后台线程进行，不卡界面。
3. **监控文件夹**：侧栏底部"设置"里添加文件夹（如 Downloads），之后其中的新文档/修改过的文档（含浏览器下载完成后改名出现的文件）会自动收录（约 2 秒防抖，大小与修改时间连续稳定、文件可打开后才入库）。只收录程序运行期间出现或修改的文件，已有文件请拖入收录。监控文件夹只收支持深度解析的格式（txt / md / pdf / docx / xlsx / xlsm / xls / pptx / 图片），不收二进制文件，并跳过 Office 锁文件（`~$*`）、下载中的临时文件和隐藏文件；启动时不存在的文件夹（如未挂载的移动硬盘）每 60 秒重试。也可直接在 `pda_config.json` 里配置 `"watch_folders": ["D:/.../Download"]`。
4. **资源管理器热键**：在资源管理器中选中文件/文件夹（可多选）后按 **Ctrl+Shift+A**，直接收录。前台不是资源管理器时会提示"请先在资源管理器中选中要收录的文件"。
5. **呼出界面**：在任何程序里按 **Ctrl+Alt+Space** 弹出主界面并聚焦提问框，直接输入问题；界面在前台时再按一次收回托盘。
   以上三个全局快捷键可以改：主界面欢迎区的"全局快捷键 · 修改"，或侧栏底部"快捷键"。清空表示不启用；组合键必须带 Ctrl、Alt 或 Win，不能占用 Ctrl+C/V 等常用键，被其他程序占用时会提示换一个。保存在 `pda_config.json` 的 `hotkeys` 里，改完立即生效。
6. **网页链接**：在输入框粘贴一个 http(s) 链接并发送（整句只有一个裸 URL 时），自动抓取网页正文存为 markdown 收录。抓取失败（超时/403/无正文）会弹 toast 说明原因。
7. **右键菜单**（需一次性安装，见下文）：文件/文件夹上右键 → "添加到知识库助理"，主程序开着时秒收并弹 toast 反馈；没开时先在后台（托盘）启动主程序再收录。

问答：底部输入框输入问题，回车发送；答案下方列出可点击的出处（文档名 + 片段），点击用系统默认程序打开归档文件。

**限定范围提问**：问题里带"在XX里 / 只看XX / 《XX》"这类限定词时，先按文档标题/标签过滤候选文档再检索；匹配不到会提示"未找到限定范围，已全库检索"。文档标签在入库后由 AI 自动生成（配置了 API Key 才启用），显示在侧栏文档卡片上。

**托盘常驻**：关闭主窗口最小化到系统托盘（首次有 toast 提示），双击托盘图标恢复窗口；托盘菜单"退出"才真正退出。"设置"里可勾选"开机自动启动"。

- 首次使用会自动下载 embedding 模型（BAAI/bge-small-zh-v1.5，约 90MB），请留意状态栏提示。绿色版发布包已内置模型。
- 解析、切块、embedding、检索都在本地完成。配置了 API Key 后有两处会把内容发给 LLM：问答时发送检索到的片段；入库时发送文档前 1500 字用于生成标签（可在 `pda_config.json` 里设 `"auto_tags": false` 关闭）。

## 安装

```bash
pip install -r requirements.txt
```

> 图片 OCR 为可选组件，单独安装：`pip install -r requirements-ocr.txt`。`rapidocr-onnxruntime`：如果它或其依赖在你的 Python 版本上装不上，其他功能不受影响，只是拖入图片时会提示"OCR 组件不可用"。

## 运行

```bash
python run.py
```

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
