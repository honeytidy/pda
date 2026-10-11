# -*- coding: utf-8 -*-
"""生成第三方许可清单 THIRD_PARTY_NOTICES.txt（发行包 / 安装包附带）。

用法：先 python scripts/build_exe.py 打包，再用打包时的同一个解释器运行
    python scripts/make_notices.py
只列 dist/pda 里实际打进去的分发包：main.exe 内嵌 PYZ 里的模块 + _internal 下的
包目录 / 扩展模块，按顶层模块名反查所属分发包（build_deps 优先于构建解释器环境）。
PySide6（LGPL-3.0）等 LGPL 组件以动态库形式随附，用户可替换 _internal 下对应 DLL。
"""
import sys
from importlib import metadata
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD_PARTY_NOTICES.txt"
DIST = ROOT / "dist" / "pda"
INTERNAL = DIST / "_internal"
APP_EXE = DIST / "main.exe"
BUILD_DEPS = ROOT / "build_deps"

# 项目自身代码与 PyInstaller 运行时钩子，不是第三方组件
SKIP = {"pda", "_pyi_rth_utils", "ossl-modules"}
# 分发包元数据里没有文件清单（conda 安装）或模块不在 RECORD 顶层的，手工映射
ALIASES = {
    "PySide6": "PySide6", "shiboken6": "shiboken6",
    "pywin": "pywin32", "win32con": "pywin32", "winerror": "pywin32",
    "commctrl": "pywin32", "ntsecuritycon": "pywin32", "win32traceutil": "pywin32",
}
# 元数据缺许可证字段的，手工补
LICENSE_OVERRIDES = {
    "pyside6": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only（随附 Qt 6 动态库，同许可）",
    "shiboken6": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
}

HEADER = """知识库助理 —— 第三方组件许可清单
================================

本软件随附以下开源组件，各组件版权归其作者所有，按各自许可证分发。
LGPL 组件（如 PySide6 / Qt）以独立动态库形式提供，未做静态链接，
你可以用兼容版本替换 _internal 目录下的对应文件。
嵌入模型 BAAI/bge-small-zh-v1.5 按 MIT 许可分发。

"""


def bundled_modules() -> set[str]:
    """dist/pda 里实际存在的第三方顶层模块名。"""
    if not APP_EXE.is_file() or not INTERNAL.is_dir():
        sys.exit(f"错误：未找到 {APP_EXE} 或 {INTERNAL}，先运行 scripts/build_exe.py")
    pyz = CArchiveReader(str(APP_EXE)).open_embedded_archive("PYZ.pyz")
    mods = {name.split(".")[0] for name in pyz.toc}
    for p in INTERNAL.iterdir():
        if p.is_dir() and not p.name.endswith((".dist-info", ".egg-info", ".libs")):
            mods.add(p.name)
        elif p.suffix == ".pyd":
            mods.add(p.name.split(".")[0])
    return mods - set(sys.stdlib_module_names) - SKIP


def module_owners() -> dict[str, metadata.Distribution]:
    """顶层模块名 -> 分发包；_internal 自带的 dist-info、build_deps 优先于构建环境。"""
    owners: dict[str, metadata.Distribution] = {}
    search = [str(INTERNAL), str(BUILD_DEPS)] + sys.path
    for dist in metadata.distributions(path=search):
        if not dist.metadata.get("Name"):
            continue
        tops = set((dist.read_text("top_level.txt") or "").split())
        for f in dist.files or []:
            head = f.parts[0]
            if head != ".." and not head.endswith((".dist-info", ".egg-info")):
                tops.add(head.split(".")[0])
        for t in tops:
            owners.setdefault(t, dist)  # 先到先得：search 顺序即优先级
    return owners


def _license(dist) -> str:
    md = dist.metadata
    override = LICENSE_OVERRIDES.get(md["Name"].lower())
    if override:
        return override
    lic = (md.get("License-Expression") or "").strip()
    if not lic:
        raw = (md.get("License") or "").strip()
        # 有的包把整篇许可证正文塞进 License 字段：只取首行
        lic = raw.splitlines()[0][:80] if raw else ""
    if not lic or lic.upper() == "UNKNOWN":
        classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or []
                       if c.startswith("License ::")]
        lic = ", ".join(classifiers)
    return lic or "见项目主页"


def _homepage(dist) -> str:
    md = dist.metadata
    if md.get("Home-page"):
        return md["Home-page"]
    for url in md.get_all("Project-URL") or []:
        label, _, link = url.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository"):
            return link.strip()
    return ""


def main():
    owners = module_owners()
    seen: dict[str, metadata.Distribution] = {}
    unmapped = []
    for mod in sorted(bundled_modules()):
        dist = owners.get(mod)
        if dist is None and mod in ALIASES:
            try:
                dist = metadata.distribution(ALIASES[mod])
            except metadata.PackageNotFoundError:
                pass
        if dist is None:
            unmapped.append(mod)
            continue
        seen.setdefault(dist.metadata["Name"].lower(), dist)
    lines = [HEADER]
    for key in sorted(seen):
        d = seen[key]
        lines.append(f"{d.metadata['Name']} {d.version.strip()}\n  许可证：{_license(d)}\n")
        home = _homepage(d)
        if home:
            lines.append(f"  主页：{home}\n")
    OUT.write_text("".join(lines), encoding="utf-8-sig", newline="\r\n")
    print(f"已写入 {OUT}（{len(seen)} 个组件）")
    if unmapped:
        # 不静默漏项：找不到归属的模块要人工确认许可证后补进 ALIASES
        print(f"警告：以下模块未找到所属分发包，未列入清单：{', '.join(unmapped)}")


if __name__ == "__main__":
    main()
