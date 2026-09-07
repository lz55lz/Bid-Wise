"""检索查询的确定性辅助规则。

Dense/Reranker 始终使用用户原问题；本模块只负责：
1. 粗粒度意图分类，供 Agent 决定是否需要补充法规/报告工具；
2. 生成少量 BM25 词法变体，增强编号、金额和原文关键词命中。

不再维护同义词扩展字典。语义泛化交给向量模型，避免词法查询被扩展词污染。
"""

import re
from enum import StrEnum

_QUESTION_WORDS = (
    "请问",
    "问一下",
    "我想知道",
    "想知道",
    "什么时候",
    "什么时间",
    "何时",
    "多少",
    "有哪些",
    "包括哪些",
    "有没有",
    "怎么",
)


class QueryIntent(StrEnum):
    """无需模型调用的粗粒度问题意图。"""

    DEFINITION = "DEFINITION"
    LIST = "LIST"
    FACTUAL = "FACTUAL"
    PROCEDURAL = "PROCEDURAL"
    COMPARISON = "COMPARISON"
    DEFAULT = "DEFAULT"


def classify_query(query: str) -> QueryIntent:
    normalized = query.strip().lower()
    if any(word in normalized for word in ("什么是", "定义", "概念", "含义", "什么叫")):
        return QueryIntent.DEFINITION
    if any(word in normalized for word in ("哪些", "包括", "流程", "步骤", "列举", "清单", "列表")):
        return QueryIntent.LIST
    if any(
        word in normalized
        for word in ("怎么办", "如何处理", "怎么解决", "怎么处理", "怎么操作", "如何进行")
    ):
        return QueryIntent.PROCEDURAL
    if any(word in normalized for word in ("区别", "差异", "不同", "比较", "对比")):
        return QueryIntent.COMPARISON
    if any(
        word in normalized
        for word in ("什么时候", "公布时间", "施行时间", "是多少", "金额", "数量", "天数")
    ):
        return QueryIntent.FACTUAL
    return QueryIntent.DEFAULT


def build_bm25_queries(query: str, *, max_variants: int = 5) -> tuple[str, ...]:
    """生成保守的词法查询变体，不改变用户语义。

    PostgreSQL ``plainto_tsquery`` 会把词项按 AND 组合，因此这里只保留用户明确写出的
    短语、分句以及去掉问句壳后的原问题；不拼接同义词或模型改写。
    """
    original = query.strip()
    if not original:
        return ()

    variants: list[str] = [original]
    variants.extend(item.strip() for item in re.findall(r'"([^"]+)"', original))
    variants.extend(
        item.strip()
        for item in re.split(r"[、，,；;。.！!？?]+", original)
        if len(item.strip()) >= 2
    )

    concise = original
    for word in _QUESTION_WORDS:
        if concise.startswith(word):
            concise = concise[len(word) :].strip()
        concise = concise.replace(word, "").strip()
    if len(concise) >= 2:
        variants.append(concise)

    return tuple(dict.fromkeys(item for item in variants if len(item) >= 2))[:max_variants]
