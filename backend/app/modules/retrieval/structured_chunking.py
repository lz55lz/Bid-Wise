"""法律/招标文档通用的结构优先切块。

原则：章节/条款边界优先，小节点在同章节内合并；只有超长内容才使用递归字符兜底。
滑动 overlap 只作用于超长兜底块，避免向量库充满重复片段。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html import unescape

from app.modules.documents.semantic_boundaries import is_explicit_clause_start

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；;])\s*|\n{2,}|\n")
_ATOMIC_TYPES = frozenset({"TABLE", "IMAGE"})
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMERIC_CLAUSE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){1,4})(?=\s|[、.．:：])")
_CHINESE_CLAUSE = re.compile(r"^\s*(第[一二三四五六七八九十百千\d]+[编章节条])")
_LIST_ITEM = re.compile(r"^\s*[（(]([一二三四五六七八九十\d]{1,2})[）)]")
_SHORT_LIST_ITEM_CHARS = 360
_SHORT_SIBLING_CLAUSE_CHARS = 360
_SHORT_SIBLING_GROUP_MAX_CHARS = 1_000
_HTML_TABLE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_TABLE_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class ChunkAtom:
    source_id: object
    order_no: int
    node_type: str
    content: str
    section_path: str | None = None
    page_number: int | None = None
    clause_key: str | None = None
    parent_clause_key: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredChunk:
    atoms: tuple[ChunkAtom, ...]
    text: str
    section_path: str
    order_start: int
    order_end: int
    page_start: int | None
    page_end: int | None
    node_types: tuple[str, ...]
    clause_keys: tuple[str, ...] = ()
    parent_clause_keys: tuple[str, ...] = ()


def build_structured_chunks(
    atoms: list[ChunkAtom],
    *,
    target_chars: int = 1_800,
    max_chars: int = 2_400,
    overlap_chars: int = 120,
) -> list[StructuredChunk]:
    """把已清洗原子节点组合为结构化检索块。"""
    atoms = _normalize_clause_atoms(atoms)
    chunks: list[StructuredChunk] = []
    current: list[ChunkAtom] = []

    def flush() -> None:
        if not current:
            return
        chunks.extend(
            _materialize_atoms(
                tuple(current), max_chars=max_chars, overlap_chars=overlap_chars
            )
        )
        current.clear()

    for atom in atoms:
        text = atom.content.strip()
        if not text or atom.node_type.upper() == "SECTION":
            # 标题语义由 section_path 进入 embedding/BM25，不单独制造只有标题的向量块。
            continue
        node_type = atom.node_type.upper()
        if node_type in _ATOMIC_TYPES:
            flush()
            chunks.extend(
                _materialize_atoms((atom,), max_chars=max_chars, overlap_chars=overlap_chars)
            )
            continue
        if not current:
            current.append(atom)
            continue

        current_section = current[0].section_path or ""
        atom_section = atom.section_path or ""
        current_chars = sum(len(item.content.strip()) + 1 for item in current)
        is_short_child_item = (
            atom.parent_clause_key is not None
            and _is_short_list_item(text)
            and _is_same_clause_family(current, atom.parent_clause_key)
        )
        is_short_sibling_clause = _is_short_sibling_clause(current, atom, text)
        must_break = (
            current_section != atom_section
            or (
                is_explicit_clause_start(text)
                and not is_short_child_item
                and not is_short_sibling_clause
            )
            or current_chars >= target_chars
            or current_chars + len(text) + 1 > max_chars
            or (
                is_short_sibling_clause
                and current_chars + len(text) + 1 > _SHORT_SIBLING_GROUP_MAX_CHARS
            )
        )
        if must_break:
            flush()
        current.append(atom)
    flush()
    return chunks


def _normalize_clause_atoms(atoms: list[ChunkAtom]) -> list[ChunkAtom]:
    """为常见条款编号补充稳定的父子关系，不修改原文文本。"""
    output: list[ChunkAtom] = []
    active_clause_key: str | None = None
    active_section: str | None = None
    for atom in atoms:
        section = atom.section_path or ""
        if section != active_section:
            active_section = section
            active_clause_key = None
        text = atom.content.strip()
        numeric = _NUMERIC_CLAUSE.match(text)
        chinese = _CHINESE_CLAUSE.match(text)
        list_item = _LIST_ITEM.match(text)
        if numeric:
            clause_key = numeric.group(1)
            parent = clause_key.rpartition(".")[0] or None
            active_clause_key = clause_key
        elif chinese:
            # 中文编/章/节/条不强行转阿拉伯数字，只提供稳定的层级锚点。
            clause_key = chinese.group(1)
            parent = None
            active_clause_key = clause_key
        elif list_item and active_clause_key:
            clause_key = f"{active_clause_key}.{_list_item_value(list_item.group(1))}"
            parent = active_clause_key
        elif active_clause_key:
            clause_key = active_clause_key
            parent = active_clause_key.rpartition(".")[0] or None
        else:
            clause_key = None
            parent = None
        output.append(replace(atom, clause_key=clause_key, parent_clause_key=parent))
    return output


def _list_item_value(value: str) -> str:
    chinese = {
        "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
        "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    }
    return chinese.get(value, value)


def _is_short_list_item(text: str) -> bool:
    return bool(_LIST_ITEM.match(text)) and len(text) <= _SHORT_LIST_ITEM_CHARS


def _is_short_sibling_clause(
    current: list[ChunkAtom], atom: ChunkAtom, text: str
) -> bool:
    """判断连续短的同级编号条款能否作为一个检索单元。

    仅看紧邻的上一原子，避免跨过正文、表格或另一条款后仍把同编号层级硬拼。
    """
    if not current or atom.parent_clause_key is None:
        return False
    previous = current[-1]
    return (
        bool(_NUMERIC_CLAUSE.match(text))
        and len(text) <= _SHORT_SIBLING_CLAUSE_CHARS
        and previous.parent_clause_key == atom.parent_clause_key
        and _NUMERIC_CLAUSE.match(previous.content.strip()) is not None
        and len(previous.content.strip()) <= _SHORT_SIBLING_CLAUSE_CHARS
    )


def _is_same_clause_family(current: list[ChunkAtom], parent_clause_key: str) -> bool:
    return any(
        atom.clause_key == parent_clause_key or atom.parent_clause_key == parent_clause_key
        for atom in current
    )



def markdown_atoms(text: str) -> list[ChunkAtom]:
    """把手工 Markdown 正文转成与 MinerU 一致的结构化原子节点。

    手工知识没有页码/bbox，但仍保留 Markdown 标题层级，避免重新索引时退化成固定字符硬切。
    """
    heading_by_level: dict[int, str] = {}
    atoms: list[ChunkAtom] = []
    buffer: list[str] = []
    order_no = 0

    def section_path() -> str | None:
        values = [heading_by_level[level] for level in sorted(heading_by_level)]
        return " / ".join(values) or None

    def flush() -> None:
        nonlocal order_no
        content = "\n".join(buffer).strip()
        buffer.clear()
        if not content:
            return
        order_no += 1
        atoms.append(
            ChunkAtom(
                source_id=order_no,
                order_no=order_no,
                node_type="PARAGRAPH",
                content=content,
                section_path=section_path(),
            )
        )

    for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        match = _MARKDOWN_HEADING.match(line.strip())
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_by_level = {
                key: value for key, value in heading_by_level.items() if key < level
            }
            heading_by_level[level] = title
            continue
        if not line.strip():
            flush()
            continue
        normalized = line.strip()
        if buffer and is_explicit_clause_start(normalized):
            flush()
        buffer.append(normalized)
    flush()
    return atoms

def contextualized_text(
    chunk: StructuredChunk,
    *,
    document_name: str | None = None,
    entry_title: str | None = None,
) -> str:
    """构造只用于 embedding/BM25/rerank 的上下文化文本，不改变引用原文。"""
    prefixes = [item.strip() for item in (document_name, entry_title, chunk.section_path) if item]
    prefix = " / ".join(dict.fromkeys(prefixes))
    return f"{prefix}\n{chunk.text}" if prefix else chunk.text


def retrieval_text(text: str, node_types: tuple[str, ...] | list[str]) -> str:
    """生成仅供检索/问答使用的可读文本，引用原文始终保持不变。"""
    if "TABLE" not in {item.upper() for item in node_types}:
        return text
    rows = [
        [_clean_table_cell(cell) for cell in _HTML_TABLE_CELL.findall(row)]
        for row in _HTML_TABLE_ROW.findall(text)
    ]
    rows = [row for row in rows if row]
    if len(rows) < 2:
        return text
    headers = rows[0]
    rendered_rows = []
    for index, row in enumerate(rows[1:], start=1):
        pairs = [
            f"{headers[column]}：{value}"
            for column, value in enumerate(row)
            if value and column < len(headers) and headers[column]
        ]
        if pairs:
            rendered_rows.append(f"第{index}行：" + "；".join(pairs))
    if not rendered_rows:
        return text
    return "表格字段：" + "；".join(headers) + "\n" + "\n".join(rendered_rows)


def table_markdown(text: str) -> str | None:
    """将解析器输出的 HTML 表格转为 Markdown；失败时交由原文展示。"""
    rows = [
        [_clean_table_cell(cell) for cell in _HTML_TABLE_CELL.findall(row)]
        for row in _HTML_TABLE_ROW.findall(text)
    ]
    rows = [row for row in rows if row]
    if len(rows) < 2 or not rows[0]:
        return None
    headers = rows[0]
    width = len(headers)

    def normalize(row: list[str]) -> list[str]:
        return (row + [""] * width)[:width]

    lines = [
        "| " + " | ".join(_escape_markdown_cell(cell) for cell in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_escape_markdown_cell(cell) for cell in normalize(row)) + " |"
        for row in rows[1:]
    )
    return "\n".join(lines)


def _clean_table_cell(value: str) -> str:
    return " ".join(unescape(_HTML_TAG.sub(" ", value)).split())


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _materialize_atoms(
    atoms: tuple[ChunkAtom, ...], *, max_chars: int, overlap_chars: int
) -> list[StructuredChunk]:
    text = "\n".join(item.content.strip() for item in atoms if item.content.strip()).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [_chunk(atoms, text)]

    # 超长表格优先按行切，其他正文按段落/句子切；每块保留原始 atom 血缘。
    parts = _split_oversized(text, max_chars=max_chars, overlap_chars=overlap_chars)
    return [_chunk(atoms, part) for part in parts if part.strip()]


def _chunk(atoms: tuple[ChunkAtom, ...], text: str) -> StructuredChunk:
    pages = [item.page_number for item in atoms if item.page_number is not None]
    return StructuredChunk(
        atoms=atoms,
        text=text.strip(),
        section_path=atoms[0].section_path or "",
        order_start=atoms[0].order_no,
        order_end=atoms[-1].order_no,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        node_types=tuple(dict.fromkeys(item.node_type.upper() for item in atoms)),
        clause_keys=tuple(
            dict.fromkeys(item.clause_key for item in atoms if item.clause_key is not None)
        ),
        parent_clause_keys=tuple(
            dict.fromkeys(
                item.parent_clause_key for item in atoms if item.parent_clause_key is not None
            )
        ),
    )


def _split_oversized(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    units = [item.strip() for item in _SENTENCE_SPLIT.split(text) if item and item.strip()]
    if not units:
        units = [text]
    pieces: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(_hard_window(unit, max_chars=max_chars, overlap_chars=overlap_chars))
            continue
        candidate = f"{current}\n{unit}".strip() if current else unit
        if current and len(candidate) > max_chars:
            pieces.append(current)
            tail = current[-overlap_chars:].strip() if overlap_chars else ""
            current = f"{tail}\n{unit}".strip() if tail else unit
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _hard_window(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if overlap_chars >= max_chars:
        overlap_chars = max_chars // 8
    step = max(1, max_chars - overlap_chars)
    return [text[start : start + max_chars].strip() for start in range(0, len(text), step)]
