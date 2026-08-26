from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import MatchEvidence, MatchOverride, MatchResult


class MatchRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, match_id: UUID, *, for_update: bool = False) -> MatchResult | None:
        statement = select(MatchResult).where(MatchResult.id == match_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_for_project(self, project_id: UUID) -> list[MatchResult]:
        statement = (
            select(MatchResult)
            .where(MatchResult.project_id == project_id)
            .order_by(MatchResult.updated_at.desc(), MatchResult.id)
        )
        return list(self._session.scalars(statement))

    def list_current_for_project(self, project_id: UUID) -> list[MatchResult]:
        statement = (
            select(MatchResult)
            .where(MatchResult.project_id == project_id, MatchResult.is_current.is_(True))
            .order_by(MatchResult.updated_at.desc(), MatchResult.id)
        )
        return list(self._session.scalars(statement))

    def mark_not_current_for_project(self, project_id: UUID) -> None:
        self._session.execute(
            update(MatchResult)
            .where(MatchResult.project_id == project_id, MatchResult.is_current.is_(True))
            .values(is_current=False)
        )

    def find_pair(
        self, requirement_id: UUID, material_id: UUID | None, *, for_update: bool = False
    ) -> MatchResult | None:
        """Find the latest pair across current and historical runs.

        A missing-material row is unique per Requirement at the database level,
        so a rerun must reactivate that row instead of inserting another one
        after ``mark_not_current_for_project``.
        """
        statement = select(MatchResult).where(
            MatchResult.requirement_id == requirement_id,
        )
        statement = (
            statement.where(MatchResult.material_id.is_(None))
            if material_id is None
            else statement.where(MatchResult.material_id == material_id)
        )
        statement = statement.order_by(
            MatchResult.is_current.desc(), MatchResult.updated_at.desc(), MatchResult.id
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def add(self, result: MatchResult) -> None:
        self._session.add(result)

    def add_evidence(self, evidence: MatchEvidence) -> None:
        self._session.add(evidence)

    def add_override(self, override: MatchOverride) -> None:
        self._session.add(override)

    def list_evidence_links(self, match_id: UUID) -> list[tuple[UUID, str]]:
        statement = select(MatchEvidence.evidence_id, MatchEvidence.side).where(
            MatchEvidence.match_result_id == match_id
        )
        return list(self._session.execute(statement).tuples())

    def list_evidence_links_for_matches(
        self, match_ids: list[UUID]
    ) -> dict[UUID, list[tuple[UUID, str]]]:
        """Batch fetch evidence links for multiple matches."""
        if not match_ids:
            return {}
        statement = (
            select(MatchEvidence.match_result_id, MatchEvidence.evidence_id, MatchEvidence.side)
            .where(MatchEvidence.match_result_id.in_(match_ids))
            .order_by(MatchEvidence.match_result_id, MatchEvidence.evidence_id)
        )
        rows = self._session.execute(statement).tuples().all()
        result: dict[UUID, list[tuple[UUID, str]]] = {mid: [] for mid in match_ids}
        for match_id, evidence_id, side in rows:
            result[match_id].append((evidence_id, side))
        return result
