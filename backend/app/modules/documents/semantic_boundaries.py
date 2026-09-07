"""只依据源文本显式边界拆分招标条款，绝不按字符长度硬切。"""

from __future__ import annotations

import re

_NUMBERED_CLAUSE_BOUNDARY = re.compile(
    r"(?:^|(?<=[。；：\n]))\s*(?=(?:第[一二三四五六七八九十百]+条|\d{1,2}(?:\.\d{1,2}){1,3})\s*)"
)
_STAGED_ITEM_START = re.compile(
    r"(?=(?:第[一二三四五六七八九十]+次)\s*(?:支付|交付|验收|结算)[：:])"
)
_LEADING_PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s+(?=(?:第[一二三四五六七八九十百]+条|\d{1,2}\.))")
_LIST_ITEM_BOUNDARY = re.compile(
    r"(?:^|(?<=[。；：\n]))\s*(?=[（(](?:[一二三四五六七八九十]|\d{1,2})[）)])"
)
_CLAUSE_START = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千\d]+[编章节条]|\d{1,3}(?:\.\d{1,3}){1,4}(?!\d)|[（(](?:[一二三四五六七八九十]|\d{1,2})[）)]|[①②③④⑤⑥⑦⑧⑨⑩])"
)
_TERMINAL = re.compile(r"[。！？；：;:]\s*$")
_CONTINUATION_PREFIX = re.compile(
    r"^(?:[，、；;。！？）)】]|(?:以内|以上|以下|以及|或者|并且|且|并|但|而|的|了|内|外))"
)
_SHORT_RESIDUE_CHARS = 28


def is_explicit_clause_start(content: str) -> bool:
    """源文本以编号/列表符开头时，不得把它并入前一条款。"""
    return bool(_CLAUSE_START.match(content or ""))


def can_absorb_short_residue(
    previous_content: str,
    previous_type: str,
    previous_page: int | None,
    content: str,
    node_type: str,
    page: int | None,
) -> bool:
    """判断短残句是否可安全并入前一个版面块。

    只允许同页、非表格/图片、且没有显式条款开头的残句合并；这避免跨页、跨表
    或把新的编号条款错误拼接到上一个段落中。
    """
    if (
        previous_page != page
        or previous_type.upper() in {"TABLE", "IMAGE"}
        or node_type.upper() in {"TABLE", "IMAGE"}
    ):
        return False
    if is_explicit_clause_start(content):
        return False
    compact_length = len(re.sub(r"\s+", "", content))
    return compact_length <= _SHORT_RESIDUE_CHARS and (
        not _TERMINAL.search(previous_content) or bool(_CONTINUATION_PREFIX.match(content))
    )


def split_explicit_clause_boundaries(content: str) -> list[str]:
    """仅在原文中的编号、分期付款或列表项边界切分。

    一段没有显式边界的正文保持完整，从而使 Evidence 仍能对应 MinerU 的原始
    版面块，不引入不可审计的“模型式分块”。
    """
    text = _LEADING_PAGE_NUMBER.sub("", content.strip())
    if not text:
        return []
    boundaries = {0}
    boundaries.update(match.end() for match in _NUMBERED_CLAUSE_BOUNDARY.finditer(text))
    for match in _STAGED_ITEM_START.finditer(text):
        preceding = text[max(0, match.start() - 20) : match.start()]
        if not re.search(
            r"(?:第[一二三四五六七八九十百]+条|\d{1,2}(?:\.\d{1,2}){1,3})\s*$", preceding
        ):
            boundaries.add(match.start())
    boundaries.update(match.end() for match in _LIST_ITEM_BOUNDARY.finditer(text))
    starts = sorted(boundaries)
    return [
        text[start : starts[index + 1] if index + 1 < len(starts) else None].strip()
        for index, start in enumerate(starts)
        if text[start : starts[index + 1] if index + 1 < len(starts) else None].strip()
    ]
