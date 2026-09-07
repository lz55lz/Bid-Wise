# ruff: noqa: E501
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.decisions.models import BidDecision
from app.modules.matching.models import MaterialMatchResult
from app.modules.risks.models import ProjectRisk


class DecisionService:
    """基于匹配覆盖率和未闭环高风险生成可重建的投标建议。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def generate(self, project_id: UUID) -> BidDecision:
        """高风险优先于覆盖率：存在 OPEN 的 HIGH/CRITICAL 风险时始终暂不建议投标。"""
        matches = list(
            (
                await self._session.scalars(
                    select(MaterialMatchResult).where(MaterialMatchResult.project_id == project_id)
                )
            ).all()
        )
        risks = list(
            (
                await self._session.scalars(
                    select(ProjectRisk).where(ProjectRisk.project_id == project_id)
                )
            ).all()
        )
        blockers = [
            risk
            for risk in risks
            if risk.severity in {"CRITICAL", "HIGH"} and risk.status == "OPEN"
        ]
        total, matched = len(matches), sum(1 for item in matches if item.final_status == "MATCHED")
        score = 0.0 if total == 0 else round(100 * matched / total, 2)
        # 评分只衡量材料匹配覆盖率；风险是硬约束，不能被高覆盖率抵消。
        decision = "NO_BID" if blockers else ("BID" if total and score >= 80 else "PENDING")
        summary = "存在未处理高风险" if blockers else f"材料匹配覆盖率 {score}%"
        snapshot = {
            "matches": [{"id": str(item.id), "status": item.final_status} for item in matches],
            "risks": [
                {
                    "id": str(item.id),
                    "rule_code": item.rule_code,
                    "rule_version_id": None
                    if item.rule_version_id is None
                    else str(item.rule_version_id),
                    "severity": item.severity,
                    "status": item.status,
                }
                for item in risks
            ],
        }
        item = await self._session.scalar(
            select(BidDecision).where(BidDecision.project_id == project_id).with_for_update()
        )
        now = datetime.now(UTC)
        if item is None:
            item = BidDecision(
                id=uuid4(),
                project_id=project_id,
                decision=decision,
                score=score,
                summary=summary,
                input_snapshot=snapshot,
                created_at=now,
                updated_at=now,
            )
            self._session.add(item)
        else:
            item.decision, item.score, item.summary, item.input_snapshot, item.updated_at = (
                decision,
                score,
                summary,
                snapshot,
                now,
            )
        return item

    async def get(self, project_id: UUID) -> BidDecision | None:
        """读取当前决策快照；企业绑定变更后该记录会被显式失效。"""
        return await self._session.scalar(
            select(BidDecision).where(BidDecision.project_id == project_id)
        )
