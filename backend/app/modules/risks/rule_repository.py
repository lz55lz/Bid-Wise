"""自定义风险规则持久化访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.risks.rule_models import RiskRule, RiskRuleVersion


class RiskRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_rules(self) -> list[RiskRule]:
        return list((await self._session.scalars(select(RiskRule).order_by(RiskRule.code))).all())

    async def get_rule(self, rule_id: UUID, *, for_update: bool = False) -> RiskRule | None:
        statement = select(RiskRule).where(RiskRule.id == rule_id)
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_by_code(self, code: str) -> RiskRule | None:
        return await self._session.scalar(select(RiskRule).where(RiskRule.code == code))

    async def get_active_version(self, rule_id: UUID) -> RiskRuleVersion | None:
        return await self._session.scalar(
            select(RiskRuleVersion)
            .where(
                RiskRuleVersion.rule_id == rule_id,
                RiskRuleVersion.is_enabled.is_(True),
                RiskRuleVersion.retired_at.is_(None),
            )
            .order_by(RiskRuleVersion.version_no.desc())
        )

    async def list_active_versions(self) -> list[tuple[RiskRule, RiskRuleVersion]]:
        result = await self._session.execute(
            select(RiskRule, RiskRuleVersion)
            .join(RiskRuleVersion, RiskRuleVersion.rule_id == RiskRule.id)
            .where(RiskRuleVersion.is_enabled.is_(True), RiskRuleVersion.retired_at.is_(None))
            .order_by(RiskRule.code, RiskRuleVersion.version_no.desc())
        )
        return list(result.all())

    async def next_version_no(self, rule_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.max(RiskRuleVersion.version_no)).where(RiskRuleVersion.rule_id == rule_id)
        )
        return int(value or 0) + 1

    def add_rule(self, rule: RiskRule) -> None:
        self._session.add(rule)

    def add_version(self, version: RiskRuleVersion) -> None:
        self._session.add(version)
