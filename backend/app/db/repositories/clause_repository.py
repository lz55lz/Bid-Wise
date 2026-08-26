from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ClauseEvidence, TenderClause


class ClauseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_for_version(
        self, version_id: UUID, clauses: list[TenderClause], links: list[ClauseEvidence]
    ) -> None:
        clause_ids = select(TenderClause.id).where(TenderClause.document_version_id == version_id)
        self._session.execute(delete(ClauseEvidence).where(ClauseEvidence.clause_id.in_(clause_ids)))
        self._session.execute(
            delete(TenderClause).where(TenderClause.document_version_id == version_id)
        )
        self._session.add_all(clauses)
        # ClauseEvidence uses scalar FK values rather than ORM relationships, so
        # make parent rows visible before inserting the links.
        self._session.flush()
        self._session.add_all(links)

    def list_for_version(self, version_id: UUID) -> list[TenderClause]:
        return list(self._session.scalars(
            select(TenderClause)
            .where(TenderClause.document_version_id == version_id)
            .order_by(TenderClause.order_no)
        ))

    def primary_evidence_ids(self, clause_ids: list[UUID]) -> dict[UUID, UUID]:
        if not clause_ids:
            return {}
        rows = self._session.execute(
            select(ClauseEvidence.clause_id, ClauseEvidence.evidence_id)
            .where(ClauseEvidence.clause_id.in_(clause_ids))
            .order_by(ClauseEvidence.clause_id, ClauseEvidence.evidence_id)
        ).tuples()
        result: dict[UUID, UUID] = {}
        for clause_id, evidence_id in rows:
            result.setdefault(clause_id, evidence_id)
        return result
