# ruff: noqa: E501
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.matching.models import MaterialMatchResult
from app.modules.materials.models import EnterpriseMaterial
from app.modules.projects.models import ProjectEnterprise, TenderProject
from app.modules.requirements.repository import RequirementRepository
from app.modules.risks.models import ProjectRisk
from app.modules.risks.repository import RiskRepository
from app.modules.risks.rule_models import RiskRule, RiskRuleVersion
from app.modules.risks.rule_repository import RiskRuleRepository
from app.modules.risks.rule_service import RiskRuleTemplateService
from app.modules.risks.rules import (
    BUILTIN_RISK_RULES,
    RiskCandidate,
    bid_deadline_expired_risk,
    bid_deadline_missing_risk,
    certificate_expiry_risks,
    mandatory_missing_risks,
    quantitative_requirement_risks,
)


class RiskService:
    """将版本化风险规则投影为项目风险事实，避免报告直接解释规则配置。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._risks = RiskRepository(session)
        self._requirements = RequirementRepository(session)
        self._templates = RiskRuleRepository(session)

    async def run(self, project_id: UUID, actor_id: UUID) -> list[ProjectRisk]:
        """按启用的风险规则模板扫描项目。

        规则版本决定是否启用和风险等级；代码只实现受控的内置执行器及项目字段 DSL。
        因此报告永远只消费 ``ProjectRisk``，不会绕过扫描直接解释模板。
        """
        project = await self._session.get(TenderProject, project_id)
        if project is None or project.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        deadline = project.bid_deadline.date() if project.bid_deadline else None
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
        matches = list(
            (
                await self._session.scalars(
                    select(MaterialMatchResult).where(MaterialMatchResult.project_id == project_id)
                )
            ).all()
        )
        # 内置模板首次使用时才落库，之后启停、严重程度调整均以数据库版本为准。
        await RiskRuleTemplateService(self._session).ensure_builtin_templates(actor_id)
        await self._session.flush()
        active_templates = await self._templates.list_active_versions()
        active_by_code = {rule.code: (rule, version) for rule, version in active_templates}

        builtin_candidates = (
            bid_deadline_missing_risk(deadline)
            + bid_deadline_expired_risk(deadline, datetime.now(UTC).date())
            + certificate_expiry_risks(materials, deadline)
            + quantitative_requirement_risks(requirements, materials)
            + mandatory_missing_risks(requirements, matches)
        )
        candidates: list[tuple[RiskCandidate, RiskRule, RiskRuleVersion]] = [
            (candidate, *active_by_code[candidate.rule_code])
            for candidate in builtin_candidates
            if candidate.rule_code in active_by_code
        ]
        candidates.extend(
            self._custom_project_candidates(project, active_templates, datetime.now(UTC))
        )
        now, output = datetime.now(UTC), []
        for candidate, rule, version in candidates:
            item = await self._risks.get(project_id, version.id, candidate.subject)
            if item is None:
                item = ProjectRisk(
                    id=uuid4(),
                    project_id=project_id,
                    rule_version_id=version.id,
                    rule_code=candidate.rule_code,
                    risk_type=rule.risk_type,
                    severity=version.severity,
                    subject=candidate.subject,
                    title=candidate.title,
                    description=candidate.description,
                    trigger_data=candidate.trigger_data,
                    status="OPEN",
                    created_at=now,
                    updated_at=now,
                )
                self._risks.add(item)
            else:
                item.risk_type, item.severity, item.title, item.description = (
                    rule.risk_type,
                    version.severity,
                    candidate.title,
                    candidate.description,
                )
                item.trigger_data = candidate.trigger_data
                item.updated_at = now
            output.append(item)
        await self._risks.delete_except(project_id, [item.id for item in output])
        await AnalysisInvalidationService(self._session).invalidate_after_risk({project_id})
        return output

    @staticmethod
    def _custom_project_candidates(
        project: TenderProject,
        templates: list[tuple[RiskRule, RiskRuleVersion]],
        now: datetime,
    ) -> list[tuple[RiskCandidate, RiskRule, RiskRuleVersion]]:
        """执行自定义模板的白名单项目字段条件。

        自定义模板不会遍历材料或需求：那些判断依赖匹配语义与证据归属，由内置规则
        执行器负责，避免在 JSON 配置中重新实现业务逻辑。
        """
        builtin_codes = {spec.code for spec in BUILTIN_RISK_RULES}
        output: list[tuple[RiskCandidate, RiskRule, RiskRuleVersion]] = []
        for rule, version in templates:
            if rule.code in builtin_codes or not RiskService._matches_project_definition(
                version.definition, project, now
            ):
                continue
            message = str(version.definition["message_template"]).strip()
            output.append(
                (
                    RiskCandidate(
                        rule_code=rule.code,
                        risk_type=rule.risk_type,
                        severity=version.severity,
                        subject="project:root",
                        title=rule.name,
                        description=message,
                        trigger_data={
                            "rule_version_id": str(version.id),
                            "definition": {"all": version.definition["all"]},
                        },
                    ),
                    rule,
                    version,
                )
            )
        return output

    @staticmethod
    def _matches_project_definition(
        definition: dict[str, object], project: TenderProject, now: datetime
    ) -> bool:
        """解释经过模板服务验证的项目字段条件；异常输入一律视为不命中。"""
        conditions = definition.get("all")
        if not isinstance(conditions, list):
            return False
        for item in conditions:
            if not isinstance(item, dict) or item.get("source") != "project":
                return False
            field, operator = item.get("field"), item.get("op")
            if not isinstance(field, str) or not isinstance(operator, str):
                return False
            expected = item.get("value")
            if isinstance(expected, dict):
                if expected.get("source") != "project" or not isinstance(
                    expected.get("field"), str
                ):
                    return False
                expected = getattr(project, expected["field"], None)
            if not RiskService._condition_matches(
                getattr(project, field, None), operator, expected, now
            ):
                return False
        return True

    @staticmethod
    def _condition_matches(actual: object, operator: str, expected: object, now: datetime) -> bool:
        if operator == "EXISTS":
            return actual is not None
        if operator == "NOT_EXISTS":
            return actual is None
        if operator == "LT_NOW":
            return isinstance(actual, datetime) and actual < now
        if operator == "IN":
            return isinstance(expected, list) and actual in expected
        if operator in {"EQ", "NE"}:
            return actual == expected if operator == "EQ" else actual != expected
        try:
            left, right = Decimal(str(actual)), Decimal(str(expected))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return {
            "GT": left > right,
            "GTE": left >= right,
            "LT": left < right,
            "LTE": left <= right,
        }.get(operator, False)

    async def list(self, project_id: UUID) -> list[ProjectRisk]:
        """读取项目当前风险，不隐式执行扫描以保持查询无副作用。"""
        return await self._risks.list(project_id)

    async def review(
        self, project_id: UUID, risk_id: UUID, status: str, resolution: str | None
    ) -> ProjectRisk:
        """记录人工处置并失效后续决策；处置原因允许按业务规则为空。"""
        if status not in {"ACCEPTED", "RESOLVED", "DISMISSED"}:
            raise DomainError("VALIDATION_ERROR", "风险审核状态无效", 422)
        risk = await self._risks.get_by_id(risk_id, for_update=True)
        if risk is None or risk.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "风险不存在", 404)
        risk.status, risk.resolution, risk.updated_at = status, resolution, datetime.now(UTC)
        await AnalysisInvalidationService(self._session).invalidate_after_risk({project_id})
        return risk
