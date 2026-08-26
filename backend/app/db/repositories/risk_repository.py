from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Risk, RiskEvidence, Rule, RuleVersion


class RuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_code(self, code: str) -> Rule | None:
        return self._session.scalar(select(Rule).where(Rule.code == code).with_for_update())

    def get(self, rule_id: UUID, *, for_update: bool = False) -> Rule | None:
        statement = select(Rule).where(Rule.id == rule_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_rules(self) -> list[Rule]:
        return list(self._session.scalars(select(Rule).order_by(Rule.code)))

    def get_active_version(self, rule_id: UUID) -> RuleVersion | None:
        return self._session.scalar(
            select(RuleVersion)
            .where(
                RuleVersion.rule_id == rule_id,
                RuleVersion.is_enabled.is_(True),
                RuleVersion.retired_at.is_(None),
            )
            .order_by(RuleVersion.version_no.desc())
            .limit(1)
        )

    def list_active_versions(self) -> list[tuple[Rule, RuleVersion]]:
        statement = (
            select(Rule, RuleVersion)
            .join(RuleVersion, RuleVersion.rule_id == Rule.id)
            .where(
                RuleVersion.is_enabled.is_(True),
                RuleVersion.retired_at.is_(None),
            )
            .order_by(Rule.code, RuleVersion.version_no.desc())
        )
        return list(self._session.execute(statement).tuples())

    def next_version_no(self, rule_id: UUID) -> int:
        value = self._session.scalar(
            select(RuleVersion.version_no)
            .where(RuleVersion.rule_id == rule_id)
            .order_by(RuleVersion.version_no.desc())
            .limit(1)
        )
        return int(value or 0) + 1

    def add_rule(self, rule: Rule) -> None:
        self._session.add(rule)

    def add_version(self, version: RuleVersion) -> None:
        self._session.add(version)


class RiskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, risk_id: UUID, *, for_update: bool = False) -> Risk | None:
        statement = select(Risk).where(Risk.id == risk_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_for_project(self, project_id: UUID) -> list[Risk]:
        statement = (
            select(Risk)
            .where(Risk.project_id == project_id)
            .order_by(Risk.updated_at.desc(), Risk.id)
        )
        return list(self._session.scalars(statement))

    def list_current_for_project(self, project_id: UUID) -> list[Risk]:
        statement = (
            select(Risk)
            .where(Risk.project_id == project_id, Risk.is_current.is_(True))
            .order_by(Risk.updated_at.desc(), Risk.id)
        )
        return list(self._session.scalars(statement))

    def mark_not_current_for_project(self, project_id: UUID) -> None:
        self._session.execute(
            update(Risk)
            .where(Risk.project_id == project_id, Risk.is_current.is_(True))
            .values(is_current=False)
        )

    def find_by_rule_subject(
        self, project_id: UUID, rule_version_id: UUID, subject: str
    ) -> Risk | None:
        return self._session.scalar(
            select(Risk)
            .where(
                Risk.project_id == project_id,
                Risk.rule_version_id == rule_version_id,
                Risk.status.in_(("PENDING", "CONFIRMED")),
                Risk.trigger_data.contains({"subject": subject}),
            )
            .with_for_update()
        )

    def add(self, risk: Risk) -> None:
        self._session.add(risk)

    def add_evidence(self, link: RiskEvidence) -> None:
        self._session.add(link)

    def list_evidence_ids(self, risk_id: UUID) -> list[UUID]:
        statement = select(RiskEvidence.evidence_id).where(RiskEvidence.risk_id == risk_id)
        return list(self._session.scalars(statement))

    def list_evidence_ids_for_risks(self, risk_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        """Batch fetch evidence IDs for multiple risks. Returns {risk_id: [evidence_ids]}."""
        if not risk_ids:
            return {}
        rows = self._session.execute(
            select(RiskEvidence.risk_id, RiskEvidence.evidence_id)
            .where(RiskEvidence.risk_id.in_(risk_ids))
            .order_by(RiskEvidence.risk_id, RiskEvidence.evidence_id)
        ).tuples().all()
        result: dict[UUID, list[UUID]] = {rid: [] for rid in risk_ids}
        for risk_id, evidence_id in rows:
            result[risk_id].append(evidence_id)
        return result
