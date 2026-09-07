"""Worker 专用的项目文档解析编排。

设计目标：PostgreSQL 保存可恢复状态；MinerU/对象存储/Embedding 等慢 IO 不占用长事务。
新版本在索引完整前只作为 staging 数据存在，RAG 仅检索 READY 的当前版本。
"""

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
from app.integrations.mineru import MinerUClient, ParseResult, ParserUnavailable
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.documents.clause_service import TenderClauseService
from app.modules.documents.cleaning_service import DocumentCleaningService
from app.modules.documents.evidence_chunking import build_evidence_chunks
from app.modules.documents.models import DocumentNode, DocumentVersion, ProjectDocument
from app.modules.documents.repository import DocumentRepository
from app.modules.documents.state_machine import transition
from app.modules.evidence.models import Evidence, EvidenceSourceNode
from app.modules.evidence.repository import EvidenceRepository
from app.modules.findings.service import FindingInvalidationService
from app.modules.retrieval.models import EvidenceEmbedding
from app.modules.retrieval.structured_chunking import retrieval_text

logger = logging.getLogger(__name__)


class DocumentParsingService:
    """解析、清洗、Evidence 构建、索引和版本激活的应用编排。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._documents = DocumentRepository(session)
        self._evidences = EvidenceRepository(session)

    async def process(self, job_id: UUID) -> None:
        job = await self._documents.get_parse_job_for_update(job_id)
        if job is None or job.status != "QUEUED":
            return
        version = await self._documents.get_version(job.document_version_id)
        if version is None:
            await self._mark_failed(job_id, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在")
            return
        document = await self._documents.get_active(version.document_id)
        if document is None:
            await self._mark_failed(job_id, "DOCUMENT_NOT_FOUND", "文档不存在")
            return

        await self._mark_running(job, version)
        output_key: str | None = None
        output_persisted = False
        try:
            if not self._settings.embedding_is_configured:
                raise EmbeddingUnavailable("bge-m3 服务尚未完成部署配置")

            # 慢 IO 全部在事务外执行。此时旧 READY 版本仍可继续提供检索。
            storage = MinioObjectStorage(self._settings)
            result, output_key = await self._parse_source(storage, version)

            # staging 事务只写本版本原始节点、清洗结果和 Evidence，不切当前版本。
            evidences = await self._stage_parsed_version(job, version, document, result, output_key)
            output_persisted = True

            embedding_inputs = [self._embedding_text(document, item) for item in evidences]
            vectors = await BgeM3EmbeddingClient(self._settings).embed(embedding_inputs)

            # 最后用一个短事务写向量/条款并原子激活新版本。
            await self._activate_version(job, version, document, evidences, vectors)
        except (ObjectStorageUnavailable, ParserUnavailable, EmbeddingUnavailable) as exc:
            await self._session.rollback()
            # 解析结果 ZIP 已成为版本审计产物时保留；只有 DB 从未记录它时才清理孤儿对象。
            if output_key and not output_persisted:
                await self._safe_delete_output(output_key)
            await self._mark_failed(job_id, "DOCUMENT_PARSE_FAILED", str(exc))
        except Exception:
            logger.exception("项目文档解析失败 job_id=%s", job_id)
            await self._session.rollback()
            if output_key and not output_persisted:
                await self._safe_delete_output(output_key)
            await self._mark_failed(job_id, "DOCUMENT_PARSE_FAILED", "文档解析失败，请稍后重试")

    async def _mark_running(self, job, version: DocumentVersion) -> None:
        now = datetime.now(UTC)
        job.status = "RUNNING"
        job.attempt += 1
        job.started_at = now
        job.progress_stage, job.progress_percent = "PARSING", 10
        job.progress_message, job.updated_at = "正在调用文档解析服务", now
        job.error_code = None
        job.error_message = None
        transition(version, "PARSING")
        version.error_code = None
        version.error_message = None
        version.completed_at = None
        await self._session.commit()

    async def _parse_source(
        self, storage: MinioObjectStorage, version: DocumentVersion
    ) -> tuple[ParseResult, str]:
        parser = MinerUClient(self._settings)
        with tempfile.TemporaryDirectory(prefix="bid-wise-parse-") as directory:
            source_path = Path(directory) / Path(version.original_file_name).name
            await asyncio.to_thread(storage.download_to_path, version.object_key, source_path)
            result = await asyncio.to_thread(parser.parse, source_path)
            output_key = f"parse-output/{version.id}/{uuid4()}.zip"
            await asyncio.to_thread(
                storage.put_bytes, output_key, result.raw_output, "application/zip"
            )
        return result, output_key

    async def _stage_parsed_version(
        self,
        job,
        version: DocumentVersion,
        document: ProjectDocument,
        result: ParseResult,
        output_key: str,
    ) -> list[Evidence]:
        # FAILED 重试可能留下 staging 节点/Evidence；只清本版本，不碰当前 READY 版本。
        stale_ids = await self._evidences.list_ids_by_document_version(version.id)
        if stale_ids:
            await FindingInvalidationService(self._session).mark_stale_for_evidences(stale_ids)
        await self._documents.delete_nodes(version.id)

        nodes = self._build_nodes(version, result)
        self._documents.add_nodes(nodes)
        await self._session.flush()

        transition(version, "CLEANING")
        now = datetime.now(UTC)
        job.progress_stage, job.progress_percent = "CLEANING", 45
        job.progress_message, job.updated_at = "正在清洗目录和版面噪音", now
        version.parse_output_key = output_key
        await DocumentCleaningService(self._session).clean(version.id)

        transition(version, "BUILDING_EVIDENCE")
        now = datetime.now(UTC)
        job.progress_stage, job.progress_percent = "BUILDING_EVIDENCE", 65
        job.progress_message, job.updated_at = "正在构建可引用 Evidence", now
        chunks = build_evidence_chunks(nodes)
        if not chunks:
            raise ParserUnavailable("未提取到可索引的正文内容，请检查文件质量")
        evidences = [
            Evidence(
                id=uuid4(),
                project_id=document.project_id,
                source_type="DOCUMENT_NODE",
                document_version_id=version.id,
                document_node_id=chunk.anchor_node_id,
                quoted_text=chunk.text,
                content_hash=hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                locator={
                    "section_path": chunk.section_path,
                    "document_name": document.logical_name,
                    "order_start": chunk.order_start,
                    "order_end": chunk.order_end,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "source_node_ids": [str(node_id) for node_id in chunk.source_node_ids],
                    "node_types": list(chunk.node_types),
                    "clause_keys": list(chunk.clause_keys),
                    "parent_clause_keys": list(chunk.parent_clause_keys),
                    "retrieval_text": retrieval_text(chunk.text, chunk.node_types),
                },
                created_at=datetime.now(UTC),
                created_by=version.created_by,
            )
            for chunk in chunks
        ]
        self._evidences.add_many(evidences)
        await self._session.flush()
        self._session.add_all(
            EvidenceSourceNode(
                evidence_id=evidence.id,
                document_node_id=node_id,
                ordinal=ordinal,
            )
            for evidence, chunk in zip(evidences, chunks, strict=True)
            for ordinal, node_id in enumerate(chunk.source_node_ids, start=1)
        )

        transition(version, "INDEXING")
        now = datetime.now(UTC)
        job.progress_stage, job.progress_percent = "INDEXING", 80
        job.progress_message, job.updated_at = "正在生成检索索引", now
        await self._session.commit()
        return evidences

    async def _activate_version(
        self,
        job,
        version: DocumentVersion,
        document: ProjectDocument,
        evidences: list[Evidence],
        vectors: list[list[float]],
    ) -> None:
        now = datetime.now(UTC)
        self._session.add_all(
            EvidenceEmbedding(evidence_id=item.id, embedding=vector, indexed_at=now)
            for item, vector in zip(evidences, vectors, strict=True)
        )
        await TenderClauseService(self._session).rebuild(version.id)

        # 只有索引和条款全部成功才切换 current_version_id。旧版本保留用于人工审核
        # 与 Evidence 血缘审计，但检索层只允许 READY 的 current_version 进入 RAG。
        old_version_ids = [
            item.id
            for item in await self._documents.list_versions(document.id)
            if item.id != version.id
        ]
        old_evidence_ids: list[UUID] = []
        for old_version_id in old_version_ids:
            old_evidence_ids.extend(
                await self._evidences.list_ids_by_document_version(old_version_id)
            )
        if old_evidence_ids:
            await FindingInvalidationService(self._session).mark_stale_for_evidences(
                old_evidence_ids
            )

        transition(version, "READY")
        version.completed_at = now
        document.current_version_id = version.id
        # RAG 从此只读新版本；依赖旧需求的分析结果必须同时退出当前业务视图。
        await AnalysisInvalidationService(self._session).invalidate_inputs({document.project_id})
        job.status = "SUCCEEDED"
        job.progress_stage, job.progress_percent = "READY", 100
        job.progress_message, job.updated_at = "解析完成", now
        job.completed_at = now
        await self._session.commit()

    def _build_nodes(self, version: DocumentVersion, result: ParseResult) -> list[DocumentNode]:
        nodes: list[DocumentNode] = []
        section_ids: dict[str, UUID] = {}
        for order_no, node in enumerate(result.nodes, start=1):
            node_id = uuid4()
            parent_id = section_ids.get(node.section_path or "")
            if node.node_type == "SECTION":
                parent_path = " / ".join((node.section_path or "").split(" / ")[:-1])
                parent_id = section_ids.get(parent_path)
            stored = DocumentNode(
                id=node_id,
                document_version_id=version.id,
                parent_node_id=parent_id,
                node_type=node.node_type,
                page_number=node.page_number,
                section_path=node.section_path,
                order_no=order_no,
                content=node.content,
                content_hash=hashlib.sha256(node.content.encode("utf-8")).hexdigest(),
                bbox=node.bbox,
                metadata_={"parser": "mineru", **node.metadata},
                created_at=datetime.now(UTC),
            )
            nodes.append(stored)
            if node.node_type == "SECTION" and node.section_path:
                section_ids[node.section_path] = node_id
        return nodes

    async def _safe_delete_output(self, output_key: str) -> None:
        try:
            await asyncio.to_thread(MinioObjectStorage(self._settings).delete_object, output_key)
        except Exception:
            logger.warning("清理未持久化解析结果失败 key=%s", output_key, exc_info=True)

    async def _mark_failed(self, job_id: UUID, code: str, message: str) -> None:
        job = await self._documents.get_parse_job_for_update(job_id)
        if job is None:
            return
        version = await self._documents.get_version(job.document_version_id)
        now = datetime.now(UTC)
        job.status = "FAILED"
        job.error_code = code
        job.error_message = message[:1000]
        job.completed_at = now
        job.progress_stage, job.progress_percent = "FAILED", 100
        job.progress_message, job.updated_at = job.error_message, now
        if version is not None and version.parse_status != "FAILED":
            transition(version, "FAILED")
            version.error_code = code
            version.error_message = job.error_message
            version.completed_at = now
        await self._session.commit()
    @staticmethod
    def _embedding_text(document: ProjectDocument, evidence: Evidence) -> str:
        locator = evidence.locator or {}
        section = str(locator.get("section_path") or "").strip()
        prefix = " / ".join(item for item in (document.logical_name.strip(), section) if item)
        body = (evidence.quoted_text or "").strip()
        return f"{prefix}\n{body}" if prefix else body
