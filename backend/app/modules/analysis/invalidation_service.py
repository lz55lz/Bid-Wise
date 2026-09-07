"""当前分析结果的统一失效规则。

上游事实变化后，哪些派生结果仍可作为“当前结果”必须只有一处定义。这里不提交事务，
由调用用例决定原子边界。
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.decisions.models import BidDecision
from app.modules.matching.models import MaterialMatchResult
from app.modules.reports.repository import ReportRepository
from app.modules.risks.models import ProjectRisk


class AnalysisInvalidationService:
    """维护 Requirement/Material/Project -> Match -> Risk -> Decision -> Report 依赖链。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._reports = ReportRepository(session)

    @staticmethod
    def _project_ids(values: Iterable[UUID]) -> set[UUID]:
        return set(values)

    async def invalidate_inputs(self, project_ids: Iterable[UUID]) -> None:
        """需求、材料、截止时间或投标企业范围变化：全部派生结果失效。"""
        ids = self._project_ids(project_ids)
        if not ids:
            return
        await self._session.execute(
            delete(MaterialMatchResult).where(MaterialMatchResult.project_id.in_(ids))
        )
        await self._session.execute(delete(ProjectRisk).where(ProjectRisk.project_id.in_(ids)))
        await self._session.execute(delete(BidDecision).where(BidDecision.project_id.in_(ids)))
        await self._reports.mark_stale_for_projects(ids)

    async def invalidate_after_match(self, project_ids: Iterable[UUID]) -> None:
        """当前匹配被人工覆盖或重算：保留 Match，失效 Risk/Decision/Report。"""
        ids = self._project_ids(project_ids)
        if not ids:
            return
        await self._session.execute(delete(ProjectRisk).where(ProjectRisk.project_id.in_(ids)))
        await self._session.execute(delete(BidDecision).where(BidDecision.project_id.in_(ids)))
        await self._reports.mark_stale_for_projects(ids)

    async def invalidate_after_risk(self, project_ids: Iterable[UUID]) -> None:
        """当前风险变化：保留 Risk，失效 Decision/Report。"""
        ids = self._project_ids(project_ids)
        if not ids:
            return
        await self._session.execute(delete(BidDecision).where(BidDecision.project_id.in_(ids)))
        await self._reports.mark_stale_for_projects(ids)

    async def invalidate_report(self, project_ids: Iterable[UUID]) -> None:
        """只改变报告正文输入的事实变化。"""
        ids = self._project_ids(project_ids)
        if ids:
            await self._reports.mark_stale_for_projects(ids)
