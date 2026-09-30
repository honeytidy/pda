# -*- coding: utf-8 -*-
"""文本切块：目标 ~500 字符、重叠 ~80，按段落优先切。

重叠只加一次：块本身按 target_size - overlap 拼装，再给后续块加上前一块末尾
overlap 字的前缀，最终每块不超过 target_size（bge-small-zh 上限 512 token，
中文约 1 字 1 token，超出部分不会进入向量）。
"""

TARGET_SIZE = 500
OVERLAP = 80


def chunk_text(text: str, target_size: int = TARGET_SIZE, overlap: int = OVERLAP) -> list:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    overlap = max(0, min(overlap, target_size // 2))
    body = target_size - overlap  # 给重叠前缀留出空间
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    # 先把段落拼成不超过 body 的块；超长段落按 body 硬切（不在这里加重叠）
    blocks = []
    current = ""
    for para in paragraphs:
        while len(para) > body:
            if current:
                blocks.append(current)
                current = ""
            blocks.append(para[:body])
            para = para[body:]
        if not para:
            continue
        candidate = f"{current}\n{para}" if current else para
        if len(candidate) > body and current:
            blocks.append(current)
            current = para
        else:
            current = candidate
    if current:
        blocks.append(current)

    # 相邻块之间加重叠前缀（只加这一次）
    if len(blocks) <= 1:
        return blocks
    result = [blocks[0]]
    for i in range(1, len(blocks)):
        tail = blocks[i - 1][-overlap:] if overlap else ""
        result.append(tail + blocks[i])
    return [b.strip() for b in result if b.strip()]
