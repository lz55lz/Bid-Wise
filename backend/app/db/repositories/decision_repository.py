from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Decision, DecisionEvidence


class DecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, decision_id: UUID, *, for_update: bool = False) -> Decision | None:
        statement = select(Decision).where(Decision.id == decision_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def latest_for_project(self, project_id: UUID, *, for_update: bool = False) -> Decision | None:
        statement = (
            select(Decision)
            .where(Decision.project_id == project_id)
            .order_by(Decision.created_at.desc(), Decision.id.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, decision: Decision) -> None:
        self._session.add(decision)

    def add_evidence(self, link: DecisionEvidence) -> None:
        self._session.add(link)

    def list_evidence_ids(self, decision_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(DecisionEvidence.evidence_id).where(
                    DecisionEvidence.decision_id == decision_id
                )
            )
        )
