from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Requirement, RequirementEvidence


class RequirementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_pending(self, project_id: UUID, category: str, title: str) -> Requirement | None:
        """Find an extraction-managed candidate, including deferred candidates.

        A deferred item remains a valid candidate; rerunning extraction must update it
        instead of producing a duplicate Requirement.
        """
        return self._session.scalar(
            select(Requirement)
            .where(
                Requirement.project_id == project_id,
                Requirement.category == category,
                Requirement.title == title,
                Requirement.review_status.in_(("PENDING", "DEFERRED")),
                Requirement.deleted_at.is_(None),
            )
            .with_for_update()
        )

    def add(self, requirement: Requirement) -> None:
        self._session.add(requirement)

    def add_evidence(self, link: RequirementEvidence) -> None:
        self._session.add(link)

    def list_for_project(self, project_id: UUID) -> list[Requirement]:
        return list(
            self._session.scalars(
                select(Requirement)
                .where(Requirement.project_id == project_id, Requirement.deleted_at.is_(None))
                .order_by(Requirement.created_at.desc())
            )
        )

    def list_confirmed_for_project(self, project_id: UUID) -> list[Requirement]:
        return list(
            self._session.scalars(
                select(Requirement)
                .where(
                    Requirement.project_id == project_id,
                    Requirement.review_status == "CONFIRMED",
                    Requirement.deleted_at.is_(None),
                )
                .order_by(Requirement.category, Requirement.created_at, Requirement.id)
            )
        )

    def has_pending_for_project(self, project_id: UUID) -> bool:
        """Whether high-priority requirements still require a human decision."""
        return (
            self._session.scalar(
                select(Requirement.id).where(
                    Requirement.project_id == project_id,
                    Requirement.review_status == "PENDING",
                    Requirement.deleted_at.is_(None),
                ).limit(1)
            )
            is not None
        )

    def get(self, requirement_id: UUID) -> Requirement | None:
        return self._session.get(Requirement, requirement_id)

    def list_evidence_ids(self, requirement_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(RequirementEvidence.evidence_id).where(
                    RequirementEvidence.requirement_id == requirement_id
                )
            )
        )

    def list_evidence_ids_for_requirements(
        self, requirement_ids: list[UUID]
    ) -> dict[UUID, list[UUID]]:
        """Batch fetch evidence IDs for multiple requirements."""
        if not requirement_ids:
            return {}
        rows = self._session.execute(
            select(RequirementEvidence.requirement_id, RequirementEvidence.evidence_id)
            .where(RequirementEvidence.requirement_id.in_(requirement_ids))
            .order_by(RequirementEvidence.requirement_id, RequirementEvidence.evidence_id)
        ).tuples().all()
        result: dict[UUID, list[UUID]] = {rid: [] for rid in requirement_ids}
        for requirement_id, evidence_id in rows:
            result[requirement_id].append(evidence_id)
        return result
