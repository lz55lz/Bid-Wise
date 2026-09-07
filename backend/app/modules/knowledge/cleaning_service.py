"""知识源解析结果的确定性清洗。

原始 MinerU 结果仍由对象存储保存；本模块只决定哪些节点可进入知识正文和向量索引。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.integrations.mineru import ParsedNode

_CONTENTS_HEADING = re.compile(r"^目\s*录(?:\s|$)")
_CONTENTS_ENTRY = re.compile(r"^第(?P<ordinal>[一二三四五六七八九十百千\d]+)[编章节](?:\s+|$)")
_ORDINALS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True, slots=True)
class KnowledgeCleaningResult:
    nodes: tuple[ParsedNode, ...]
    filtered_contents_entries: int
    filtered_empty_nodes: int


def clean_knowledge_nodes(nodes: tuple[ParsedNode, ...]) -> KnowledgeCleaningResult:
    """移除目录标题及递增章节目录项，正文的重复“第一章”必须被保留。"""
    kept: list[ParsedNode] = []
    in_contents = False
    last_ordinal: int | None = None
    filtered_contents = 0
    filtered_empty = 0

    for node in nodes:
        if bool((node.metadata or {}).get("layout_artifact")):
            filtered_empty += 1
            continue
        content = _normalize(node.content)
        if not content:
            filtered_empty += 1
            continue
        if _CONTENTS_HEADING.fullmatch(content):
            in_contents, last_ordinal = True, None
            filtered_contents += 1
            continue
        ordinal = _contents_ordinal(content)
        if in_contents and ordinal is not None:
            if last_ordinal is None or ordinal > last_ordinal:
                last_ordinal = ordinal
                filtered_contents += 1
                continue
            # 目录项序号回到第一章，说明已进入正文；当前节点要保留。
            in_contents = False
        elif in_contents:
            in_contents = False

        kept.append(
            ParsedNode(
                node_type=node.node_type,
                content=content,
                page_number=node.page_number,
                section_path=_without_contents(node.section_path),
                bbox=node.bbox,
                metadata=node.metadata,
            )
        )
    return KnowledgeCleaningResult(tuple(kept), filtered_contents, filtered_empty)


def _contents_ordinal(content: str) -> int | None:
    match = _CONTENTS_ENTRY.match(content)
    if match is None:
        return None
    value = match.group("ordinal")
    return int(value) if value.isdecimal() else _ORDINALS.get(value)


def _without_contents(section_path: str | None) -> str | None:
    if not section_path:
        return None
    parts = [part for part in section_path.split(" / ") if not _CONTENTS_HEADING.fullmatch(part)]
    return " / ".join(parts) or None


def _normalize(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line).strip()
