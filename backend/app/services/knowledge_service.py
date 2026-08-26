from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import LEGAL_COMPLIANCE, SYSTEM_ADMIN
from app.core.errors import DomainError
from app.db.models import DocumentVersion, Evidence, SearchChunk, Task
from app.db.models.knowledge import KnowledgeEntry, KnowledgeVersion
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.knowledge_repository import KnowledgeRepository
from app.integrations.object_storage import MinioObjectStorage
from app.integrations.vector_store import PgVectorStore
from app.schemas.knowledge import (
    KnowledgeCreateRequest,
    KnowledgeResponse,
    KnowledgeRevisionRequest,
)
from app.services.audit_service import AuditService


class KnowledgeService:
    """Legal/case knowledge lifecycle, deliberately independent from advanced analysis."""

    def __init__(
        self, session: Session, storage: MinioObjectStorage, vector_store: PgVectorStore
    ) -> None:
        self._session = session
        self._storage = storage
        self._vector_store = vector_store
        self._repo = KnowledgeRepository(session)
        self._documents = DocumentRepository(session)
        self._audit = AuditService(session)

    def create(
        self, actor_id: UUID, role_codes: set[str], payload: KnowledgeCreateRequest
    ) -> KnowledgeResponse:
        self._require_manager(role_codes)
        now = datetime.now(UTC)
        entry = KnowledgeEntry(
            id=uuid4(),
            knowledge_type=payload.knowledge_type,
            title=payload.title,
            authority=payload.authority,
            source_reference=payload.source_reference,
            created_at=now,
            created_by=actor_id,
            updated_at=now,
            deleted_at=None,
        )
        version = self._draft(entry.id, 1, actor_id, payload)
        evidence = self._manual_evidence(entry, version, actor_id)
        self._repo.add_all([entry, evidence])
        self._session.flush()
        version.source_evidence_id = evidence.id
        self._repo.add(version)
        self._audit.record(
            actor_id=actor_id,
            action="CREATE_KNOWLEDGE_ENTRY",
            target_type="KNOWLEDGE_ENTRY",
            target_id=entry.id,
            after={"knowledge_type": entry.knowledge_type, "version_no": 1},
        )
        self._session.commit()
        return self._response(entry, version)

    def list(self, role_codes: set[str], query: str | None) -> list[KnowledgeResponse]:
        return [
            self._response(entry, version)
            for entry, version in self._repo.list_entries(
                published_only=not self._can_manage(role_codes), query=query
            )
        ]

    def delete(self, entry_id: UUID, actor_id: UUID, role_codes: set[str]) -> None:
        self._require_manager(role_codes)
        entry = self._repo.get_entry(entry_id)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        versions = list(
            self._session.scalars(
                select(KnowledgeVersion).where(KnowledgeVersion.knowledge_entry_id == entry_id)
            )
        )
        source_versions = [
            item.source_document_version_id for item in versions if item.source_document_version_id
        ]
        evidence_ids = {item.source_evidence_id for item in versions if item.source_evidence_id}
        if source_versions:
            evidence_ids.update(
                self._session.scalars(
                    select(Evidence.id).where(Evidence.document_version_id.in_(source_versions))
                ).all()
            )
        if evidence_ids:
            chunk_ids = [
                str(item)
                for item in self._session.scalars(
                    select(SearchChunk.id).where(SearchChunk.evidence_id.in_(evidence_ids))
                ).all()
            ]
            if chunk_ids:
                self._vector_store.delete(chunk_ids)
        for version in versions:
            self._storage.delete_object(f"knowledge-source/{entry.id}/{version.id}/source")
        if source_versions:
            self._session.execute(
                Task.__table__.delete().where(
                    Task.target_type == "DOCUMENT_VERSION", Task.target_id.in_(source_versions)
                )
            )
            self._session.execute(
                DocumentVersion.__table__.delete().where(DocumentVersion.id.in_(source_versions))
            )
        self._repo.delete_entry(entry_id)
        self._audit.record(
            actor_id=actor_id,
            action="DELETE_KNOWLEDGE_ENTRY",
            target_type="KNOWLEDGE_ENTRY",
            target_id=entry_id,
        )
        self._session.commit()

    def revise(
        self,
        entry_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: KnowledgeRevisionRequest,
    ) -> KnowledgeResponse:
        self._require_manager(role_codes)
        entry = self._repo.get_entry(entry_id, for_update=True)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        version = self._draft(entry.id, self._repo.next_version_no(entry.id), actor_id, payload)
        evidence = self._manual_evidence(entry, version, actor_id)
        entry.updated_at = datetime.now(UTC)
        self._repo.add(evidence)
        self._session.flush()
        version.source_evidence_id = evidence.id
        self._repo.add(version)
        self._audit.record(
            actor_id=actor_id,
            action="REVISE_KNOWLEDGE_ENTRY",
            target_type="KNOWLEDGE_ENTRY",
            target_id=entry.id,
            after={"version_no": version.version_no},
        )
        self._session.commit()
        return self._response(entry, version)

    def publish(self, version_id: UUID, actor_id: UUID, role_codes: set[str]) -> KnowledgeResponse:
        self._require_manager(role_codes)
        version, entry = self._version_entry(version_id)
        if version.status != "DRAFT":
            raise DomainError("INVALID_STATE_TRANSITION", "仅草稿版本可以发布", 409)
        if not version.content.strip():
            raise DomainError("KNOWLEDGE_VERSION_NOT_READY", "知识版本尚未生成可发布正文", 409)
        if version.source_document_version_id is not None:
            source = self._documents.get_version(version.source_document_version_id)
            if source is None or source.parse_status != "READY":
                raise DomainError(
                    "KNOWLEDGE_VERSION_NOT_READY", "源文件尚未完成解析和向量索引，不能发布", 409
                )
        for prior in self._session.scalars(
            select(KnowledgeVersion).where(
                KnowledgeVersion.knowledge_entry_id == entry.id,
                KnowledgeVersion.status == "PUBLISHED",
            )
        ):
            prior.status = "DRAFT"
        now = datetime.now(UTC)
        version.status, version.published_at, version.published_by, entry.updated_at = (
            "PUBLISHED",
            now,
            actor_id,
            now,
        )
        self._audit.record(
            actor_id=actor_id,
            action="PUBLISH_KNOWLEDGE_VERSION",
            target_type="KNOWLEDGE_VERSION",
            target_id=version.id,
            after={"entry_id": str(entry.id), "version_no": version.version_no},
        )
        self._session.commit()
        return self._response(entry, version)

    def unpublish(
        self, version_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> KnowledgeResponse:
        self._require_manager(role_codes)
        version, entry = self._version_entry(version_id)
        if version.status != "PUBLISHED":
            raise DomainError("INVALID_STATE_TRANSITION", "仅已发布版本可以停用", 409)
        version.status, version.published_at, version.published_by = "DRAFT", None, None
        entry.updated_at = datetime.now(UTC)
        self._audit.record(
            actor_id=actor_id,
            action="UNPUBLISH_KNOWLEDGE_VERSION",
            target_type="KNOWLEDGE_VERSION",
            target_id=version.id,
            after={"entry_id": str(entry.id), "version_no": version.version_no},
        )
        self._session.commit()
        return self._response(entry, version)

    def _version_entry(self, version_id: UUID) -> tuple[KnowledgeVersion, KnowledgeEntry]:
        version = self._repo.get_version(version_id)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识版本不存在", 404)
        entry = self._repo.get_entry(version.knowledge_entry_id, for_update=True)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        return version, entry

    @staticmethod
    def _draft(
        entry_id: UUID,
        version_no: int,
        actor_id: UUID,
        payload: KnowledgeCreateRequest | KnowledgeRevisionRequest,
    ) -> KnowledgeVersion:
        return KnowledgeVersion(
            id=uuid4(),
            knowledge_entry_id=entry_id,
            version_no=version_no,
            status="DRAFT",
            content=payload.content,
            issued_on=payload.issued_on,
            effective_on=payload.effective_on,
            citation_note=payload.citation_note,
            source_document_version_id=None,
            source_evidence_id=None,
            published_at=None,
            published_by=None,
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )

    def _response(self, entry: KnowledgeEntry, version: KnowledgeVersion) -> KnowledgeResponse:
        source = (
            self._documents.get_version(version.source_document_version_id)
            if version.source_document_version_id
            else None
        )
        return KnowledgeResponse(
            entry_id=entry.id,
            version_id=version.id,
            version_no=version.version_no,
            knowledge_type=entry.knowledge_type,
            title=entry.title,
            authority=entry.authority,
            source_reference=entry.source_reference,
            status=version.status,
            content=version.content,
            issued_on=version.issued_on,
            effective_on=version.effective_on,
            citation_note=version.citation_note,
            source_document_version_id=version.source_document_version_id,
            source_parse_status=None if source is None else source.parse_status,
            source_cleaning_summary=None if source is None else source.cleaning_summary,
            published_at=version.published_at,
            created_at=version.created_at,
        )

    @staticmethod
    def _manual_evidence(
        entry: KnowledgeEntry, version: KnowledgeVersion, actor_id: UUID
    ) -> Evidence:
        content = version.content.strip()
        return Evidence(
            id=uuid4(),
            source_type="USER_CONFIRMATION",
            document_version_id=None,
            document_node_id=None,
            page_number=None,
            quoted_text=content[:1_000],
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            bbox=None,
            source_reference={
                "knowledge_entry_id": str(entry.id),
                "knowledge_version_id": str(version.id),
                "source_reference": entry.source_reference,
            },
            confidence=None,
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )

    @staticmethod
    def _can_manage(role_codes: set[str]) -> bool:
        return bool({SYSTEM_ADMIN, LEGAL_COMPLIANCE}.intersection(role_codes))

    def _require_manager(self, role_codes: set[str]) -> None:
        if not self._can_manage(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护法规/案例知识库", 403)
