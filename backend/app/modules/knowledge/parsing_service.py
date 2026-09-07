"""知识源 Worker 解析与向量入库。"""

import asyncio
import hashlib
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.embedding import BgeM3EmbeddingClient, EmbeddingUnavailable
from app.integrations.mineru import MinerUClient, ParserUnavailable
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.modules.knowledge.cleaning_service import clean_knowledge_nodes
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeParseJob
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.state_machine import transition
from app.modules.retrieval.structured_chunking import ChunkAtom, build_structured_chunks, contextualized_text


logger = logging.getLogger(__name__)


class KnowledgeParsingService:
    """将 MinerU 的结构化文本拆块并向量化；只有任务成功才允许版本发布。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session, self._settings = session, settings
        self._repository = KnowledgeRepository(session)

    async def process(self, job_id: UUID) -> None:
        job = await self._session.get(KnowledgeParseJob, job_id, with_for_update=True)
        if job is None or job.status != "QUEUED":
            return
        document = await self._repository.document(job.knowledge_document_version_id)
        version = (
            None
            if document is None
            else await self._repository.version(document.knowledge_version_id)
        )
        if document is None or version is None:
            await self._failed(job_id, "KNOWLEDGE_SOURCE_NOT_FOUND", "知识源文件不存在")
            return
        entry = await self._repository.entry(version.knowledge_entry_id)
        if entry is None:
            await self._failed(job_id, "KNOWLEDGE_ENTRY_NOT_FOUND", "知识条目不存在")
            return
        now = datetime.now(UTC)
        job.status, job.attempt, job.started_at = "RUNNING", job.attempt + 1, now
        job.progress_stage, job.progress_percent = "PARSING", 10
        job.progress_message, job.updated_at = "正在调用文档解析服务", now
        transition(document, "PARSING")
        document.error_code, document.error_message = None, None
        await self._session.commit()
        output_key: str | None = None
        output_written = False
        try:
            if not self._settings.embedding_is_configured:
                raise EmbeddingUnavailable("bge-m3 服务尚未完成部署配置")
            storage, parser = MinioObjectStorage(self._settings), MinerUClient(self._settings)
            with tempfile.TemporaryDirectory(prefix="bid-wise-knowledge-parse-") as directory:
                source = Path(directory) / Path(document.original_file_name).name
                await asyncio.to_thread(storage.download_to_path, document.object_key, source)
                result = await asyncio.to_thread(parser.parse, source)
                output_key = f"knowledge-parse-output/{document.id}/{uuid4()}.zip"
                await asyncio.to_thread(
                    storage.put_bytes, output_key, result.raw_output, "application/zip"
                )
                output_written = True
            cleaned = clean_knowledge_nodes(result.nodes)
            transition(document, "INDEXING")
            job.progress_stage, job.progress_percent = "CLEANING", 45
            job.progress_message, job.updated_at = "正在清洗目录和版面噪音", datetime.now(UTC)
            document.cleaning_summary = {
                "filtered_contents_entries": cleaned.filtered_contents_entries,
                "filtered_empty_nodes": cleaned.filtered_empty_nodes,
                "indexed_nodes": len(cleaned.nodes),
            }
            await self._session.commit()
            chunks = self._chunks(cleaned.nodes)
            job.progress_stage, job.progress_percent = "INDEXING", 70
            job.progress_message, job.updated_at = "正在生成知识检索索引", datetime.now(UTC)
            vectors = await BgeM3EmbeddingClient(self._settings).embed(
                [contextualized_text(chunk, entry_title=version.title) for chunk in chunks]
            )
            await self._repository.delete_chunks(version.id)
            now = datetime.now(UTC)
            self._session.add_all(
                KnowledgeChunk(
                    id=uuid4(),
                    knowledge_version_id=version.id,
                    order_no=index,
                    content=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    section_path=section_path,
                    embedding=vector,
                    created_at=now,
                )
                for index, (chunk, vector) in enumerate(
                    zip(chunks, vectors, strict=True), start=1
                )
                for text, section_path in [(chunk.text, chunk.section_path or None)]
            )
            version.content = "\n\n".join(node.content for node in cleaned.nodes)
            transition(document, "READY")
            document.parse_output_key, document.completed_at = output_key, now
            job.status, job.completed_at = "SUCCEEDED", now
            job.progress_stage, job.progress_percent = "READY", 100
            job.progress_message, job.updated_at = "解析完成", now
            await self._session.commit()
        except (ObjectStorageUnavailable, ParserUnavailable, EmbeddingUnavailable) as exc:
            await self._session.rollback()
            if output_written and output_key:
                await self._safe_delete_output(output_key)
            logger.warning(
                "知识文件解析/索引依赖不可用 job_id=%s document_id=%s",
                job_id,
                document.id,
                exc_info=True,
            )
            await self._failed(job_id, "KNOWLEDGE_PARSE_FAILED", str(exc))
        except Exception:
            await self._session.rollback()
            if output_written and output_key:
                await self._safe_delete_output(output_key)
            logger.exception(
                "知识文件解析/索引发生未预期异常 job_id=%s document_id=%s",
                job_id,
                document.id,
            )
            await self._failed(job_id, "KNOWLEDGE_PARSE_FAILED", "知识文件解析或索引失败")

    async def _safe_delete_output(self, output_key: str) -> None:
        try:
            await asyncio.to_thread(
                MinioObjectStorage(self._settings).delete_object, output_key
            )
        except Exception:
            logger.warning(
                "清理失败知识解析结果包失败 key=%s", output_key, exc_info=True
            )

    async def _failed(self, job_id: UUID, code: str, message: str) -> None:
        job = await self._session.get(KnowledgeParseJob, job_id, with_for_update=True)
        if job is None:
            return
        document = await self._repository.document(job.knowledge_document_version_id)
        now = datetime.now(UTC)
        job.status, job.error_code, job.error_message, job.completed_at = (
            "FAILED",
            code,
            message[:1000],
            now,
        )
        job.progress_stage, job.progress_percent = "FAILED", 100
        job.progress_message, job.updated_at = job.error_message, now
        if document is not None:
            transition(document, "FAILED")
            document.error_code, document.error_message, document.completed_at = (
                code,
                job.error_message,
                now,
            )
        await self._session.commit()

    @staticmethod
    def _chunks(nodes) :
        """法规/制度按标题与条款结构聚合，超长内容才做递归兜底切分。"""
        atoms = [
            ChunkAtom(
                source_id=index,
                order_no=index,
                node_type=node.node_type,
                content=node.content,
                section_path=node.section_path,
                page_number=node.page_number,
            )
            for index, node in enumerate(nodes, start=1)
            if node.content.strip()
        ]
        chunks = build_structured_chunks(atoms)
        if not chunks:
            raise ValueError("知识源没有可索引正文")
        return chunks
