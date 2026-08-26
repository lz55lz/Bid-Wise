from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.knowledge import KnowledgeEntry, KnowledgeVersion


class KnowledgeRepository:
    """Persistence-only access for the legal/case knowledge base."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entity: object) -> None:
        self._session.add(entity)

    def add_all(self, entities: list[object]) -> None:
        self._session.add_all(entities)

    def get_entry(self, entry_id: UUID, *, for_update: bool = False) -> KnowledgeEntry | None:
        statement = select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        return self._session.scalar(statement.with_for_update() if for_update else statement)

    def get_version(self, version_id: UUID) -> KnowledgeVersion | None:
        return self._session.get(KnowledgeVersion, version_id)

    def get_version_by_source_document_version(
        self, document_version_id: UUID
    ) -> KnowledgeVersion | None:
        return self._session.scalar(
            select(KnowledgeVersion).where(
                KnowledgeVersion.source_document_version_id == document_version_id
            )
        )

    def latest_version(self, entry_id: UUID) -> KnowledgeVersion | None:
        return self._session.scalar(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.knowledge_entry_id == entry_id)
            .order_by(KnowledgeVersion.version_no.desc())
            .limit(1)
        )

    def next_version_no(self, entry_id: UUID) -> int:
        value = self._session.scalar(
            select(func.max(KnowledgeVersion.version_no)).where(
                KnowledgeVersion.knowledge_entry_id == entry_id
            )
        )
        return int(value or 0) + 1

    def delete_entry(self, entry_id: UUID) -> bool:
        entry = self.get_entry(entry_id)
        if entry is None:
            return False
        self._session.execute(
            KnowledgeEntry.__table__.delete().where(KnowledgeEntry.id == entry_id)
        )
        return True

    def list_entries(
        self, *, published_only: bool, query: str | None = None
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion]]:
        latest = (
            select(
                KnowledgeVersion.knowledge_entry_id,
                func.max(KnowledgeVersion.version_no).label("max_version_no"),
            )
            .group_by(KnowledgeVersion.knowledge_entry_id)
            .subquery()
        )
        statement = (
            select(KnowledgeEntry, KnowledgeVersion)
            .join(KnowledgeVersion, KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id)
            .join(latest, latest.c.knowledge_entry_id == KnowledgeEntry.id)
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeVersion.version_no == latest.c.max_version_no,
            )
        )
        if published_only:
            statement = statement.where(KnowledgeVersion.status == "PUBLISHED")
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                KnowledgeEntry.title.ilike(pattern) | KnowledgeVersion.content.ilike(pattern)
            )
        return list(self._session.execute(statement.order_by(KnowledgeEntry.title)).tuples())

    def list_published_manual_knowledge(
        self, *, limit: int
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion, UUID]]:
        return self._published("LEGAL", limit, include_case=True)

    def list_published_legal_knowledge(
        self, *, limit: int
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion, UUID]]:
        return self._published("LEGAL", limit, include_case=False)

    def _published(
        self, kind: str, limit: int, *, include_case: bool
    ) -> list[tuple[KnowledgeEntry, KnowledgeVersion, UUID]]:
        kinds = ("LEGAL", "CASE") if include_case else (kind,)
        rows = self._session.execute(
            select(KnowledgeEntry, KnowledgeVersion, KnowledgeVersion.source_evidence_id)
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeEntry.knowledge_type.in_(kinds),
                KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id,
                KnowledgeVersion.status == "PUBLISHED",
                KnowledgeVersion.source_document_version_id.is_(None),
                KnowledgeVersion.source_evidence_id.is_not(None),
            )
            .order_by(KnowledgeVersion.published_at.desc(), KnowledgeVersion.created_at.desc())
            .limit(limit)
        ).tuples()
        return [
            (entry, version, evidence_id)
            for entry, version, evidence_id in rows
            if evidence_id is not None
        ]
