# -*- coding: utf-8 -*-
"""生成 src/pda.ico：QPainter 渲染眼睛 logo 多尺寸 PNG，纯 Python 打包成 ICO 容器。

ICO（Vista+）内嵌 PNG：ICONDIR 头 + 每档 16 字节目录项 + PNG 数据。
256px 的宽高字段记 0。
用法：python scripts/make_icon.py
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

from pda.ui.icon import make_pixmap

SIZES = (16, 32, 48, 64, 128, 256)
OUT = Path(__file__).resolve().parent.parent / "src" / "pda.ico"


def png_bytes(size: int) -> bytes:
    pm = make_pixmap(size)
    buf = QByteArray()
    qbuf = QBuffer(buf)
    qbuf.open(QIODevice.WriteOnly)
    pm.save(qbuf, "PNG")
    return bytes(buf)


def main():
    app = QGuiApplication([])  # noqa: F841（offscreen 下中文字体退化不影响纯图形 logo）
    images = [png_bytes(s) for s in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in zip(SIZES, images):
        dim = 0 if size == 256 else size  # 256 记 0
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(data), offset))
        offset += len(data)

    OUT.write_bytes(header + b"".join(entries) + b"".join(images))
    print(f"已生成 {OUT}（{OUT.stat().st_size} 字节，{len(images)} 档尺寸）")


if __name__ == "__main__":
    main()
