from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Evidence


class EvidenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, evidence_id: UUID) -> Evidence | None:
        return self._session.get(Evidence, evidence_id)

    def add_all(self, evidences: list[Evidence]) -> None:
        self._session.add_all(evidences)

    def list_for_version(self, document_version_id: UUID) -> list[Evidence]:
        statement = (
            select(Evidence)
            .where(Evidence.document_version_id == document_version_id)
            .order_by(Evidence.page_number, Evidence.created_at, Evidence.id)
        )
        return list(self._session.scalars(statement))

    def list_by_ids(self, evidence_ids: list[UUID]) -> dict[UUID, Evidence]:
        """Batch fetch evidences by IDs. Returns {evidence_id: Evidence}."""
        if not evidence_ids:
            return {}
        statement = select(Evidence).where(Evidence.id.in_(evidence_ids))
        rows = self._session.scalars(statement).all()
        return {e.id: e for e in rows}
