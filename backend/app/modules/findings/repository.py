"""发现项仓储：只读写发现项和其 Evidence 关联。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.findings.models import FindingEvidence, ProjectFinding


class FindingRepository:
    """业务授权和状态迁移由 FindingService 处理。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_in_project(self, project_id: UUID, finding_id: UUID) -> ProjectFinding | None:
        return await self._session.scalar(
            select(ProjectFinding).where(
                ProjectFinding.id == finding_id,
                ProjectFinding.project_id == project_id,
            )
        )

    async def list_in_project(self, project_id: UUID, status: str | None) -> list[ProjectFinding]:
        statement = select(ProjectFinding).where(ProjectFinding.project_id == project_id)
        if status is not None:
            statement = statement.where(ProjectFinding.status == status)
        statement = statement.order_by(ProjectFinding.created_at.desc(), ProjectFinding.id)
        return list((await self._session.scalars(statement)).all())

    async def list_evidence_ids(self, finding_id: UUID) -> list[UUID]:
        statement = select(FindingEvidence.evidence_id).where(
            FindingEvidence.finding_id == finding_id
        )
        return list((await self._session.scalars(statement)).all())


    async def list_evidence_ids_by_findings(
        self, finding_ids: list[UUID]
    ) -> dict[UUID, list[UUID]]:
        """批量读取发现项 Evidence 关联，供报告/快照场景避免 N+1 查询。"""
        if not finding_ids:
            return {}
        rows = (
            await self._session.execute(
                select(FindingEvidence.finding_id, FindingEvidence.evidence_id).where(
                    FindingEvidence.finding_id.in_(finding_ids)
                )
            )
        ).all()
        result: dict[UUID, list[UUID]] = {}
        for finding_id, evidence_id in rows:
            result.setdefault(finding_id, []).append(evidence_id)
        return result

    async def list_requiring_stale_mark(self, evidence_ids: list[UUID]) -> list[ProjectFinding]:
        """找出依赖即将删除 Evidence 的有效发现项，不改写已驳回历史。"""
        if not evidence_ids:
            return []
        statement = (
            select(ProjectFinding)
            .join(FindingEvidence, FindingEvidence.finding_id == ProjectFinding.id)
            .where(
                FindingEvidence.evidence_id.in_(evidence_ids),
                ProjectFinding.status.in_(("PENDING_REVIEW", "CONFIRMED")),
            )
            .distinct()
        )
        return list((await self._session.scalars(statement)).all())

    def add(self, finding: ProjectFinding) -> None:
        self._session.add(finding)

    def add_evidence_links(self, finding_id: UUID, evidence_ids: list[UUID]) -> None:
        self._session.add_all(
            FindingEvidence(finding_id=finding_id, evidence_id=evidence_id)
            for evidence_id in evidence_ids
        )
