"""Document-native semantic boundaries shared by parsing and clause derivation.

The rules in this module only recognise boundaries explicitly present in the
source text.  They never use a character/token budget to alter content.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.services.document_text_quality import assess_text_quality, indexability_gate

_NUMBERED_CLAUSE_BOUNDARY = re.compile(
    r"(?:^|(?<=[。；：\n]))\s*"
    r"(?=(?:第[一二三四五六七八九十百]+条|\d{1,2}(?:\.\d{1,2}){1,3})\s*)"
)
_STAGED_ITEM_START = re.compile(
    r"(?=(?:第[一二三四五六七八九十]+次)\s*(?:支付|交付|验收|结算)[：:])"
)
_LEADING_PAGE_NUMBER = re.compile(
    r"^\s*\d{1,3}\s+(?=(?:第[一二三四五六七八九十百]+条|\d{1,2}\.))"
)
_LIST_ITEM_BOUNDARY = re.compile(
    r"(?:^|(?<=[。；：\n]))\s*(?=[（(](?:[一二三四五六七八九十]|\d{1,2})[）)])"
)
_CLAUSE_START = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千\d]+[编章节条]|"
    r"\d{1,3}(?:\.\d{1,3}){1,4}(?!\d)|"
    r"[（(](?:[一二三四五六七八九十]|\d{1,2})[）)]|[①②③④⑤⑥⑦⑧⑨⑩])"
)
_PAGE_ARTIFACT = re.compile(
    r"^\s*(?:第\s*\d{1,4}\s*页(?:\s*/\s*共?\s*\d{1,4}\s*页)?|"
    r"page\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4})\s*$",
    re.IGNORECASE,
)
_TERMINAL = re.compile(r"[。！？；：;:]\s*$")
_CONTINUATION_PREFIX = re.compile(
    r"^(?:[，、；;。！？）)】]|(?:以内|以上|以下|以及|或者|并且|且|并|但|而|的|了|内|外))"
)
_CONTENTS_HEADING = re.compile(r"^目\s*录$")
_CONTENTS_ENTRY = re.compile(
    r"^第(?P<ordinal>[一二三四五六七八九十百千\d]+)[编章节](?:\s+|$)"
)
_CHINESE_ORDINALS = {
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

# This is deliberately not a target chunk size.  It only distinguishes a
# parser residue from an independently readable clause/list row.
_SHORT_RESIDUE_CHARS = 28


def is_explicit_clause_start(content: str) -> bool:
    """Whether source text explicitly starts a new legal/tender clause."""
    return bool(_CLAUSE_START.match(content or ""))


def _compact_len(content: str) -> int:
    return len(re.sub(r"\s+", "", content or ""))


def _normalise_text(content: object) -> str:
    return re.sub(r"\s+", " ", str(content or "")).strip()


def _contents_ordinal(content: str) -> int | None:
    """Return an ordinal for a one-line contents entry when it is unambiguous."""
    match = _CONTENTS_ENTRY.match(content)
    if match is None:
        return None
    value = match.group("ordinal")
    if value.isdecimal():
        return int(value)
    return _CHINESE_ORDINALS.get(value)


def _is_noise(content: str) -> bool:
    if not content or _PAGE_ARTIFACT.fullmatch(content):
        return True
    return indexability_gate(content, assess_text_quality(content)) is not None


def _can_absorb(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Keep a MinerU line residue with the clause it completes.

    The rule is intentionally conservative: never cross pages, tables or an
    explicit numbering boundary.  We only glue a short fragment when the
    preceding block has not ended a sentence, or the fragment itself plainly
    looks like a grammatical continuation (``内；`` is the common case).
    """
    if previous.get("page_no") != current.get("page_no"):
        return False
    if str(previous.get("chunk_type", "")).lower() in {"table", "cell", "image"}:
        return False
    if str(current.get("chunk_type", "")).lower() in {"table", "cell", "image"}:
        return False
    text = str(current.get("chunk_text") or "")
    if is_explicit_clause_start(text):
        return False
    previous_text = str(previous.get("chunk_text") or "")
    return (
        _compact_len(text) <= _SHORT_RESIDUE_CHARS
        and (not _TERMINAL.search(previous_text) or bool(_CONTINUATION_PREFIX.match(text)))
    )


def semantic_chunk_layout_nodes(chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert parser layout blocks into auditable clause-aware chunks.

    MinerU remains the source of page, section and bbox facts.  This layer
    only joins *same-page* short residues and splits at explicit numbered
    legal/tender boundaries.  It never uses a character window, never joins
    tables with prose, and records the source block indexes for audit.
    """
    prepared: list[dict[str, Any]] = []
    in_contents = False
    last_contents_ordinal: int | None = None
    for source in chunks:
        text = _normalise_text(source.get("chunk_text"))
        # MinerU commonly emits the contents title and each entry as separate
        # layout nodes.  A title-only quality gate therefore removes only
        # "目录", leaving every chapter heading in the knowledge body.  While
        # inside a contents block, drop its monotonic heading sequence.  The
        # sequence resets when the real body starts from chapter/section one.
        if _CONTENTS_HEADING.fullmatch(text):
            in_contents = True
            last_contents_ordinal = None
            continue
        contents_ordinal = _contents_ordinal(text)
        if in_contents and contents_ordinal is not None:
            if last_contents_ordinal is None or contents_ordinal > last_contents_ordinal:
                last_contents_ordinal = contents_ordinal
                continue
            in_contents = False
        elif in_contents:
            in_contents = False
        if _is_noise(text):
            continue
        item = {**source, "chunk_text": text}
        metadata = dict(item.get("parser_metadata") or {})
        metadata.setdefault("source_order_nos", [source.get("chunk_index")])
        item["parser_metadata"] = metadata
        if prepared and _can_absorb(prepared[-1], item):
            previous = prepared[-1]
            previous["chunk_text"] = f"{previous['chunk_text']} {item['chunk_text']}".strip()
            previous_metadata = dict(previous.get("parser_metadata") or {})
            previous_metadata["source_order_nos"] = [
                *previous_metadata.get("source_order_nos", []),
                *metadata.get("source_order_nos", []),
            ]
            previous_metadata["semantic_merge"] = "SAME_PAGE_SHORT_RESIDUE"
            previous["parser_metadata"] = previous_metadata
            continue
        # A one-to-six character item which cannot complete a preceding clause
        # is parser noise, except for an explicit chapter/section/article
        # heading.  The contents-state handling above has already discarded
        # headings inside a real contents block.
        if _compact_len(text) < 7 and not is_explicit_clause_start(text):
            continue
        prepared.append(item)

    atomic: list[dict[str, Any]] = []
    for source in prepared:
        text = str(source["chunk_text"])
        # Tables/lists are already semantically atomic layout objects.  For
        # prose, only explicit source numbering is a legal split boundary.
        parts = (
            [text]
            if str(source.get("chunk_type", "")).lower() in {"table", "cell", "image"}
            else split_explicit_clause_boundaries(text)
        )
        for part_index, part in enumerate(parts):
            # A split may expose a source residue such as "注：" that was
            # hidden inside a longer layout block.  It is not a clause, list
            # item or table row and must not survive as standalone evidence.
            if _compact_len(part) < 7 and not is_explicit_clause_start(part):
                continue
            item = {**source, "chunk_text": part}
            metadata = dict(item.get("parser_metadata") or {})
            if len(parts) > 1:
                metadata["semantic_split"] = "EXPLICIT_CLAUSE_BOUNDARY"
                metadata["semantic_part"] = part_index + 1
            item["parser_metadata"] = metadata
            atomic.append(item)

    for index, chunk in enumerate(atomic):
        chunk["chunk_index"] = index
        chunk["chunk_id"] = f"{chunk.get('doc_id', 'document')}_{index}"
    return atomic


def split_explicit_clause_boundaries(content: str) -> list[str]:
    """Split only at explicit numbered or staged tender-clause boundaries.

    A source paragraph with no such boundary remains wholly intact.  The
    caller may attach every returned part to the same source Evidence.
    """
    text = _LEADING_PAGE_NUMBER.sub("", content.strip())
    if not text:
        return []

    boundaries = {0}
    boundaries.update(match.end() for match in _NUMBERED_CLAUSE_BOUNDARY.finditer(text))
    for match in _STAGED_ITEM_START.finditer(text):
        # `4.1.1 第一次支付` is a single clause heading.  A later staged item
        # without the numeric prefix is a separate explicit business item.
        preceding = text[max(0, match.start() - 20):match.start()]
        if re.search(
            r"(?:第[一二三四五六七八九十百]+条|\d{1,2}(?:\.\d{1,2}){1,3})\s*$",
            preceding,
        ):
            continue
        boundaries.add(match.start())
    for match in _LIST_ITEM_BOUNDARY.finditer(text):
        boundaries.add(match.end())

    starts = sorted(boundaries)
    return [
        text[start: starts[index + 1] if index + 1 < len(starts) else None].strip()
        for index, start in enumerate(starts)
        if text[start: starts[index + 1] if index + 1 < len(starts) else None].strip()
    ]
