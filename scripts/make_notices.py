# -*- coding: utf-8 -*-
"""生成第三方许可清单 THIRD_PARTY_NOTICES.txt（发行包 / 安装包附带）。

用法：用打包时的同一个解释器运行 python scripts/make_notices.py
列出该环境里所有已安装分发包的名称、版本、许可证与主页。PySide6（LGPL-3.0）
等 LGPL 组件以动态库形式随附，用户可替换 _internal 下对应 DLL。
"""
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "THIRD_PARTY_NOTICES.txt"

HEADER = """知识库助理 —— 第三方组件许可清单
================================

本软件随附以下开源组件，各组件版权归其作者所有，按各自许可证分发。
LGPL 组件（如 PySide6 / Qt）以独立动态库形式提供，未做静态链接，
你可以用兼容版本替换 _internal 目录下的对应文件。
嵌入模型 BAAI/bge-small-zh-v1.5 按 MIT 许可分发。

"""


def _license(dist) -> str:
    md = dist.metadata
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
    seen = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name and name.lower() not in seen:
            seen[name.lower()] = dist
    lines = [HEADER]
    for key in sorted(seen):
        d = seen[key]
        lines.append(f"{d.metadata['Name']} {d.version}\n  许可证：{_license(d)}\n")
        home = _homepage(d)
        if home:
            lines.append(f"  主页：{home}\n")
    OUT.write_text("".join(lines), encoding="utf-8-sig", newline="\r\n")
    print(f"已写入 {OUT}（{len(seen)} 个组件）")


if __name__ == "__main__":
    main()
