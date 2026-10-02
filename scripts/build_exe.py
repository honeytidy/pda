# -*- coding: utf-8 -*-
"""打包脚本：PyInstaller 构建应用本体（onedir + windowed），再编译原生启动器。

产物：dist/pda/pda.exe（启动器）+ main.exe（应用本体）。

优先用项目下 .venv（干净环境）。venv 里的 PySide6 不可用时（本机 .venv 以 conda
的 python 为基础，conda 把 Library\bin 加进 DLL 搜索路径，PyPI 版 Qt6Core 要的
系统 icuuc.dll 被解析成 conda 的旧版 ICU → WinError 127），需显式加 --allow-conda 才回退到当前解释器
（anaconda base 的 conda 版 PySide6 6.11.0 实测工作正常）：换解释器会得到
依赖版本不同的产物，不能悄悄回退。

内置语义模型：构建前确保 build_model_cache/ 下有 bge-small-zh 模型缓存（缺失时
先从 dist/pda/data/model_cache 复制，再不行用构建解释器下载），构建后放进
dist/pda/data/model_cache。安装包和便携 zip 都从那里取模型；缺模型时 ISCC 直接
报错，不会静默打出不带模型的包。

用法：python scripts/build_exe.py [--allow-conda] [--installer]
  --installer：构建完成后调用 ISCC 生成安装包（版本号取 pda/__init__.py 的 __version__）
"""
import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
# 固定为 venv 中已验证的版本，避免构建漂移
PYINSTALLER_REQ = "pyinstaller==6.22.3"

HIDDEN = [
    # 函数内延迟 import 的模块，PyInstaller 静态分析找不到
    "pypdf", "docx", "openpyxl", "pptx", "fastembed", "onnxruntime",
    "rapidocr_onnxruntime", "cv2", "watchdog", "readability", "lxml",
    "win32com", "pythoncom", "requests",
]
COLLECT_ALL = [
    # 包内带数据文件（onnx 模型等），整体收集
    "rapidocr_onnxruntime", "fastembed",
]
# build_deps/ 里放 PyPI 版 numpy（OpenBLAS ~21MB，替代 anaconda MKL ~350MB）
# 和 opencv-python-headless，--paths 让它们优先于 anaconda 环境里的同名包。
# 准备（numpy 与 requirements.txt 一致，opencv 固定为已验证版本）：
#   python -m pip install --target=build_deps numpy==2.5.3 opencv-python-headless==5.0.0.93
BUILD_DEPS = ROOT / "build_deps"
BUILD_DEPS_REQS = ("numpy==2.5.3", "opencv-python-headless==5.0.0.93")

# 内置语义模型的暂存目录（不放 build/：PyInstaller --clean 会清 workpath）
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_STAGE = ROOT / "build_model_cache"
DIST_MODEL = ROOT / "dist" / "pda" / "data" / "model_cache"
# 与 pda/embeddings.py::_model_cached 的判断一致
_MODEL_DIRS = ("models--Qdrant--bge-small-zh-v1.5", "fast-bge-small-zh-v1.5")

# ISCC 查找顺序：环境变量 ISCC → 以下位置（Inno Setup 7）
ISCC_CANDIDATES = [
    r"D:\Programs\Inno Setup 7\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    r"C:\Program Files\Inno Setup 7\ISCC.exe",
]

EXCLUDES = [
    # anaconda base 同时装了 PyQt6，PyInstaller 不允许一个应用里混两种 Qt 绑定
    "PyQt6", "PyQt5", "PySide2", "tkinter",
    # 依赖图爆炸的根源：fsspec.gui → panel → holoviews/bokeh/nbconvert/playwright…
    # 从根部切断（均为可选功能，本应用用不到）
    "fsspec.gui", "panel", "nbconvert", "playwright", "chromadb.test",
    # chromadb 服务端/云侧可选依赖（已验证屏蔽后 PersistentClient 正常；
    # 注意 opentelemetry 和 grpc 不能裁——chromadb/__init__ 模块层会 import；
    # posthog 也不能裁——exclude 按后缀匹配会误伤 chromadb.telemetry.product.posthog，
    # 导致收录在 vectorstore 写入阶段报 No module named 而失败）
    "kubernetes", "uvicorn", "fastapi", "starlette", "watchfiles", "websockets",
    "httptools", "typer",
    # huggingface_hub 的 S3 可选后端
    # （hf_xet 不能裁——它是 huggingface_hub 1.x 的硬依赖，裁了模型下载直接失败）
    "botocore", "aiobotocore", "boto3", "s3fs",
    # 大杂烩双保险
    "pytest", "sphinx", "jedi", "black", "yapf", "nbformat", "IPython",
    "matplotlib", "scipy", "pandas", "notebook", "docutils",
    "dask", "distributed", "xarray", "numba", "sympy", "bokeh", "holoviews",
    "sklearn", "statsmodels", "seaborn", "h5py", "mpi4py", "intake", "hvplot",
    "datashader", "altair", "ipywidgets", "pyarrow", "mypy", "skimage",
    "pywt", "networkx", "nbclient", "bleach", "jupyter_server", "ipympl",
    "colorcet", "flask", "asyncssh", "zict", "lmdb", "narwhals",
    # Jupyter 内核全家桶：只经 loguru._colorama 的 try/except 可选导入进来
    "ipykernel", "jupyter_client", "jupyter_core", "zmq", "tornado", "debugpy",
    "traitlets", "dill", "pexpect", "ptyprocess",
    # 仅 httpx/httpx2 的命令行入口（_main，有 ImportError 兜底）和 pydantic 调试打印用
    "rich", "pygments",
    # 仅 pptx 生成图表时用（pptx.chart.xlsx），本应用只读 pptx
    "xlsxwriter",
]

# PyInstaller 跑完后从 dist/pda/_internal 删掉的文件（相对 _internal）。
# 均已确认运行时不会被加载：
PRUNE = [
    # OpenCV 视频解码后端，按需 LoadLibrary；OCR 只用 imread/imdecode
    "cv2/opencv_videoio_ffmpeg*.dll",
    # numpy 的 OpenBLAS 在 numpy.libs/ 下已有一份（delvewheel 从那里加载），根目录这份是重复收集
    "libscipy_openblas64_*.dll",
    # Qt：只用 QtCore/QtGui/QtWidgets。网络、PDF、离屏/最小/Direct2D 平台插件都用不到
    "PySide6/QtNetwork.*.pyd", "Qt6Network.dll", "Qt6Pdf.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/tls", "PySide6/plugins/networkinformation",
    "PySide6/plugins/generic",
    "PySide6/plugins/platforms/qdirect2d.dll",
    "PySide6/plugins/platforms/qoffscreen.dll",
    "PySide6/plugins/platforms/qminimal.dll",
]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT, **kw)


def pick_builder_python(allow_conda: bool) -> Path:
    """优先 venv；venv 的 PySide6 不可用时，只有 --allow-conda 才回退当前解释器。"""
    if VENV_PY.is_file():
        r = subprocess.run(
            [str(VENV_PY), "-c", "import PySide6.QtCore"],
            capture_output=True,
        )
        if r.returncode == 0:
            print("使用 venv 打包")
            return VENV_PY
        print("venv 的 PySide6 不可用")
    fallback = Path(sys.executable)
    if not allow_conda:
        sys.exit(f"错误：项目 .venv 不可用。确认要用当前解释器（{fallback}）打包时，\n"
                 "  加 --allow-conda 重新运行（产物依赖版本以该环境为准）。")
    print("=" * 60)
    print(f"警告：未使用项目 .venv，改用当前解释器打包：{fallback}")
    print("      产物会带上该环境里的包，体积和依赖版本可能与预期不同。")
    print("=" * 60)
    return fallback


def ensure_pyinstaller(builder: Path) -> None:
    """venv 里缺 pyinstaller 就装；回退到的全局解释器绝不自动 pip install（会改用户环境）。"""
    r = subprocess.run([str(builder), "-m", "PyInstaller", "--version"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(f"PyInstaller {r.stdout.strip()}")
        return
    if builder == VENV_PY:
        run([builder, "-m", "pip", "install", "-q", PYINSTALLER_REQ])
        return
    sys.exit(f"错误：构建解释器 {builder} 没有安装 PyInstaller。\n"
             "  本脚本不会自动往全局/conda 环境里装包。请确认后手动执行：\n"
             f"  \"{builder}\" -m pip install {PYINSTALLER_REQ}")


def builder_base_prefix(builder: Path) -> Path:
    """构建解释器的 sys.base_prefix（venv 指向其基础安装，conda DLL 在那里）。"""
    r = subprocess.run(
        [str(builder), "-c", "import sys; print(sys.base_prefix)"],
        capture_output=True, text=True, check=True,
    )
    return Path(r.stdout.strip())


def find_csc() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / \
        "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def build_launcher() -> bool:
    """编译 C# 原生启动器（.NET Framework 自带 csc，无需 SDK）。

    产物布局：dist/pda/main.exe（应用本体）+ dist/pda/pda.exe（启动器）。
    双击启动器毫秒级弹 splash；右键菜单直接调 main.exe --add。
    """
    csc = find_csc()
    if not csc.is_file():
        # 不能跳过：右键菜单 / 安装包 / make_release 都依赖 main.exe + pda.exe 布局
        sys.exit(f"错误：未找到 {csc}（.NET Framework 4.x 自带的 C# 编译器）。\n"
                 "  启动器 pda.exe 无法编译，产物布局不完整。请启用 .NET Framework 4.8 后重试。")
    app_exe = ROOT / "dist" / "pda" / "pda.exe"
    renamed = app_exe.with_name("main.exe")
    if app_exe.is_file():
        if renamed.is_file():
            renamed.unlink()
        app_exe.rename(renamed)
    out = ROOT / "dist" / "pda" / "pda.exe"
    ico = ROOT / "src" / "pda.ico"
    cmd = [csc, "-nologo", "-target:winexe", "-out:" + str(out),
           str(ROOT / "src" / "launcher.cs"),
           "-r:System.dll", "-r:System.Drawing.dll", "-r:System.Windows.Forms.dll"]
    if ico.is_file():
        cmd.append("-win32icon:" + str(ico))
    run(cmd)
    print(f"启动器编译完成：{out}")
    return True


def _has_model(cache: Path) -> bool:
    return cache.is_dir() and any((cache / n).is_dir() for n in _MODEL_DIRS)


def _build_env() -> dict:
    env = _build_env()
    return env


def ensure_model_staged(builder: Path) -> None:
    """确保 MODEL_STAGE 下有模型缓存。放在 PyInstaller 之前：缺模型尽早失败。"""
    if _has_model(MODEL_STAGE):
        print(f"内置模型：{MODEL_STAGE}")
        return
    if _has_model(DIST_MODEL):
        print(f"从 {DIST_MODEL} 复制模型到 {MODEL_STAGE}")
        shutil.copytree(DIST_MODEL, MODEL_STAGE, dirs_exist_ok=True)
        return
    print(f"下载语义模型 {MODEL_NAME} 到 {MODEL_STAGE}（约 90MB）...")
    MODEL_STAGE.mkdir(parents=True, exist_ok=True)
    code = ("import sys; from fastembed import TextEmbedding; "
            "TextEmbedding(model_name=sys.argv[1], cache_dir=sys.argv[2])")
    run([builder, "-c", code, MODEL_NAME, MODEL_STAGE], env=_build_env())
    if not _has_model(MODEL_STAGE):
        sys.exit(f"错误：模型下载后仍未在 {MODEL_STAGE} 找到 {_MODEL_DIRS}")


def bundle_model() -> None:
    """把暂存模型放进 dist/pda/data/model_cache（安装包 / 便携 zip 都从这里取）。"""
    if _has_model(DIST_MODEL):
        return
    shutil.copytree(MODEL_STAGE, DIST_MODEL, dirs_exist_ok=True)
    print(f"已内置模型：{DIST_MODEL}")


def app_version() -> str:
    ns = {}
    exec((ROOT / "pda" / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"]


def find_iscc() -> Path | None:
    for c in [os.environ.get("ISCC")] + ISCC_CANDIDATES:
        if c and Path(c).is_file():
            return Path(c)
    return None


def build_installer() -> None:
    iscc = find_iscc()
    if iscc is None:
        sys.exit("错误：未找到 ISCC.exe（Inno Setup 7）。可设环境变量 ISCC 指向它。")
    run([iscc, f"/DAppVersion={app_version()}", ROOT / "packaging" / "installer.iss"])


def prune_dist() -> None:
    internal = ROOT / "dist" / "pda" / "_internal"
    freed = 0
    for pattern in PRUNE:
        for p in internal.glob(pattern):
            if p.is_dir():
                size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p)
            else:
                size = p.stat().st_size
                p.unlink()
            freed += size
            print(f"  裁剪 {p.relative_to(internal)}（{size / 2**20:.1f} MB）")
    print(f"裁剪完成，共减少 {freed / 2**20:.1f} MB（未压缩）")


def main():
    ap = argparse.ArgumentParser(description="PDA 打包脚本")
    ap.add_argument("--allow-conda", action="store_true",
                    help=".venv 不可用时允许回退到当前（conda）解释器打包")
    ap.add_argument("--installer", action="store_true",
                    help="构建完成后调用 ISCC 生成安装包")
    args = ap.parse_args()
    # 先检查 csc：否则 PyInstaller 跑完十几分钟才发现启动器编译不了
    if not find_csc().is_file():
        sys.exit(f"错误：未找到 {find_csc()}（.NET Framework 4.x 自带的 C# 编译器），"
                 "无法编译启动器 pda.exe。请启用 .NET Framework 4.8 后重试。")
    if not VENV_PY.is_file():
        print("创建 venv（避免 anaconda 环境污染 / 体积爆炸）...")
        venv.create(VENV, with_pip=True)
        run([VENV_PY, "-m", "pip", "install", "-U", "pip"])
        run([VENV_PY, "-m", "pip", "install", "-r", "requirements.txt",
             "-r", "requirements-ocr.txt", PYINSTALLER_REQ, "pywin32"])
    if not BUILD_DEPS.is_dir():
        print("提示：未找到 build_deps/，numpy/opencv 直接用构建解释器里的版本。准备方法：")
        print(f"  python -m pip install --target=build_deps {' '.join(BUILD_DEPS_REQS)}")

    builder = pick_builder_python(args.allow_conda)
    ensure_pyinstaller(builder)
    ensure_model_staged(builder)

    cmd = [
        builder, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir", "--windowed",
        "--name", "pda",
        "--icon", str(ROOT / "src" / "pda.ico"),
        "--distpath", "dist", "--workpath", "build",
        # 生成的 spec 放进 build/；本脚本是唯一权威构建入口
        "--specpath", "build",
    ]
    env = dict(os.environ)
    if BUILD_DEPS.is_dir():
        # 让 PyPI numpy(OpenBLAS)/opencv-headless 在分析时优先于 anaconda 同名包。
        # 注意必须用 PYTHONPATH（sys.path 前部），--paths 实测被追加到搜索路径末尾
        env["PYTHONPATH"] = str(BUILD_DEPS) + os.pathsep + env.get("PYTHONPATH", "")
    # PYZ/PKG 内部不做 zlib 压缩：main.exe 变大，但交给安装包/zip 的 LZMA 整体压缩
    # 反而更小（已压缩的数据 LZMA 压不动）。运行时解压逻辑不变（level 0 仍是合法 zlib 流）
    env["PYINSTALLER_ZLIB_COMPRESSION_LEVEL"] = "0"
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for c in COLLECT_ALL:
        cmd += ["--collect-all", c]
    # anaconda 的 Python 扩展（_ssl/_ctypes/_sqlite3/_lzma/_bz2/pyexpat/zlib）
    # 依赖的第三方 DLL 在 Library\bin，PyInstaller 不会自动带上，需显式加入
    # 必须按实际构建解释器计算（而不是运行本脚本的 sys.executable）
    conda_bin = builder_base_prefix(builder) / "Library" / "bin"
    for dll in ("libssl-3-x64.dll", "libcrypto-3-x64.dll", "ffi.dll",
                "liblzma.dll", "sqlite3.dll", "libexpat.dll", "zlib.dll",
                "bz2.dll"):
        src = conda_bin / dll
        if src.is_file():
            cmd += ["--add-binary", f"{src};."]

    # chromadb 需要 collect-all（它的 api.rust/telemetry 等子模块是懒加载，
    # 静态图收不全；collect-data 只收非 py 文件会导致收录时报 No module named）。
    # 图爆炸由 EXCLUDES 里的 chromadb.test/pytest/panel 等切断，不会拖进大杂烩。
    cmd += ["--collect-all", "chromadb"]
    for e in EXCLUDES:
        cmd += ["--exclude-module", e]
    cmd.append(str(ROOT / "run.py"))

    # PyInstaller 会整体删除 dist/pda！data/（知识库数据）必须先备份、构建后恢复
    data_dir = ROOT / "dist" / "pda" / "data"
    backup = ROOT / "dist" / "pda_data_backup"
    if backup.exists():
        # 残留备份可能是上次构建中断时唯一的数据副本，绝不自动删除
        print(f"错误：发现残留的数据备份 {backup}")
        print("  可能是上次构建中断。请先确认其内容后手动处理：")
        print(f"  - 若 {data_dir} 不存在：把备份目录改名/移回为 {data_dir}")
        print("  - 若两者都在且备份已无用：手动删除备份目录")
        print("处理完再重新运行本脚本。")
        sys.exit(1)
    moved = False
    if data_dir.is_dir():
        shutil.move(str(data_dir), str(backup))
        moved = True
        print(f"已备份数据目录：{backup}")

    try:
        run(cmd, env=env)
    finally:
        if moved:
            if data_dir.exists():
                print(f"警告：{data_dir} 已存在，未恢复备份；备份保留在 {backup}，请手动合并")
            else:
                data_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(data_dir))
                print("已恢复数据目录到 dist/pda/data")

    prune_dist()
    bundle_model()
    build_launcher()
    if args.installer:
        build_installer()
    print(f"\n打包完成：{ROOT / 'dist' / 'pda'}（pda.exe = 启动器，main.exe = 应用本体）")


if __name__ == "__main__":
    main()
