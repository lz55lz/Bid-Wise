"""知识库持久化访问；不包含权限判断或任务状态转换。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, cast, delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.models import (
    KnowledgeChunk,
    KnowledgeDocumentVersion,
    KnowledgeEntry,
    KnowledgeParseJob,
    KnowledgeVersion,
)


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def entry(self, entry_id: UUID, *, locked: bool = False) -> KnowledgeEntry | None:
        statement = select(KnowledgeEntry).where(KnowledgeEntry.id == entry_id)
        if locked:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def version(self, version_id: UUID) -> KnowledgeVersion | None:
        return await self._session.get(KnowledgeVersion, version_id)


    async def latest_version(self, entry_id: UUID) -> KnowledgeVersion | None:
        """返回最近创建的知识版本，用于新文件版本继承上一版元数据。"""
        return await self._session.scalar(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.knowledge_entry_id == entry_id)
            .order_by(KnowledgeVersion.version_no.desc(), KnowledgeVersion.id.desc())
            .limit(1)
        )

    async def next_version_no(self, entry_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.max(KnowledgeVersion.version_no)).where(
                KnowledgeVersion.knowledge_entry_id == entry_id
            )
        )
        return int(value or 0) + 1

    async def document(
        self, document_id: UUID, *, locked: bool = False
    ) -> KnowledgeDocumentVersion | None:
        statement = select(KnowledgeDocumentVersion).where(
            KnowledgeDocumentVersion.id == document_id
        )
        if locked:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def document_for_version(self, version_id: UUID) -> KnowledgeDocumentVersion | None:
        return await self._session.scalar(
            select(KnowledgeDocumentVersion).where(
                KnowledgeDocumentVersion.knowledge_version_id == version_id
            )
        )

    async def parse_job(
        self, document_id: UUID, *, locked: bool = False
    ) -> KnowledgeParseJob | None:
        statement = select(KnowledgeParseJob).where(
            KnowledgeParseJob.knowledge_document_version_id == document_id
        )
        if locked:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_parse_jobs_for_recovery(self, stale_before: datetime) -> list[KnowledgeParseJob]:
        statement = (
            select(KnowledgeParseJob)
            .where(
                (KnowledgeParseJob.status == "QUEUED")
                | (
                    (KnowledgeParseJob.status == "RUNNING")
                    & (KnowledgeParseJob.started_at.is_not(None))
                    & (KnowledgeParseJob.started_at < stale_before)
                )
            )
            .order_by(KnowledgeParseJob.created_at, KnowledgeParseJob.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def delete_chunks(self, version_id: UUID) -> None:
        await self._session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_version_id == version_id)
        )

    async def search_published(self, vector: list[float], limit: int):
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        statement = (
            select(KnowledgeEntry, KnowledgeVersion, KnowledgeChunk, distance)
            .join(KnowledgeVersion, KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id)
            .join(KnowledgeChunk, KnowledgeChunk.knowledge_version_id == KnowledgeVersion.id)
            .where(KnowledgeEntry.deleted_at.is_(None), KnowledgeVersion.status == "PUBLISHED")
            .order_by(distance)
            .limit(limit)
        )
        return (await self._session.execute(statement)).all()

    async def search_published_bm25(self, query: str, limit: int):
        """只在已发布公共知识中检索；调用方负责与向量候选融合。"""
        # 词法索引只依赖 chunk 自身列，保证 PostgreSQL 能稳定命中函数 GIN 索引；
        # 条目标题/来源语义已进入 dense embedding，不在这里做跨表函数索引。
        lexical_text = (
            func.coalesce(KnowledgeChunk.section_path, "")
            + literal_column("' '")
            + KnowledgeChunk.content
        )
        zh_config = literal_column("'zh'::regconfig")
        search_vector = func.to_tsvector(zh_config, cast(lexical_text, Text))
        ts_query = func.plainto_tsquery(zh_config, query)
        rank = func.ts_rank_cd(search_vector, ts_query)
        statement = (
            select(KnowledgeEntry, KnowledgeVersion, KnowledgeChunk, rank)
            .join(KnowledgeVersion, KnowledgeVersion.knowledge_entry_id == KnowledgeEntry.id)
            .join(KnowledgeChunk, KnowledgeChunk.knowledge_version_id == KnowledgeVersion.id)
            .where(
                KnowledgeEntry.deleted_at.is_(None),
                KnowledgeVersion.status == "PUBLISHED",
                search_vector.op("@@")(ts_query),
            )
            .order_by(rank.desc(), KnowledgeChunk.order_no)
            .limit(limit)
        )
        return (await self._session.execute(statement)).all()
