# ruff: noqa: E501
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.matching.evaluator import MaterialRequirementEvaluator
from app.modules.matching.models import MaterialMatchResult
from app.modules.matching.repository import MatchingRepository
from app.modules.materials.models import EnterpriseMaterial
from app.modules.projects.models import ProjectEnterprise, TenderProject
from app.modules.requirements.repository import RequirementRepository


class MatchingService:
    """确认态需求与确认态材料的规则匹配编排。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._requirements = RequirementRepository(session)
        self._results = MatchingRepository(session)
        self._evaluator = MaterialRequirementEvaluator()
        self._invalidation = AnalysisInvalidationService(session)

    async def run(self, project_id: UUID) -> list[MaterialMatchResult]:
        """以项目事实源中的截止日匹配，调用方不能通过参数改变规则结论。"""
        project = await self._session.get(TenderProject, project_id)
        if project is None or project.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        bid_deadline = project.bid_deadline.date() if project.bid_deadline else None
        requirements = await self._requirements.list_confirmed(project_id)
        enterprise_ids = list(
            (
                await self._session.scalars(
                    select(ProjectEnterprise.enterprise_id).where(
                        ProjectEnterprise.project_id == project_id
                    )
                )
            ).all()
        )
        if not enterprise_ids:
            raise DomainError("PROJECT_ENTERPRISE_REQUIRED", "项目尚未绑定投标企业", 409)
        materials = list(
            (
                await self._session.scalars(
                    select(EnterpriseMaterial).where(
                        EnterpriseMaterial.status == "CONFIRMED",
                        EnterpriseMaterial.deleted_at.is_(None),
                        EnterpriseMaterial.enterprise_id.in_(enterprise_ids),
                    )
                )
            ).all()
        )
        # 需求被删除、驳回或由新的文档事实替换后，旧匹配结果不能继续进入风险、
        # 决策和报告。当前确认需求集合就是匹配事实的完整作用域。
        requirement_ids = [item.id for item in requirements]
        stale_results = delete(MaterialMatchResult).where(
            MaterialMatchResult.project_id == project_id
        )
        if requirement_ids:
            stale_results = stale_results.where(
                MaterialMatchResult.requirement_id.not_in(requirement_ids)
            )
        await self._session.execute(stale_results)
        output: list[MaterialMatchResult] = []
        status_rank = {"MISSING": 0, "UNCERTAIN": 1, "MATCHED": 2}
        for requirement in requirements:
            best_material = None
            best_evaluation = self._evaluator.evaluate(requirement, None, bid_deadline)
            for material in materials:
                evaluation = self._evaluator.evaluate(requirement, material, bid_deadline)
                if status_rank[evaluation.status] > status_rank[best_evaluation.status]:
                    best_material, best_evaluation = material, evaluation
                if evaluation.status == "MATCHED":
                    break
            current = await self._results.get(project_id, requirement.id)
            if current is None:
                current = MaterialMatchResult(
                    id=uuid4(),
                    project_id=project_id,
                    requirement_id=requirement.id,
                    material_id=None,
                    rule_status="MISSING",
                    final_status="MISSING",
                    reason="",
                    missing_conditions=[],
                    matched_facts=[],
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                self._results.add(current)
            current.material_id = None if best_material is None else best_material.id
            current.rule_status = best_evaluation.status
            if current.overridden_at is None:
                current.final_status = best_evaluation.status
            current.reason, current.missing_conditions = (
                best_evaluation.reason,
                best_evaluation.missing_conditions,
            )
            current.matched_facts = best_evaluation.matched_facts
            current.updated_at = datetime.now(UTC)
            output.append(current)
        # 匹配变化会改变风险和决策；即使本次结果为空，也必须清掉旧下游结果。
        await self._invalidation.invalidate_after_match({project_id})
        return output

    async def list(self, project_id: UUID) -> list[MaterialMatchResult]:
        """读取当前有效需求范围内的匹配结果，供页面和报告使用。"""
        return await self._results.list(project_id)

    async def override(
        self, project_id: UUID, match_id: UUID, actor_id: UUID, final_status: str, reason: str
    ) -> MaterialMatchResult:
        """人工覆盖自动匹配结论，并使风险、决策、报告全部失效重算。"""
        if final_status not in {"MATCHED", "UNCERTAIN", "MISSING"} or not reason.strip():
            raise DomainError("VALIDATION_ERROR", "人工覆盖状态或原因无效", 422)
        item = await self._results.get_by_id(match_id, for_update=True)
        if item is None or item.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "匹配结果不存在", 404)
        item.final_status = final_status
        item.overridden_at, item.overridden_by, item.override_reason = (
            datetime.now(UTC),
            actor_id,
            reason.strip(),
        )
        item.updated_at = datetime.now(UTC)
        await self._invalidation.invalidate_after_match({project_id})
        return item
