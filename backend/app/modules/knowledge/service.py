"""知识条目与版本发布服务。"""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient, EmbeddingUnavailable
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.knowledge.models import (
    KnowledgeChunk,
    KnowledgeDocumentVersion,
    KnowledgeEntry,
    KnowledgeParseJob,
    KnowledgeVersion,
)
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.retrieval.structured_chunking import (
    build_structured_chunks,
    contextualized_text,
    markdown_atoms,
)


class KnowledgeService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._repository = KnowledgeRepository(session)
        self._settings = settings

    @staticmethod
    def _manager(actor: AuthenticatedUser) -> None:
        if not {"SYSTEM_ADMIN", "LEGAL_COMPLIANCE"}.intersection(actor.role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护知识库", 403)

    async def list(self, actor: AuthenticatedUser, query: str | None) -> list[dict[str, object]]:
        manage = bool({"SYSTEM_ADMIN", "LEGAL_COMPLIANCE"}.intersection(actor.role_codes))
        stmt = (
            select(KnowledgeEntry, KnowledgeVersion, KnowledgeDocumentVersion, KnowledgeParseJob)
            .select_from(KnowledgeEntry)
            .join(KnowledgeVersion, KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id)
            .outerjoin(
                KnowledgeDocumentVersion,
                KnowledgeDocumentVersion.knowledge_version_id == KnowledgeVersion.id,
            )
            .outerjoin(
                KnowledgeParseJob,
                KnowledgeParseJob.knowledge_document_version_id == KnowledgeDocumentVersion.id,
            )
            .where(KnowledgeEntry.deleted_at.is_(None))
        )
        if not manage:
            stmt = stmt.where(KnowledgeVersion.status == "PUBLISHED")
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                KnowledgeVersion.title.ilike(pattern)
                | KnowledgeVersion.source_reference.ilike(pattern)
                | KnowledgeVersion.content.ilike(pattern)
            )
        rows = (
            await self._session.execute(
                stmt.order_by(KnowledgeVersion.title, KnowledgeVersion.version_no.desc())
            )
        ).all()
        return [self._row(entry, version, document, job) for entry, version, document, job in rows]

    async def create(self, actor: AuthenticatedUser, data: dict[str, str]) -> dict[str, object]:
        self._manager(actor)
        now = datetime.now(UTC)
        entry = KnowledgeEntry(
            id=uuid4(),
            knowledge_type=data["knowledge_type"],
            title=data["title"],
            authority=data.get("authority"),
            source_reference=data["source_reference"],
            issued_on=data.get("issued_on"),
            effective_on=data.get("effective_on"),
            citation_note=data.get("citation_note"),
            created_at=now,
            created_by=actor.id,
            updated_at=now,
            deleted_at=None,
        )
        version = KnowledgeVersion(
            id=uuid4(),
            knowledge_entry_id=entry.id,
            version_no=1,
            status="DRAFT",
            content=data["content"],
            title=data["title"],
            authority=data.get("authority"),
            source_reference=data["source_reference"],
            issued_on=data.get("issued_on"),
            effective_on=data.get("effective_on"),
            citation_note=data.get("citation_note"),
            published_at=None,
            published_by=None,
            created_at=now,
            created_by=actor.id,
        )
        self._session.add_all([entry, version])
        self._audit(actor.id, "CREATE_KNOWLEDGE_ENTRY", entry.id, now)
        return self._row(entry, version)

    async def create_document_entry(
        self,
        actor: AuthenticatedUser,
        *,
        knowledge_type: str,
        title: str,
        source_reference: str,
        authority: str | None,
        issued_on: datetime | None,
        effective_on: datetime | None,
        citation_note: str | None,
    ) -> KnowledgeEntry:
        """先创建知识条目，再由文件上传创建其首个草稿版本。

        文件型知识不能复用 ``create``：后者会提前建立空正文版本，导致上传文件从
        第二版开始且留下不可发布的空版本。
        """
        self._manager(actor)
        now = datetime.now(UTC)
        entry = KnowledgeEntry(
            id=uuid4(),
            knowledge_type=knowledge_type,
            title=title,
            authority=authority,
            source_reference=source_reference,
            issued_on=issued_on,
            effective_on=effective_on,
            citation_note=citation_note,
            created_at=now,
            created_by=actor.id,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(entry)
        await self._session.flush()
        self._audit(actor.id, "CREATE_KNOWLEDGE_DOCUMENT_ENTRY", entry.id, now)
        return entry

    async def revise(
        self, actor: AuthenticatedUser, entry_id: UUID, data: dict[str, str]
    ) -> dict[str, object]:
        """手工正文也保留不可变版本历史，不能覆盖已发布内容。"""
        self._manager(actor)
        entry = await self._repository.entry(entry_id, locked=True)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        now = datetime.now(UTC)
        baseline = await self._repository.latest_version(entry.id)
        version = KnowledgeVersion(
            id=uuid4(),
            knowledge_entry_id=entry.id,
            version_no=await self._repository.next_version_no(entry.id),
            status="DRAFT",
            content=data["content"],
            title=str(data.get("title") or (baseline.title if baseline is not None else entry.title)),
            authority=data.get(
                "authority", baseline.authority if baseline is not None else entry.authority
            ),
            source_reference=str(
                data.get("source_reference")
                or (baseline.source_reference if baseline is not None else entry.source_reference)
            ),
            issued_on=data.get(
                "issued_on", baseline.issued_on if baseline is not None else entry.issued_on
            ),
            effective_on=data.get(
                "effective_on",
                baseline.effective_on if baseline is not None else entry.effective_on,
            ),
            citation_note=data.get(
                "citation_note",
                baseline.citation_note if baseline is not None else entry.citation_note,
            ),
            published_at=None,
            published_by=None,
            created_at=now,
            created_by=actor.id,
        )
        self._session.add(version)
        self._audit(actor.id, "REVISE_KNOWLEDGE_ENTRY", version.id, now)
        return self._row(entry, version)

    async def delete(self, actor: AuthenticatedUser, entry_id: UUID) -> None:
        """软删除撤销公共检索可见性，源文件与审计仍可受控保留。"""
        self._manager(actor)
        entry = await self._repository.entry(entry_id, locked=True)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        now = datetime.now(UTC)
        entry.deleted_at, entry.updated_at = now, now
        self._audit(actor.id, "DELETE_KNOWLEDGE_ENTRY", entry.id, now)

    async def publish(
        self, actor: AuthenticatedUser, version_id: UUID, published: bool
    ) -> dict[str, object]:
        self._manager(actor)
        version = await self._session.get(KnowledgeVersion, version_id, with_for_update=True)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识版本不存在", 404)
        # 锁条目本身而不是只锁“当前已发布版本”。当尚无 PUBLISHED 行时，后者无法
        # 阻止两个并发请求同时发布两个草稿版本。
        entry = await self._repository.entry(version.knowledge_entry_id, locked=True)
        if entry is None or entry.deleted_at:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        now = datetime.now(UTC)
        if published:
            source = await self._repository.document_for_version(version.id)
            if source is not None and source.parse_status != "READY":
                raise DomainError(
                    "KNOWLEDGE_VERSION_NOT_READY", "源文件尚未完成解析和向量入库", 409
                )
            if source is None and not version.content.strip():
                raise DomainError("KNOWLEDGE_VERSION_NOT_READY", "知识版本尚无可发布正文", 409)
            if source is None:
                await self._index_manual_content(version)
            for old in (
                await self._session.scalars(
                    select(KnowledgeVersion).where(
                        KnowledgeVersion.knowledge_entry_id == entry.id,
                        KnowledgeVersion.status == "PUBLISHED",
                    )
                )
            ).all():
                old.status = "DRAFT"
                old.published_at = None
                old.published_by = None
            version.status, version.published_at, version.published_by = "PUBLISHED", now, actor.id
            # Entry 是逻辑条目的“当前已发布摘要”。只有真正发布成功时才更新它，
            # DRAFT 元数据因此不会和旧 PUBLISHED 正文混搭。
            entry.title = version.title
            entry.authority = version.authority
            entry.source_reference = version.source_reference
            entry.issued_on = version.issued_on
            entry.effective_on = version.effective_on
            entry.citation_note = version.citation_note
        else:
            version.status, version.published_at, version.published_by = "DRAFT", None, None
        entry.updated_at = now
        self._audit(
            actor.id,
            "PUBLISH_KNOWLEDGE_VERSION" if published else "UNPUBLISH_KNOWLEDGE_VERSION",
            version.id,
            now,
        )
        return self._row(entry, version)

    async def rebuild_manual_index(
        self, actor: AuthenticatedUser, version_id: UUID
    ) -> dict[str, object]:
        """为历史手工正文补建向量；文件型知识仍走独立解析任务。"""
        self._manager(actor)
        version = await self._repository.version(version_id)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识版本不存在", 404)
        if await self._repository.document_for_version(version.id) is not None:
            raise DomainError(
                "VALIDATION_ERROR",
                "文件型知识索引随源文件解析生成；如内容变化请上传新的知识版本",
                409,
            )
        await self._index_manual_content(version)
        self._audit(actor.id, "REBUILD_MANUAL_KNOWLEDGE_INDEX", version.id, datetime.now(UTC))
        entry = await self._repository.entry(version.knowledge_entry_id)
        assert entry is not None
        return self._row(entry, version)

    async def _index_manual_content(self, version: KnowledgeVersion) -> None:
        """手工正文与文件型知识共用结构化切块和检索上下文，避免重建索引行为漂移。"""
        if not self._settings.embedding_is_configured:
            raise DomainError("EMBEDDING_UNAVAILABLE", "向量服务尚未完成部署配置", 503)
        entry = await self._repository.entry(version.knowledge_entry_id)
        if entry is None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        chunks = build_structured_chunks(markdown_atoms(version.content))
        if not chunks:
            raise DomainError("KNOWLEDGE_VERSION_NOT_READY", "知识版本尚无可索引正文", 409)
        inputs = [contextualized_text(chunk, entry_title=version.title) for chunk in chunks]
        try:
            vectors = await BgeM3EmbeddingClient(self._settings).embed(inputs)
        except EmbeddingUnavailable as exc:
            raise DomainError("EMBEDDING_UNAVAILABLE", "向量服务暂不可用", 503) from exc
        await self._repository.delete_chunks(version.id)
        now = datetime.now(UTC)
        self._session.add_all(
            KnowledgeChunk(
                id=uuid4(),
                knowledge_version_id=version.id,
                order_no=index,
                content=chunk.text,
                content_hash=hashlib.sha256(chunk.text.encode()).hexdigest(),
                section_path=chunk.section_path or None,
                embedding=vector,
                created_at=now,
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True), start=1)
        )

    def _audit(self, actor_id: UUID, action: str, target: UUID, now: datetime) -> None:
        self._session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_type="KNOWLEDGE",
                target_id=target,
                project_id=None,
                created_at=now,
            )
        )

    @staticmethod
    def _row(
        e: KnowledgeEntry,
        v: KnowledgeVersion,
        document: KnowledgeDocumentVersion | None = None,
        job: KnowledgeParseJob | None = None,
    ) -> dict[str, object]:
        return {
            "entry_id": str(e.id),
            "version_id": str(v.id),
            "version_no": v.version_no,
            "knowledge_type": e.knowledge_type,
            "title": v.title,
            "authority": v.authority,
            "source_reference": v.source_reference,
            "issued_on": v.issued_on,
            "effective_on": v.effective_on,
            "citation_note": v.citation_note,
            "status": v.status,
            "content": v.content,
            "published_at": v.published_at,
            "created_at": v.created_at,
            "source_document_version_id": None if document is None else str(document.id),
            "source_parse_status": None if document is None else document.parse_status,
            "source_parse_error": None if document is None else document.error_message,
            "source_cleaning_summary": None if document is None else document.cleaning_summary,
            "source_parse_progress": None if job is None else job.progress_percent,
            "source_parse_message": None if job is None else job.progress_message,
        }
