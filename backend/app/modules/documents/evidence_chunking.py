"""将已清洗 DocumentNode 转换为可追溯的结构化 Evidence。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.documents.models import DocumentNode
from app.modules.retrieval.structured_chunking import ChunkAtom, build_structured_chunks


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    anchor_node_id: UUID
    source_node_ids: tuple[UUID, ...]
    text: str
    section_path: str
    order_start: int
    order_end: int
    page_start: int | None
    page_end: int | None
    node_types: tuple[str, ...]
    clause_keys: tuple[str, ...]
    parent_clause_keys: tuple[str, ...]


def build_evidence_chunks(nodes: list[DocumentNode]) -> list[EvidenceChunk]:
    """结构优先切块：章节/条款边界 > 同章节合并 > 超长递归兜底。"""
    atoms = [
        ChunkAtom(
            source_id=node.id,
            order_no=node.order_no,
            node_type=node.node_type,
            content=(node.cleaned_content or "").strip(),
            section_path=node.section_path,
            page_number=node.page_number,
        )
        for node in nodes
        if (node.cleaned_content or "").strip()
    ]
    chunks = build_structured_chunks(atoms)
    output: list[EvidenceChunk] = []
    for chunk in chunks:
        source_ids = tuple(
            dict.fromkeys(
                atom.source_id for atom in chunk.atoms if isinstance(atom.source_id, UUID)
            )
        )
        if not source_ids:
            continue
        output.append(
            EvidenceChunk(
                anchor_node_id=source_ids[0],
                source_node_ids=source_ids,
                text=chunk.text,
                section_path=chunk.section_path,
                order_start=chunk.order_start,
                order_end=chunk.order_end,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                node_types=chunk.node_types,
                clause_keys=chunk.clause_keys,
                parent_clause_keys=chunk.parent_clause_keys,
            )
        )
    return output
