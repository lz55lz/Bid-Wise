"""解析节点清洗与招标要求预筛选。

``content`` 始终保存 MinerU 原文；这里只写派生的 ``cleaned_content`` 和审计元数据。
目录、页码、乱码被过滤后，人工仍能定位和复核原始版面。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from uuid import UUID
from html import unescape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import DocumentNode, DocumentVersion
from app.modules.documents.semantic_boundaries import can_absorb_short_residue
from app.modules.documents.text_quality import assess_text_quality, indexability_gate

_PAGE_ARTIFACT = re.compile(
    r"^(?:第\s*\d{1,4}\s*页(?:\s*/\s*共?\s*\d{1,4}\s*页)?|page\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4})$",
    re.IGNORECASE,
)
_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_TABLE_ROW_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TABLE_PLACEHOLDER_RE = re.compile(r"(?:\d+|[.。…\-—_]+)")
_CONTENTS_HEADING = re.compile(r"^目\s*录$")
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

_BASE_KEYWORDS = (
    "投标人资格",
    "投标保证金",
    "履约保证金",
    "资格审查",
    "评标办法",
    "评审办法",
    "评审标准",
    "实质性要求",
    "分包",
    "联合体",
    "投标文件",
    "投标有效期",
    "投标报价",
    "合同条款",
    "技术标准",
    "技术要求",
    "规格",
    "交货",
    "验收",
    "付款",
    "结算",
    "违约责任",
    "质量保修",
    "保修",
)
_CANDIDATE_RE = re.compile(
    "|".join(
        (*_BASE_KEYWORDS, r"^第[一二三四五六七八九十百千\d]+条", r"^\d+\.\d+[.\d]*[^.\s篇章节]")
    )
)
_OBLIGATION_RE = re.compile(r"应当|必须|不得|不准|严禁|需要|要求|须")
_NEGATIVE_RE = re.compile(
    r"^目?录$|^第[一二三四五六七八九十百千\d]+[章节条篇]$|^附[录件][一二三四五六七八九十\d]*$|^表格\d+$|^图\d+$|^注[：:]\s*\S|覆盖率\s*\d+%|成活率\s*\d+%|病虫害|园林植物|绿化施工|株行距|乔木|灌木|地被植物"
)
_SECTION_BLACKLIST_RE = re.compile(
    r"投标报价说明|工程量清单|计价规范|工程技术规范|施工技术规范|技术规范|廉政协议|乙方职责|承包人义务|承包人职责|监理规则|监理制度|园林植物|绿化施工|地被植物|乔木灌木|株行距"
)
_PROCEDURAL_RE = re.compile(
    r"开标|评标委员会|澄清|修改招标文件|踏勘|预备会|网上开标|远程解密|异议|投诉|质疑|投标文件格式"
)
_CONTRACT_SECTION_RE = re.compile(
    r"合同条款|合同条件|付款|支付|结算|履约|违约|质量保修|质保|保修|竣工|验收标准|移交"
)
_SPEC_ITEM_RE = re.compile(
    r"^\s*[\(（][\d一二三四五六七八九十]+[\)）]\s*\S|^\s*\d+\.\d+[.\d]*[^\s]"
)
_MEANINGFUL_MIN = 8
_DUPLICATE_SHORT_MAX = 120
_SECTION_CANDIDATE_LIMIT = 20


class DocumentCleaningService:
    """把每个解析节点标记为可消费/不可消费，并做确定性招标要求初筛。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def clean(self, version_id: UUID) -> dict[str, int]:
        rows = await self._session.scalars(
            select(DocumentNode)
            .where(DocumentNode.document_version_id == version_id)
            .order_by(DocumentNode.order_no)
        )
        nodes = list(rows.all())
        prepared = [(node, self._normalize(node.content)) for node in nodes]
        keys = [self._duplicate_key(text) for _, text in prepared]
        duplicate_keys = [((node.section_path or ""), key) for (node, _), key in zip(prepared, keys, strict=True)]
        counts = Counter(
            item for item in duplicate_keys if 0 < len(item[1]) <= _DUPLICATE_SHORT_MAX
        )
        seen: set[tuple[str, str]] = set()
        in_contents = False
        last_ordinal: int | None = None
        indexable: list[DocumentNode] = []
        reasons: Counter[str] = Counter()
        raw_chars = clean_chars = garbled_chars = 0

        for (node, text), key in zip(prepared, keys, strict=True):
            visible = [char for char in text if not char.isspace()]
            raw_count = sum(not char.isspace() for char in node.content)
            quality = assess_text_quality(text)
            raw_chars += raw_count
            clean_chars += len(visible)
            garbled_chars += quality.garbled_characters

            reason = self._contents_reason(text, in_contents, last_ordinal)
            if _CONTENTS_HEADING.fullmatch(text):
                in_contents, last_ordinal = True, None
            elif in_contents:
                ordinal = self._contents_ordinal(text)
                if ordinal is not None and (last_ordinal is None or ordinal > last_ordinal):
                    last_ordinal = ordinal
                else:
                    in_contents, last_ordinal = False, None
            if reason is None and bool((node.metadata_ or {}).get("layout_artifact")):
                reason = "LAYOUT_ARTIFACT"
            if reason is None and node.node_type == "TABLE" and self._is_empty_template_table(node.content):
                reason = "EMPTY_TABLE_TEMPLATE"
            if reason is None:
                reason = self._classify(
                    text,
                    visible,
                    (node.section_path or "", key),
                    counts,
                    seen,
                    quality.garbled_ratio,
                )

            # MinerU 偶尔会把一句话末尾拆成很短的单独版面块。它不是噪音时，
            # 同页合回前一块比直接丢弃更接近原文语义；原始 ``content`` 不修改。
            if reason == "TOO_SHORT" and indexable:
                previous = indexable[-1]
                if can_absorb_short_residue(
                    previous.cleaned_content or "",
                    previous.node_type,
                    previous.page_number,
                    text,
                    node.node_type,
                    node.page_number,
                ):
                    previous.cleaned_content = f"{previous.cleaned_content} {text}".strip()
                    previous.cleaning_metadata = {
                        **(previous.cleaning_metadata or {}),
                        "source_order_nos": [
                            *(previous.cleaning_metadata or {}).get(
                                "source_order_nos", [previous.order_no]
                            ),
                            node.order_no,
                        ],
                        "semantic_merge": "SAME_PAGE_SHORT_RESIDUE",
                    }
                    reason = "MERGED_SHORT_RESIDUE"

            node.cleaned_content = text if reason is None else None
            node.tender_req_candidate = reason is None and self._is_requirement_candidate(
                text, node.section_path or "", node.node_type
            )
            node.cleaning_metadata = {
                **(node.cleaning_metadata or {}),
                "indexable": reason is None,
                "reason": reason,
                "raw_characters": raw_count,
                "cleaned_characters": len(visible),
                "garbled_ratio": round(quality.garbled_ratio, 4),
            }
            if reason is None:
                indexable.append(node)
            else:
                reasons[reason] += 1

        self._apply_section_limit(indexable)
        version = await self._session.get(DocumentVersion, version_id)
        if version:
            version.cleaning_summary = {
                "total_nodes": len(nodes),
                "indexable_nodes": len(indexable),
                "filtered_nodes": len(nodes) - len(indexable),
                "tender_candidates": sum(node.tender_req_candidate for node in indexable),
                "raw_characters": raw_chars,
                "cleaned_characters": clean_chars,
                "garbled_ratio": round(garbled_chars / max(clean_chars, 1), 4),
                "filtered_by_reason": dict(sorted(reasons.items())),
                "cleaned_at": datetime.now(UTC).isoformat(),
            }
        return {
            "total": len(nodes),
            "indexable": len(indexable),
            "filtered": len(nodes) - len(indexable),
        }

    @staticmethod
    def _normalize(content: str) -> str:
        normalized = (
            unicodedata.normalize("NFKC", content).replace("\r\n", "\n").replace("\r", "\n")
        )
        normalized = "".join(
            char
            for char in normalized
            if unicodedata.category(char) != "Cf" or char in {"\n", "\t"}
        )
        lines = [_SPACE.sub(" ", line).strip() for line in normalized.split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()

    @staticmethod
    def _duplicate_key(content: str) -> str:
        return re.sub(r"\s+", "", re.sub(r"#+\s*", "", content)).casefold()

    @staticmethod
    def _is_empty_template_table(content: str) -> bool:
        """识别“表头 + 序号 + 空白单元格”的投标格式模板，不作为 RAG 证据。"""
        rows = _TABLE_ROW_RE.findall(content)
        if len(rows) < 2:
            return False

        def cell_text(value: str) -> str:
            return " ".join(unescape(_HTML_TAG_RE.sub("", value)).split())

        data_cells = [
            cell_text(cell)
            for row in rows[1:]
            for cell in _TABLE_CELL_RE.findall(row)
        ]
        if not data_cells:
            return False
        return all(not cell or _TABLE_PLACEHOLDER_RE.fullmatch(cell) for cell in data_cells)

    @staticmethod
    def _contents_ordinal(text: str) -> int | None:
        match = _CONTENTS_ENTRY.match(text)
        if match is None:
            return None
        value = match.group("ordinal")
        return int(value) if value.isdecimal() else _ORDINALS.get(value)

    def _contents_reason(
        self, text: str, in_contents: bool, last_ordinal: int | None
    ) -> str | None:
        if _CONTENTS_HEADING.fullmatch(text):
            return "CONTENTS_HEADING"
        ordinal = self._contents_ordinal(text)
        if in_contents and ordinal is not None and (last_ordinal is None or ordinal > last_ordinal):
            return "CONTENTS_ENTRY"
        return None

    @staticmethod
    def _classify(
        text: str,
        visible: list[str],
        key: tuple[str, str],
        counts: Counter[tuple[str, str]],
        seen: set[tuple[str, str]],
        garbled_ratio: float,
    ) -> str | None:
        if not text or _PAGE_ARTIFACT.fullmatch(text):
            return "PAGE_ARTIFACT"
        if gate := indexability_gate(text):
            return gate
        section_path, content_key = key
        if content_key and counts[key] >= 3:
            if key in seen:
                return "DUPLICATE_FRAGMENT"
            seen.add(key)
        if sum(char.isalnum() or "一" <= char <= "鿿" for char in visible) < _MEANINGFUL_MIN:
            return "TOO_SHORT"
        return "GARBLED_TEXT" if garbled_ratio > 0.35 else None

    @staticmethod
    def _is_requirement_candidate(content: str, section_path: str, node_type: str) -> bool:
        if _NEGATIVE_RE.search(content) or _SECTION_BLACKLIST_RE.search(section_path):
            return False
        if node_type == "SECTION":
            return bool(_CANDIDATE_RE.search(section_path))
        if _PROCEDURAL_RE.search(content) or (len(content) < 40 and _SPEC_ITEM_RE.match(content)):
            return False
        return (
            len(content) >= 30
            and bool(_CANDIDATE_RE.search(content))
            and (
                bool(_OBLIGATION_RE.search(content))
                or (bool(_CONTRACT_SECTION_RE.search(section_path)) and len(content) >= 50)
            )
        )

    @staticmethod
    def _keyword_score(node: DocumentNode) -> int:
        haystack = f"{node.section_path or ''}\n{node.cleaned_content or ''}"
        return sum(keyword in haystack for keyword in _BASE_KEYWORDS) + (
            3 if _OBLIGATION_RE.search(haystack) else 0
        )

    def _apply_section_limit(self, nodes: list[DocumentNode]) -> None:
        groups: dict[str, list[DocumentNode]] = defaultdict(list)
        for node in nodes:
            if node.node_type == "PARAGRAPH" and node.tender_req_candidate:
                groups[(node.section_path or "")[:160]].append(node)
        for node in nodes:
            if node.node_type == "PARAGRAPH":
                node.tender_req_candidate = False
        for group in groups.values():
            for node in sorted(
                group, key=lambda item: (self._keyword_score(item), item.order_no), reverse=True
            )[:_SECTION_CANDIDATE_LIMIT]:
                node.tender_req_candidate = True
