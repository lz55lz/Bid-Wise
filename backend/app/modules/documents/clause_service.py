"""从清洗后的布局节点派生可追溯招标条款。"""

import hashlib
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import ClauseEvidence, DocumentNode, TenderClause
from app.modules.documents.semantic_boundaries import split_explicit_clause_boundaries
from app.modules.evidence.models import Evidence, EvidenceSourceNode

_MANDATORY = re.compile(r"不得|必须|应当|须|否决|废标|无效|不予|签章|应具备|应满足")


class TenderClauseService:
    """条款仅由有效节点生成；每个条款始终可回查原始 Evidence。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rebuild(self, version_id: UUID) -> int:
        await self._session.execute(
            delete(ClauseEvidence).where(
                ClauseEvidence.clause_id.in_(
                    select(TenderClause.id).where(TenderClause.document_version_id == version_id)
                )
            )
        )
        await self._session.execute(
            delete(TenderClause).where(TenderClause.document_version_id == version_id)
        )
        nodes = list(
            (
                await self._session.scalars(
                    select(DocumentNode)
                    .where(
                        DocumentNode.document_version_id == version_id,
                        DocumentNode.cleaned_content.is_not(None),
                        DocumentNode.node_type != "SECTION",
                    )
                    .order_by(DocumentNode.order_no)
                )
            ).all()
        )
        evidence_rows = await self._session.execute(
            select(EvidenceSourceNode.document_node_id, EvidenceSourceNode.evidence_id)
            .join(Evidence, Evidence.id == EvidenceSourceNode.evidence_id)
            .where(Evidence.document_version_id == version_id)
            .order_by(EvidenceSourceNode.document_node_id, EvidenceSourceNode.ordinal)
        )
        evidence: dict[UUID, list[UUID]] = {}
        for node_id, evidence_id in evidence_rows.all():
            values = evidence.setdefault(node_id, [])
            if evidence_id not in values:
                values.append(evidence_id)
        clauses: list[TenderClause] = []
        links: list[ClauseEvidence] = []
        for node in nodes:
            text = node.cleaned_content or ""
            # 表格和列表是独立版面事实；正文只按原文明确出现的编号边界拆分，
            # 不能为了长度或关键词把相邻段落拼接成一个不可回溯的“智能条款”。
            parts = (
                [text]
                if node.node_type in {"TABLE", "LIST"}
                else split_explicit_clause_boundaries(text)
            )
            for content in parts:
                clause = TenderClause(
                    id=uuid4(),
                    document_version_id=version_id,
                    order_no=len(clauses) + 1,
                    clause_type="REQUIREMENT" if _MANDATORY.search(content) else "TEXT",
                    section_path=node.section_path,
                    start_page=node.page_number,
                    end_page=node.page_number,
                    content=content,
                    contextualized_content=(
                        f"章节：{node.section_path}\n" if node.section_path else ""
                    )
                    + content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    mandatory_signal=bool(_MANDATORY.search(content)),
                    quality_metadata={
                        "source_node_ids": [str(node.id)],
                        "cleaning": node.cleaning_metadata,
                    },
                    created_at=datetime.now(UTC),
                )
                clauses.append(clause)
                for evidence_id in evidence.get(node.id, []):
                    links.append(
                        ClauseEvidence(
                            clause_id=clause.id,
                            evidence_id=evidence_id,
                            relation="DERIVED_FROM",
                        )
                    )
        self._session.add_all(clauses)
        # ClauseEvidence 只保存 UUID 外键，ORM 无法从纯值推导插入顺序；先
        # flush 条款父记录，避免 PostgreSQL 在批量写关联表时找不到 clause_id。
        await self._session.flush()
        self._session.add_all(links)
        return len(clauses)
