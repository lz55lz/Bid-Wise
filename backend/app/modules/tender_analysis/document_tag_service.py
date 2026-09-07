"""文档已提取标签的只读视图。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.documents.models import DocumentVersion, ProjectDocument
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.tender_analysis.models import TenderDocumentTag, TenderTag


class DocumentTagService:
    """新版的条款/字段事实视图；每一项保留原文节点、置信度和人工复核状态。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, project_id: UUID, document_id: UUID, actor: AuthenticatedUser
    ) -> list[dict[str, object]]:
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._session.scalar(
            select(ProjectDocument).where(
                ProjectDocument.id == document_id,
                ProjectDocument.project_id == project_id,
                ProjectDocument.deleted_at.is_(None),
            )
        )
        if document is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        statement = (
            select(TenderDocumentTag, TenderTag, DocumentVersion.version_no)
            .join(TenderTag, TenderTag.code == TenderDocumentTag.tag_code)
            .join(DocumentVersion, DocumentVersion.id == TenderDocumentTag.document_version_id)
            .where(DocumentVersion.document_id == document.id)
            .order_by(
                DocumentVersion.version_no.desc(),
                TenderDocumentTag.created_at,
                TenderDocumentTag.id,
            )
        )
        return [
            {
                "id": str(item.id),
                "version_no": version_no,
                "code": tag.code,
                "name": tag.name,
                "value": item.value,
                "confidence": item.confidence,
                "source_evidence_id": (
                    None if item.source_evidence_id is None else str(item.source_evidence_id)
                ),
                "source_text": item.source_text,
                "source_page_number": item.source_page_number,
                "review_status": item.review_status,
                "validation_issues": item.validation_issues,
            }
            for item, tag, version_no in (await self._session.execute(statement)).all()
        ]
