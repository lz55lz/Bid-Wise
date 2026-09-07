"""项目文件上传与查询服务。"""

import asyncio
import hashlib
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.upload_validation import validate_uploaded_file_content
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.documents.models import DocumentParseJob, DocumentVersion, ProjectDocument
from app.modules.documents.repository import DocumentRepository
from app.modules.projects.models import TenderProject
from app.modules.documents.schemas import (
    DocumentNodePage,
    DocumentNodeResponse,
    DocumentParseJobResponse,
    DocumentVersionResponse,
    ProjectDocumentResponse,
)
from app.modules.documents.state_machine import transition
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService

_ALLOWED_FILE_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass(frozen=True, slots=True)
class StagedUpload:
    """已校验并落入临时目录的上传文件，服务结束时必须删除。"""

    path: Path
    original_file_name: str
    mime_type: str
    file_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AuthorizedDocumentDownload:
    """经数据库项目授权后才可创建的下载流，不包含对象键。"""

    file_name: str
    mime_type: str
    stream: Iterator[bytes]


class DocumentService:
    """项目文档用例；对象存储只处理字节，项目服务决定资源授权。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._documents = DocumentRepository(session)

    async def upload(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        logical_name: str,
        upload: UploadFile,
    ) -> ProjectDocumentResponse:
        """上传项目文件并创建不可变的第一个版本。"""
        await ProjectService(self._session).require_document_write(project_id, actor)
        if not logical_name.strip():
            raise DomainError("VALIDATION_ERROR", "文档名称不能为空", 422)
        try:
            storage = MinioObjectStorage(self._settings)
        except ObjectStorageUnavailable as exc:
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        staged = await self._stage_upload(upload)
        document_id = uuid4()
        version_id = uuid4()
        object_key = f"projects/{project_id}/documents/{document_id}/versions/{version_id}/source"
        now = datetime.now(UTC)
        document = ProjectDocument(
            id=document_id,
            project_id=project_id,
            logical_name=logical_name.strip(),
            # ``project_documents`` 与 ``document_versions`` 互相引用。首次上传时
            # 不能在同一条 INSERT 里提前写入 current_version_id，否则 PostgreSQL
            # 会先检查到一个尚未落库的版本外键。先落库“逻辑文档 + 首个版本”，
            # 再回填当前版本，既满足约束也保留 current_version 的业务语义。
            current_version_id=None,
            created_at=now,
            created_by=actor.id,
        )
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_no=1,
            original_file_name=staged.original_file_name,
            file_size=staged.file_size,
            mime_type=staged.mime_type,
            sha256=staged.sha256,
            object_key=object_key,
            parse_status="UPLOADED",
            created_at=now,
            created_by=actor.id,
        )
        object_uploaded = False
        try:
            self._documents.add_document(document)
            # 先写入父记录，避免没有 relationship 的两个实体在 flush 时被按
            # 不确定顺序 INSERT，导致首个版本找不到其 document_id 外键目标。
            await self._session.flush()
            self._documents.add_version(version)
            await self._session.flush()
            document.current_version_id = version.id
            await self._session.flush()
            await asyncio.to_thread(storage.put_file, object_key, staged.path, staged.mime_type)
            object_uploaded = True
            # 项目在首次成功持久化文件后进入进行中状态；不能继续显示为“草稿/待上传”。
            project = await self._session.get(TenderProject, project_id)
            if project is not None and project.status == "DRAFT":
                project.status = "ACTIVE"
            await self._session.commit()
        except ObjectStorageUnavailable as exc:
            await self._session.rollback()
            raise DomainError(
                "OBJECT_STORAGE_UNAVAILABLE",
                "文件上传失败，请稍后重试",
                503,
            ) from exc
        except Exception:
            await self._session.rollback()
            if object_uploaded:
                await asyncio.to_thread(storage.delete_object, object_key)
            raise
        finally:
            staged.path.unlink(missing_ok=True)
        return self._to_response(document, version)

    async def request_parse(
        self,
        project_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUser,
        version_id: UUID | None = None,
    ) -> DocumentParseJobResponse:
        """创建或重置解析任务，提交后才向 ARQ 发布唯一任务 ID。"""
        await ProjectService(self._session).require_document_write(project_id, actor)
        # 锁逻辑文档直到任务状态提交，串行化同一文档多个版本的解析提交。
        document = await self._documents.get_active(document_id, for_update=True)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在", 404)
        version = (
            await self._documents.get_current_version(document)
            if version_id is None
            else await self._documents.get_version_for_document(document.id, version_id)
        )
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档版本不存在", 404)
        if version.parse_status == "READY":
            raise DomainError(
                "DOCUMENT_ALREADY_READY",
                "该版本已完成解析；如源文件有变化请上传新版本，索引异常请使用重建索引功能",
                409,
            )
        if version.parse_status not in {"UPLOADED", "FAILED"}:
            raise DomainError("TASK_ALREADY_RUNNING", "文档版本正在处理，不能重复提交解析", 409)
        job = await self._documents.get_parse_job(version.id)
        if job is not None and job.status in {"QUEUED", "RUNNING"}:
            raise DomainError("TASK_ALREADY_RUNNING", "文档正在解析", 409)
        if await self._documents.has_active_parse_for_document(document.id):
            raise DomainError("TASK_ALREADY_RUNNING", "该文档的其他版本正在解析", 409)

        now = datetime.now(UTC)
        if job is None:
            job = DocumentParseJob(
                document_version_id=version.id,
                status="QUEUED",
                attempt=0,
                progress_stage="QUEUED",
                progress_percent=0,
                progress_message="等待 Worker 处理",
                created_at=now,
                created_by=actor.id,
                updated_at=now,
            )
            self._documents.add_parse_job(job)
        else:
            job.status = "QUEUED"
            job.arq_job_id = None
            job.error_code = None
            job.error_message = None
            job.started_at = None
            job.completed_at = None
            job.progress_stage, job.progress_percent = "QUEUED", 0
            job.progress_message, job.updated_at = "等待 Worker 处理", now
        transition(version, "QUEUED")
        version.error_code = None
        version.error_message = None
        await self._session.commit()

        try:
            job.arq_job_id = await ArqTaskQueue(self._settings).publish_document_parse(str(job.id))
            await self._session.commit()
        except TaskQueueUnavailable:
            # Redis 只是执行信号；任务事实已经持久化为 QUEUED，由 reconciler 补投。
            job.arq_job_id = None
            await self._session.commit()
        return self._to_parse_job_response(job)

    async def list_project_documents(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
    ) -> list[ProjectDocumentResponse]:
        """先验证项目归属，再读取项目内未删除文档。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        documents = await self._documents.list_active_by_project(project_id)
        responses: list[ProjectDocumentResponse] = []
        for document in documents:
            version = await self._documents.get_current_version(document)
            if version is not None:
                job = await self._documents.get_parse_job(version.id)
                responses.append(self._to_response(document, version, job))
        return responses

    async def get_project_document(
        self, project_id: UUID, document_id: UUID, actor: AuthenticatedUser
    ) -> ProjectDocumentResponse:
        """返回单个逻辑文档及当前版本，详情页无需依赖列表接口的偶然结果。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        version = await self._documents.get_current_version(document)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档当前版本不存在", 404)
        return self._to_response(document, version, await self._documents.get_parse_job(version.id))

    async def get_parse_job(
        self,
        project_id: UUID,
        document_id: UUID,
        job_id: UUID,
        actor: AuthenticatedUser,
    ) -> DocumentParseJobResponse:
        """按项目、逻辑文档和版本三重归属查询任务，UUID 不构成授权。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        job = await self._documents.get_parse_job_by_id(job_id)
        if job is None:
            raise DomainError("PARSE_JOB_NOT_FOUND", "解析任务不存在或无权访问", 404)
        version = await self._documents.get_version_for_document(
            document.id, job.document_version_id
        )
        if version is None:
            raise DomainError("PARSE_JOB_NOT_FOUND", "解析任务不存在或无权访问", 404)
        return self._to_parse_job_response(job)

    async def list_nodes(
        self,
        project_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUser,
        offset: int,
        limit: int,
        version_no: int | None,
    ) -> DocumentNodePage:
        """分页读取指定或当前版本的解析节点，文档 ID 不能替代项目授权。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        if version_no is None:
            version = await self._documents.get_current_version(document)
        else:
            version = await self._documents.get_version_by_number(document.id, version_no)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档版本不存在", 404)
        nodes, total = await self._documents.list_nodes(version.id, offset, limit)
        return DocumentNodePage(
            items=[
                DocumentNodeResponse(
                    id=node.id,
                    node_type=node.node_type,
                    page_number=node.page_number,
                    section_path=node.section_path,
                    order_no=node.order_no,
                    content=node.content,
                    metadata=node.metadata_,
                )
                for node in nodes
            ],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def upload_new_version(
        self,
        project_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUser,
        upload: UploadFile,
    ) -> DocumentVersionResponse:
        """上传逻辑文档的新版本；解析成功前不替换当前有效版本。"""
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        await ProjectService(self._session).require_document_write(project_id, actor)
        try:
            storage = MinioObjectStorage(self._settings)
        except ObjectStorageUnavailable as exc:
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        staged = await self._stage_upload(upload)
        versions = await self._documents.list_versions(document.id)
        version_no = max((item.version_no for item in versions), default=0) + 1
        version_id = uuid4()
        object_key = f"projects/{project_id}/documents/{document.id}/versions/{version_id}/source"
        version = DocumentVersion(
            id=version_id,
            document_id=document.id,
            version_no=version_no,
            original_file_name=staged.original_file_name,
            file_size=staged.file_size,
            mime_type=staged.mime_type,
            sha256=staged.sha256,
            object_key=object_key,
            parse_status="UPLOADED",
            created_at=datetime.now(UTC),
            created_by=actor.id,
        )
        object_uploaded = False
        try:
            self._documents.add_version(version)
            await self._session.flush()
            await asyncio.to_thread(storage.put_file, object_key, staged.path, staged.mime_type)
            object_uploaded = True
            await self._session.commit()
        except ObjectStorageUnavailable as exc:
            await self._session.rollback()
            raise DomainError(
                "OBJECT_STORAGE_UNAVAILABLE", "文件上传失败，请稍后重试", 503
            ) from exc
        except IntegrityError as exc:
            # version_no 由数据库唯一约束最终兜底。并发上传同一逻辑文档时，
            # 让调用方得到可重试的业务冲突，而不是暴露数据库 500。
            await self._session.rollback()
            if object_uploaded:
                await asyncio.to_thread(storage.delete_object, object_key)
            raise DomainError(
                "DOCUMENT_VERSION_CONFLICT",
                "文档版本被并发更新，请刷新后重试",
                409,
            ) from exc
        except Exception:
            await self._session.rollback()
            if object_uploaded:
                await asyncio.to_thread(storage.delete_object, object_key)
            raise
        finally:
            staged.path.unlink(missing_ok=True)
        return self._to_version_response(version)

    async def list_document_versions(
        self, project_id: UUID, document_id: UUID, actor: AuthenticatedUser
    ) -> list[DocumentVersionResponse]:
        """项目成员可查看同一逻辑文档的版本元数据，不返回对象键。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        return [
            self._to_version_response(version)
            for version in await self._documents.list_versions(document.id)
        ]

    async def create_authorized_download(
        self,
        project_id: UUID,
        document_id: UUID,
        actor: AuthenticatedUser,
    ) -> AuthorizedDocumentDownload:
        """回查项目、逻辑文档和当前版本后才打开对象流。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        document = await self._documents.get_active(document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档不存在或无权访问", 404)
        version = await self._documents.get_current_version(document)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档版本不存在", 404)
        try:
            storage = MinioObjectStorage(self._settings)
            stream = storage.stream_object(version.object_key)
        except ObjectStorageUnavailable as exc:
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "文件存储暂不可用", 503) from exc
        return AuthorizedDocumentDownload(
            file_name=version.original_file_name,
            mime_type=version.mime_type,
            stream=stream,
        )

    async def _stage_upload(self, upload: UploadFile) -> StagedUpload:
        """流式读取上传内容，限制大小并在本地计算 SHA-256。"""
        file_name = Path(upload.filename or "").name
        suffix = Path(file_name).suffix.lower()
        mime_type = _ALLOWED_FILE_TYPES.get(suffix)
        if not file_name or mime_type is None:
            raise DomainError("UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、XLSX、PPTX 文件", 422)

        file_size = 0
        digest = hashlib.sha256()
        temporary = tempfile.NamedTemporaryFile(prefix="bid-wise-upload-", delete=False)
        temporary_path = Path(temporary.name)
        try:
            with temporary:
                while chunk := await upload.read(1024 * 1024):
                    file_size += len(chunk)
                    if file_size > self._settings.max_upload_bytes:
                        raise DomainError("FILE_TOO_LARGE", "上传文件超过大小限制", 413)
                    digest.update(chunk)
                    temporary.write(chunk)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        if file_size == 0:
            temporary_path.unlink(missing_ok=True)
            raise DomainError("EMPTY_FILE", "不能上传空文件", 422)
        try:
            validate_uploaded_file_content(temporary_path, suffix)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return StagedUpload(
            path=temporary_path,
            original_file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
            sha256=digest.hexdigest(),
        )

    @staticmethod
    def _to_response(
        document: ProjectDocument,
        version: DocumentVersion,
        job: DocumentParseJob | None = None,
    ) -> ProjectDocumentResponse:
        return ProjectDocumentResponse(
            id=document.id,
            project_id=document.project_id,
            logical_name=document.logical_name,
            current_version=DocumentService._to_version_response(version, job),
            created_at=document.created_at,
        )

    @staticmethod
    def _to_version_response(
        version: DocumentVersion, job: DocumentParseJob | None = None
    ) -> DocumentVersionResponse:
        return DocumentVersionResponse(
            id=version.id,
            version_no=version.version_no,
            original_file_name=version.original_file_name,
            file_size=version.file_size,
            mime_type=version.mime_type,
            sha256=version.sha256,
            parse_status=version.parse_status,
            progress_percent=None if job is None else job.progress_percent,
            progress_message=None if job is None else job.progress_message,
            error_code=version.error_code,
            error_message=version.error_message,
            created_at=version.created_at,
            completed_at=version.completed_at,
        )

    @staticmethod
    def _to_parse_job_response(job: DocumentParseJob) -> DocumentParseJobResponse:
        return DocumentParseJobResponse(
            id=job.id,
            document_version_id=job.document_version_id,
            status=job.status,
            attempt=job.attempt,
            progress_stage=job.progress_stage,
            progress_percent=job.progress_percent,
            progress_message=job.progress_message,
            error_code=job.error_code,
            error_message=job.error_message,
            created_at=job.created_at,
            completed_at=job.completed_at,
            updated_at=job.updated_at,
        )
