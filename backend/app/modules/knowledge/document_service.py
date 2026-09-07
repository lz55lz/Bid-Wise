"""知识源文件的上传、排队与解析编排。

它刻意不调用项目 DocumentService：公共法规库没有 project_id，不能继承项目成员
授权；两边只复用底层的 MinIO、MinerU 和 ARQ 集成客户端。
"""

import asyncio
import hashlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.upload_validation import validate_uploaded_file_content
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.documents.service import StagedUpload
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.knowledge.models import (
    KnowledgeDocumentVersion,
    KnowledgeParseJob,
    KnowledgeVersion,
)
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.state_machine import transition

_ALLOWED_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class KnowledgeDocumentService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session, self._settings = session, settings
        self._repository = KnowledgeRepository(session)

    @staticmethod
    def _require_manager(actor: AuthenticatedUser) -> None:
        if not {"SYSTEM_ADMIN", "LEGAL_COMPLIANCE"}.intersection(actor.role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护知识库", 403)

    async def upload(
        self, entry_id: UUID, actor: AuthenticatedUser, upload: UploadFile
    ) -> dict[str, object]:
        """上传即新建草稿版本，解析成功前绝不替换当前已发布版本。"""
        self._require_manager(actor)
        entry = await self._repository.entry(entry_id, locked=True)
        if entry is None or entry.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
        try:
            storage = MinioObjectStorage(self._settings)
        except ObjectStorageUnavailable as exc:
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        staged = await self._stage(upload)
        now, version_id, document_id = datetime.now(UTC), uuid4(), uuid4()
        baseline = await self._repository.latest_version(entry.id)
        version = KnowledgeVersion(
            id=version_id,
            knowledge_entry_id=entry.id,
            version_no=await self._repository.next_version_no(entry.id),
            status="DRAFT",
            content="",
            title=baseline.title if baseline is not None else entry.title,
            authority=baseline.authority if baseline is not None else entry.authority,
            source_reference=(
                baseline.source_reference if baseline is not None else entry.source_reference
            ),
            issued_on=baseline.issued_on if baseline is not None else entry.issued_on,
            effective_on=(baseline.effective_on if baseline is not None else entry.effective_on),
            citation_note=(baseline.citation_note if baseline is not None else entry.citation_note),
            published_at=None,
            published_by=None,
            created_at=now,
            created_by=actor.id,
        )
        document = KnowledgeDocumentVersion(
            id=document_id,
            knowledge_version_id=version.id,
            original_file_name=staged.original_file_name,
            mime_type=staged.mime_type,
            file_size=staged.file_size,
            sha256=staged.sha256,
            object_key=f"knowledge/{entry.id}/versions/{version.id}/source",
            parse_status="UPLOADED",
            parse_output_key=None,
            error_code=None,
            error_message=None,
            cleaning_summary=None,
            created_at=now,
            created_by=actor.id,
            completed_at=None,
        )
        uploaded = False
        try:
            # 两个模型未声明 ORM relationship，不能依赖 Unit of Work 推断外键写入顺序。
            # 必须先持久化知识版本，文件版本才可安全引用它。
            self._session.add(version)
            await self._session.flush()
            self._session.add(document)
            await self._session.flush()
            await asyncio.to_thread(
                storage.put_file, document.object_key, staged.path, staged.mime_type
            )
            uploaded = True
            await self._session.commit()
        except ObjectStorageUnavailable as exc:
            await self._session.rollback()
            raise DomainError(
                "OBJECT_STORAGE_UNAVAILABLE", "文件上传失败，请稍后重试", 503
            ) from exc
        except Exception:
            await self._session.rollback()
            if uploaded:
                await asyncio.to_thread(storage.delete_object, document.object_key)
            raise
        finally:
            staged.path.unlink(missing_ok=True)
        return await self.request_parse(entry_id, document.id, actor)

    async def request_parse(
        self, entry_id: UUID, document_id: UUID, actor: AuthenticatedUser
    ) -> dict[str, object]:
        self._require_manager(actor)
        entry = await self._repository.entry(entry_id)
        # 同一知识源版本的提交通过行锁串行化，避免并发请求都观察到“无任务”后
        # 插入两个 ParseJob，最后只靠唯一约束抛数据库异常。
        document = await self._repository.document(document_id, locked=True)
        if entry is None or entry.deleted_at is not None or document is None:
            raise DomainError("RESOURCE_NOT_FOUND", "知识源文件不存在", 404)
        version = await self._repository.version(document.knowledge_version_id)
        if version is None or version.knowledge_entry_id != entry.id:
            raise DomainError("RESOURCE_NOT_FOUND", "知识源文件不存在", 404)
        if document.parse_status == "READY":
            raise DomainError(
                "KNOWLEDGE_DOCUMENT_ALREADY_READY",
                "该知识源版本已完成解析；如正文有变化请上传新版本",
                409,
            )
        if document.parse_status not in {"UPLOADED", "FAILED"}:
            raise DomainError("TASK_ALREADY_RUNNING", "知识文件正在处理，不能重复提交解析", 409)
        job = await self._repository.parse_job(document.id)
        if job is not None and job.status in {"QUEUED", "RUNNING"}:
            raise DomainError("TASK_ALREADY_RUNNING", "知识文件正在解析", 409)
        now = datetime.now(UTC)
        if job is None:
            job = KnowledgeParseJob(
                id=uuid4(),
                knowledge_document_version_id=document.id,
                status="QUEUED",
                arq_job_id=None,
                attempt=0,
                progress_stage="QUEUED",
                progress_percent=0,
                progress_message="等待 Worker 处理",
                error_code=None,
                error_message=None,
                created_at=now,
                started_at=None,
                completed_at=None,
                updated_at=now,
            )
            self._session.add(job)
        else:
            job.status, job.arq_job_id, job.error_code, job.error_message = (
                "QUEUED",
                None,
                None,
                None,
            )
            job.started_at, job.completed_at = None, None
            job.progress_stage, job.progress_percent = "QUEUED", 0
            job.progress_message, job.updated_at = "等待 Worker 处理", now
        transition(document, "QUEUED")
        document.error_code, document.error_message, document.completed_at = None, None, None
        await self._session.commit()
        try:
            job.arq_job_id = await ArqTaskQueue(self._settings).publish_knowledge_parse(str(job.id))
            self._session.add(
                AuditLog(
                    actor_id=actor.id,
                    action="SUBMIT_KNOWLEDGE_PARSE",
                    target_type="KNOWLEDGE_DOCUMENT",
                    target_id=document.id,
                    project_id=None,
                    after_summary=None,
                    created_at=now,
                )
            )
            await self._session.commit()
        except TaskQueueUnavailable:
            job.arq_job_id = None
            self._session.add(
                AuditLog(
                    actor_id=actor.id,
                    action="SUBMIT_KNOWLEDGE_PARSE",
                    target_type="KNOWLEDGE_DOCUMENT",
                    target_id=document.id,
                    project_id=None,
                    after_summary="队列暂不可用，等待自动补投",
                    created_at=now,
                )
            )
            await self._session.commit()
        return self._job_response(job, document)

    async def get_job(
        self, entry_id: UUID, job_id: UUID, actor: AuthenticatedUser
    ) -> dict[str, object]:
        self._require_manager(actor)
        job = await self._session.get(KnowledgeParseJob, job_id)
        if job is None:
            raise DomainError("RESOURCE_NOT_FOUND", "解析任务不存在", 404)
        document = await self._repository.document(job.knowledge_document_version_id)
        version = (
            None
            if document is None
            else await self._repository.version(document.knowledge_version_id)
        )
        if document is None or version is None or version.knowledge_entry_id != entry_id:
            raise DomainError("RESOURCE_NOT_FOUND", "解析任务不存在", 404)
        return self._job_response(job, document)

    async def _stage(self, upload: UploadFile) -> StagedUpload:
        file_name = Path(upload.filename or "").name
        suffix = Path(file_name).suffix.lower()
        mime_type = _ALLOWED_FILE_TYPES.get(suffix)
        if not file_name or mime_type is None:
            raise DomainError("UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、XLSX、PPTX 文件", 422)
        size, digest = 0, hashlib.sha256()
        temporary = tempfile.NamedTemporaryFile(prefix="bid-wise-knowledge-", delete=False)
        path = Path(temporary.name)
        try:
            with temporary:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > self._settings.max_upload_bytes:
                        raise DomainError("FILE_TOO_LARGE", "上传文件超过大小限制", 413)
                    digest.update(chunk)
                    temporary.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        if size == 0:
            path.unlink(missing_ok=True)
            raise DomainError("EMPTY_FILE", "不能上传空文件", 422)
        try:
            validate_uploaded_file_content(path, suffix)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StagedUpload(path, file_name, mime_type, size, digest.hexdigest())

    @staticmethod
    def _job_response(
        job: KnowledgeParseJob, document: KnowledgeDocumentVersion
    ) -> dict[str, object]:
        return {
            "id": str(job.id),
            "document_version_id": str(document.id),
            "knowledge_version_id": str(document.knowledge_version_id),
            "status": job.status,
            "parse_status": document.parse_status,
            "attempt": job.attempt,
            "progress_stage": job.progress_stage,
            "progress_percent": job.progress_percent,
            "progress_message": job.progress_message,
            "updated_at": job.updated_at.isoformat(),
            "error_code": job.error_code,
            "error_message": job.error_message,
        }
