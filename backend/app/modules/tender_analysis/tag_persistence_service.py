"""投标字段提取结果的审计事实写入。"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tender_analysis.models import TenderDocumentTag


class TenderTagPersistenceService:
    """只负责保存本轮自动提取与人工确认标签，不派生项目业务事实。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_run_tags(
        self,
        *,
        run_id: UUID,
        version_id: UUID,
        extracted: dict[str, dict[str, object]],
        reviewed: dict[str, dict[str, object]],
        reviewer_id: UUID | None,
        now: datetime,
    ) -> None:
        # LangGraph checkpoint/Worker 重试可能重放 persist 节点；run 级先删后写保证幂等。
        await self._session.execute(
            delete(TenderDocumentTag).where(TenderDocumentTag.pipeline_run_id == run_id)
        )
        records: list[TenderDocumentTag] = []
        for code, item in extracted.items():
            if not isinstance(item, dict):
                continue
            node_id = _uuid_or_none(item.get("source_node_id"))
            records.append(
                TenderDocumentTag(
                    id=uuid4(),
                    pipeline_run_id=run_id,
                    document_version_id=version_id,
                    tag_code=code,
                    value=item.get("value"),
                    confidence=float(item.get("confidence", 0)),
                    source_evidence_id=_uuid_or_none(item.get("source_evidence_id")),
                    source_document_node_id=node_id,
                    source_page_number=item.get("source_page_number"),
                    source_text=str(item.get("source_text") or "") or None,
                    extract_method=str(item.get("extract_method") or "LLM"),
                    model_id=str(item.get("model_id") or "") or None,
                    review_status="UNREVIEWED",
                    validation_issues=[],
                    created_at=now,
                )
            )
        for code, item in reviewed.items():
            if not isinstance(item, dict):
                continue
            records.append(
                TenderDocumentTag(
                    id=uuid4(),
                    pipeline_run_id=run_id,
                    document_version_id=version_id,
                    tag_code=code,
                    value=item.get("value"),
                    confidence=float(item.get("confidence", 1)),
                    source_evidence_id=_uuid_or_none(item.get("source_evidence_id")),
                    source_document_node_id=_uuid_or_none(item.get("source_node_id")),
                    source_page_number=item.get("source_page_number"),
                    source_text=str(item.get("source_text") or item.get("note") or "") or None,
                    extract_method="HUMAN",
                    model_id=None,
                    review_status="APPROVED",
                    validation_issues=[],
                    created_at=now,
                    reviewed_at=now,
                    reviewed_by=reviewer_id,
                )
            )
        self._session.add_all(records)


def _uuid_or_none(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None
