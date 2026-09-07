from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import DocumentVersion, ProjectDocument
from app.modules.requirements.models import ProjectField, TenderRequirement


def _current_pipeline_projection(model):
    """Pipeline 派生事实只在其来源版本仍是所属文档 current_version 时生效。"""
    current_source = (
        select(DocumentVersion.id)
        .join(ProjectDocument, ProjectDocument.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.id == model.source_document_version_id,
            ProjectDocument.project_id == model.project_id,
            ProjectDocument.current_version_id == DocumentVersion.id,
            DocumentVersion.parse_status == "READY",
        )
        .exists()
    )
    return or_(model.extraction_source != "PIPELINE_HUMAN", current_source)


class RequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, requirement_id: UUID, *, for_update: bool = False
    ) -> TenderRequirement | None:
        statement = select(TenderRequirement).where(
            TenderRequirement.id == requirement_id,
            TenderRequirement.deleted_at.is_(None),
            _current_pipeline_projection(TenderRequirement),
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_confirmed(self, project_id: UUID) -> list[TenderRequirement]:
        statement = (
            select(TenderRequirement)
            .where(
                TenderRequirement.project_id == project_id,
                TenderRequirement.review_status == "CONFIRMED",
                TenderRequirement.deleted_at.is_(None),
                _current_pipeline_projection(TenderRequirement),
            )
            .order_by(TenderRequirement.category, TenderRequirement.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_active(self, project_id: UUID) -> list[TenderRequirement]:
        """审核页只展示当前文档版本对应的未删除需求。"""
        statement = (
            select(TenderRequirement)
            .where(
                TenderRequirement.project_id == project_id,
                TenderRequirement.deleted_at.is_(None),
                _current_pipeline_projection(TenderRequirement),
            )
            .order_by(
                TenderRequirement.category,
                TenderRequirement.created_at,
                TenderRequirement.id,
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def list_fields(self, project_id: UUID) -> list[ProjectField]:
        statement = (
            select(ProjectField)
            .where(
                ProjectField.project_id == project_id,
                _current_pipeline_projection(ProjectField),
            )
            .order_by(ProjectField.field_code, ProjectField.created_at, ProjectField.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_field(self, field_id: UUID, *, for_update: bool = False) -> ProjectField | None:
        statement = select(ProjectField).where(
            ProjectField.id == field_id,
            _current_pipeline_projection(ProjectField),
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def count_pending_by_projects(
        self, project_ids: list[UUID]
    ) -> list[tuple[UUID, int]]:
        if not project_ids:
            return []
        rows = await self._session.execute(
            select(TenderRequirement.project_id, func.count())
            .where(
                TenderRequirement.project_id.in_(project_ids),
                TenderRequirement.review_status == "PENDING",
                TenderRequirement.deleted_at.is_(None),
                _current_pipeline_projection(TenderRequirement),
            )
            .group_by(TenderRequirement.project_id)
        )
        return [(project_id, int(count)) for project_id, count in rows.all()]
