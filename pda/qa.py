# -*- coding: utf-8 -*-
"""问答：向量检索 + FTS5 关键词检索混合，拼 prompt 调 LLM 生成带引用的中文答案。

V1.2：限定范围提问——"在XX里/只看XX"之类限定词先按文档标题/标签模糊匹配
过滤候选文档再检索；匹配不到则全库检索并在答案中提示。
"""
import logging
import re

from . import db, embeddings, llm, vectorstore

_log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是用户的个人知识库助理。根据下面给出的资料片段回答用户的问题。\n"
    "要求：\n"
    "1. 用中文简洁回答；\n"
    "2. 回答中引用资料时用 [1]、[2] 这样的编号标注出处；\n"
    "3. 资料中没有的信息不要编造，直接说明资料中未提及。"
)

# 限定词模式："在XX里/中/内"、"只看XX"、"《XX》"
# 普通英文/中文引号不再当限定词（问题里引用一个词很常见，会误把检索范围缩小）
# "在" 前面不能紧挨着能与它组词的字（现在/存在/正在/实在/所在…），否则
# "现在公司里有多少人"会被误判为限定在"公司"
_ZAI = r"(?<![现存正实所自好健潜内外处旨志意还])在"
_SCOPE_PATTERNS = [
    re.compile(_ZAI + r"[《「『]?([^，。,.？?！!《》「」『』\s]{2,20}?)[》」』]?(?:里|中|内)"),
    re.compile(r"只看[《「『]?([^，。,.？?！!《》「」『』\s]{2,20}?)[》」』]?(?:[，。,.？?！!\s]|$)"),
    re.compile(r"[《「『]([^《》「」『』]{1,20}?)[》」』]"),
]

# 泛指词不是限定范围（"在这个过程中"、"在其中"、"在资料里"）
_SCOPE_STOPWORDS = {
    "过程", "这个过程", "此过程", "其中", "这里", "那里", "哪里", "这个", "那个",
    "这些", "那些", "资料", "文档", "文件", "知识库", "全部", "所有", "我的资料",
    "我的文档", "工作", "生活", "实际", "现实", "心里", "脑子", "家里", "实践",
    "工作中", "日常", "平时", "网上", "路上",
}
# "在…中/里"里以这些词开头的多为泛指（"在这次会议中"除外的指代性短语）
_SCOPE_GENERIC_PREFIX = ("这", "那", "其", "此", "该", "哪")

# 限定范围内命中少于该数时，补充全库结果（限定词可能误判，或范围内资料不全）
_SCOPE_MIN_HITS = 3


def parse_scope(query: str) -> str | None:
    """提取限定范围词（如"会议纪要"），提取不到返回 None。"""
    for i, pattern in enumerate(_SCOPE_PATTERNS):
        for m in pattern.finditer(query):
            term = m.group(1).strip()
            if not term or term in _SCOPE_STOPWORDS:
                continue
            if i == 0 and term.startswith(_SCOPE_GENERIC_PREFIX):
                continue  # "在这个项目中"：指代词，不是文档名
            return term
    return None


# 向量结果的余弦距离上限：超过即视为与问题无关，不送给 LLM。
# bge-small-zh 实测：相关片段的最佳距离 0.28~0.46，无关问题（天气/菜谱/寒暄）最近也有 0.61+
_MAX_VEC_DISTANCE = 0.6
# RRF 融合常数（Cormack et al. 2009 的经验值）
_RRF_K = 60
_MAX_HITS = 8


def retrieve(query: str, vec_top: int = 8, fts_top: int = 5,
             doc_ids: list | None = None, query_vec: list | None = None) -> list:
    """混合检索：向量 top8（去掉距离过远的）+ 关键词 top5，按 RRF 融合排序，最多 8 条。

    doc_ids 不为 None 时只在指定文档范围内检索。query_vec 可传入已算好的查询向量
    （同一问题多次检索时只算一次 embedding）。
    """
    if query_vec is None:
        query_vec = embeddings.embed([query])[0]
    vec_hits = [
        h for h in vectorstore.search(query_vec, top_k=vec_top, doc_ids=doc_ids)
        if h.get("distance") is None or h["distance"] <= _MAX_VEC_DISTANCE
    ]

    fts_hits = []
    try:
        fts_hits = db.fts_search(query, top_k=fts_top, doc_ids=doc_ids)
    except Exception:
        # FTS 语法错误等失败时跳过，只靠向量结果
        _log.warning("FTS 检索失败，仅使用向量结果", exc_info=True)
        fts_hits = []

    # RRF：每路按名次给 1/(k+rank) 分，两路都命中的片段自然排前；同分保持向量优先
    scores, hits_by_id = {}, {}
    for hits in (vec_hits, fts_hits):
        for rank, hit in enumerate(hits):
            cid = hit.get("chunk_id") or hit.get("id")
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            hits_by_id.setdefault(cid, hit)
    ranked = sorted(scores, key=scores.get, reverse=True)
    return [hits_by_id[cid] for cid in ranked[:_MAX_HITS]]


def answer(query: str) -> dict:
    """返回 {answer, sources, markdown}；sources 为 [{index, title, file_path, snippet}]。

    markdown=True 仅当答案来自 LLM：检索兜底展示的是文档原文片段，里面的 * # 等
    不是 Markdown，按纯文本显示。
    """
    # 限定范围提问：标题/标签模糊匹配候选文档
    scope_note = ""
    doc_ids = None
    term = parse_scope(query)
    if term:
        docs = db.find_documents_by_term(term)
        if docs:
            doc_ids = [d["id"] for d in docs]
            scope_note = f"（已限定在匹配「{term}」的 {len(docs)} 个文档内检索）\n\n"
        else:
            scope_note = f"（未找到匹配「{term}」的文档，已全库检索）\n\n"

    query_vec = embeddings.embed([query])[0]
    hits = retrieve(query, doc_ids=doc_ids, query_vec=query_vec)
    if doc_ids is not None and not hits:
        # 限定范围内一无所获（多半是误判了限定词）：退回全库
        hits = retrieve(query, query_vec=query_vec)
        scope_note = f"（「{term}」范围内没有相关内容，已全库检索）\n\n"
    elif doc_ids is not None and len(hits) < _SCOPE_MIN_HITS:
        # 范围内结果很少：限定结果排前面，再补充全库结果，避免误判时漏掉真正相关的文档
        seen = {h.get("chunk_id") or h.get("id") for h in hits}
        extra = [h for h in retrieve(query, query_vec=query_vec)
                 if (h.get("chunk_id") or h.get("id")) not in seen]
        if extra:
            hits = hits + extra[:max(0, 8 - len(hits))]
            scope_note = (f"（匹配「{term}」的文档内相关内容较少，"
                          "已优先列出并补充全库结果）\n\n")
    sources = [
        {
            "index": i + 1,
            "title": h.get("title", ""),
            "file_path": h.get("file_path", ""),
            "snippet": (h.get("text", "") or "").replace("\n", " ")[:100],
            "text": h.get("text", ""),
        }
        for i, h in enumerate(hits)
    ]

    if not hits:
        return {
            "answer": scope_note + "知识库中没有检索到相关内容。请先把相关文档拖进窗口收录。",
            "sources": [],
        }

    if not llm.has_llm():
        lines = [
            "未配置 API Key，无法生成智能回答。以下是检索到的相关资料片段：",
            "",
        ]
        for s in sources:
            lines.append(f"[{s['index']}] {s['title']}：{s['text'][:200]}")
        return {"answer": scope_note + "\n".join(lines), "sources": sources}

    context = "\n\n".join(
        f"[{s['index']}]（来源：{s['title']}）\n{s['text']}" for s in sources
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"资料片段：\n{context}\n\n用户问题：{query}",
        },
    ]
    try:
        text = llm.chat(messages)
    except Exception as e:
        text = f"调用 LLM 失败：{e}\n\n以下是检索到的相关资料片段：\n" + "\n".join(
            f"[{s['index']}] {s['title']}：{s['text'][:200]}" for s in sources
        )
        return {"answer": scope_note + text, "sources": sources}
    return {"answer": scope_note + text, "sources": sources, "markdown": True}
