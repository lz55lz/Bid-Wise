import hashlib
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import (
    can_manage_enterprise_materials,
    can_manage_knowledge,
    can_write_project_documents,
)
from app.db.models import (
    AnalysisRun,
    Document,
    DocumentNode,
    DocumentVersion,
    KnowledgeEntry,
    KnowledgeVersion,
    Report,
    Task,
)
from app.db.repositories.clause_repository import ClauseRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.knowledge_repository import KnowledgeRepository
from app.db.repositories.task_repository import TaskRepository
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.integrations.task_publisher import TaskPublisher
from app.schemas.documents import (
    DocumentNodePage,
    DocumentNodeResponse,
    DocumentResponse,
    DocumentTaskResponse,
    DocumentVersionResponse,
    TaskResponse,
    TenderClauseResponse,
)
from app.schemas.knowledge import KnowledgeDocumentTaskResponse, KnowledgeResponse
from app.services.audit_service import AuditService
from app.services.project_service import ProjectService
from app.services.task_service import TaskService

_ALLOWED_FILES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    file_name: str
    logical_name: str
    mime_type: str
    file_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AuthorizedDownload:
    file_name: str
    mime_type: str
    stream: Iterator[bytes]


class DocumentService:
    def __init__(
        self,
        session: Session,
        object_storage: MinioObjectStorage,
        task_publisher: TaskPublisher,
    ) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._knowledge = KnowledgeRepository(session)
        self._tasks = TaskRepository(session)
        self._projects = ProjectService(session)
        self._storage = object_storage
        self._publisher = task_publisher
        self._audit = AuditService(session)

    def upload_tender_document(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        document_type: str,
        upload: UploadFile,
        max_upload_bytes: int,
    ) -> DocumentTaskResponse:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        if not can_write_project_documents(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权上传项目文件", 403)
        if document_type != "TENDER":
            raise DomainError("DOCUMENT_TYPE_INVALID", "项目文件只能上传招标文件", 422)

        staged_upload = self._stage_upload(upload, max_upload_bytes)
        try:
            return self._persist_tender_upload(project_id, actor_id, staged_upload)
        finally:
            staged_upload.path.unlink(missing_ok=True)

    def upload_enterprise_material_document(
        self,
        actor_id: UUID,
        role_codes: set[str],
        upload: UploadFile,
        max_upload_bytes: int,
    ) -> DocumentTaskResponse:
        if not can_manage_enterprise_materials(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权上传企业材料证明文件", 403)
        staged_upload = self._stage_upload(upload, max_upload_bytes)
        try:
            return self._persist_enterprise_upload(actor_id, staged_upload)
        finally:
            staged_upload.path.unlink(missing_ok=True)

    def upload_knowledge_document(
        self,
        actor_id: UUID,
        role_codes: set[str],
        knowledge_type: str,
        title: str,
        authority: str | None,
        source_reference: str,
        issued_on: date | None,
        effective_on: date | None,
        citation_note: str | None,
        upload: UploadFile,
        max_upload_bytes: int,
    ) -> KnowledgeDocumentTaskResponse:
        if not can_manage_knowledge(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权上传法规/案例源文件", 403)
        if knowledge_type not in {"LEGAL", "CASE"}:
            raise DomainError("DOCUMENT_TYPE_INVALID", "知识文件仅支持法规或案例", 422)
        if not title.strip() or not source_reference.strip():
            raise DomainError("REQUEST_INVALID", "标题和来源引用不能为空", 422)
        staged_upload = self._stage_upload(upload, max_upload_bytes)
        try:
            return self._persist_knowledge_upload(
                actor_id,
                staged_upload,
                knowledge_type=knowledge_type,
                title=title.strip(),
                authority=authority.strip() if authority and authority.strip() else None,
                source_reference=source_reference.strip(),
                issued_on=issued_on,
                effective_on=effective_on,
                citation_note=(
                    citation_note.strip() if citation_note and citation_note.strip() else None
                ),
                entry_id=None,
            )
        finally:
            staged_upload.path.unlink(missing_ok=True)

    def upload_knowledge_document_revision(
        self,
        entry_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        issued_on: date | None,
        effective_on: date | None,
        citation_note: str | None,
        upload: UploadFile,
        max_upload_bytes: int,
    ) -> KnowledgeDocumentTaskResponse:
        if not can_manage_knowledge(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权上传法规/案例源文件", 403)
        staged_upload = self._stage_upload(upload, max_upload_bytes)
        try:
            return self._persist_knowledge_upload(
                actor_id,
                staged_upload,
                knowledge_type=None,
                title=None,
                authority=None,
                source_reference=None,
                issued_on=issued_on,
                effective_on=effective_on,
                citation_note=(
                    citation_note.strip() if citation_note and citation_note.strip() else None
                ),
                entry_id=entry_id,
            )
        finally:
            staged_upload.path.unlink(missing_ok=True)

    def get_document(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> DocumentResponse:
        document = self._get_visible_document(document_id, actor_id, role_codes)
        return self._document_response(document)

    def list_versions(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[DocumentVersionResponse]:
        self._get_visible_document(document_id, actor_id, role_codes)
        versions = self._documents.list_versions(document_id)
        return [self._version_response(v) for v in versions]

    def list_project_documents(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[DocumentResponse]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        return [
            self._document_response(document)
            for document in self._documents.list_by_project(project_id)
        ]

    def list_nodes(
        self,
        document_id: UUID,
        version_no: int | None,
        offset: int,
        limit: int,
        actor_id: UUID,
        role_codes: set[str],
    ) -> DocumentNodePage:
        document = self._get_visible_document(document_id, actor_id, role_codes)
        version = self._resolve_version(document, version_no)
        nodes = self._documents.list_nodes(version.id, offset, limit)
        return DocumentNodePage(
            document_id=document.id,
            document_version_id=version.id,
            items=[self._node_response(node) for node in nodes],
            offset=offset,
            limit=limit,
        )

    def list_clauses(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[TenderClauseResponse]:
        document = self._get_visible_document(document_id, actor_id, role_codes)
        version = self._resolve_version(document, None)
        repository = ClauseRepository(self._session)
        clauses = repository.list_for_version(version.id)
        evidence = repository.primary_evidence_ids([clause.id for clause in clauses])
        return [
            TenderClauseResponse(
                id=clause.id,
                order_no=clause.order_no,
                clause_type=clause.clause_type,
                section_path=clause.section_path,
                start_page=clause.start_page,
                end_page=clause.end_page,
                content=clause.content,
                mandatory_signal=clause.mandatory_signal,
                evidence_ids=[evidence[clause.id]] if clause.id in evidence else [],
            )
            for clause in clauses
        ]

    def get_task(self, task_id: UUID, actor_id: UUID, role_codes: set[str]) -> TaskResponse:
        task = self._tasks.get(task_id)
        if task is None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        if task.target_type == "DOCUMENT_VERSION":
            version = self._documents.get_version(task.target_id)
            if version is None:
                raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
            self._get_visible_document(version.document_id, actor_id, role_codes)
        elif task.target_type == "PROJECT":
            self._projects.get_visible(task.target_id, actor_id, role_codes)
        elif task.target_type == "REPORT":
            report = self._session.get(Report, task.target_id)
            if report is None:
                raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
            self._projects.get_visible(report.project_id, actor_id, role_codes)
        elif task.target_type == "ANALYSIS_RUN":
            analysis_run = self._session.get(AnalysisRun, task.target_id)
            if analysis_run is None:
                raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
            self._projects.get_visible(analysis_run.project_id, actor_id, role_codes)
        else:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return self._task_response(task)

    def retry_document(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> DocumentTaskResponse:
        document = self._get_visible_document(document_id, actor_id, role_codes)
        if document.document_type == "TENDER":
            self._projects.require_writable(
                self._projects.get_visible(document.project_id, actor_id, role_codes)  # type: ignore[arg-type]
            )
            if not can_write_project_documents(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重新执行解析任务", 403)
        elif document.document_type == "ENTERPRISE":
            if not can_manage_enterprise_materials(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重新执行解析任务", 403)
        elif document.document_type in {"LEGAL", "CASE"}:
            if not can_manage_knowledge(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重新执行解析任务", 403)
        else:
            raise DomainError("PERMISSION_DENIED", "无权重新执行解析任务", 403)
        version = self._resolve_version(document, None)
        if version.parse_status != "FAILED":
            raise DomainError("TASK_RETRY_NOT_ALLOWED", "仅失败文档可以重新执行", 409)

        previous_task = self._tasks.latest_failed_document_task(version.id)
        if previous_task is None:
            raise DomainError(
                "TASK_RETRY_NOT_ALLOWED", "No failed document stage is available to retry", 409
            )
        version.parse_status = "QUEUED"
        version.error_code = None
        version.error_message = None
        version.completed_at = None
        # 重试一律换新 thread 全量重跑：复用旧 thread 会回放上次"成功"阶段，
        # 若重新解析产出不同内容，clean/annotate/tagging 将回放陈旧结果
        version.pipeline_thread_id = f"bid-{version.id}-{uuid4().hex[:6]}"
        # 统一走 bid_pipeline 重试，不走单独 stage task
        task_service = TaskService(self._session)
        task = task_service.create_pipeline_task(version, document.project_id, actor_id)
        self._audit.record(
            actor_id=actor_id,
            action="RETRY_DOCUMENT",
            target_type="DOCUMENT_VERSION",
            target_id=version.id,
            project_id=document.project_id,
        )
        self._session.commit()

        # 重新派发到 ARQ
        if document.document_type in {"LEGAL", "CASE"}:
            self._enqueue_retry_knowledge(document, version, actor_id)
        else:
            self._enqueue_pipeline(
                document_version_id=str(version.id),
                project_id=str(document.project_id) if document.project_id else None,
                enterprise_name=(
                    document.logical_name if document.document_type == "ENTERPRISE" else ""
                ),
                thread_id=version.pipeline_thread_id,
            )
        return DocumentTaskResponse(
            document_id=document.id,
            document_version_id=version.id,
            version_no=version.version_no,
            task=self._task_response(task),
        )

    def reprocess_document(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> DocumentTaskResponse:
        """Rebuild a completed document with the current parser/chunking rules."""
        document = self._get_visible_document(document_id, actor_id, role_codes)
        if document.document_type == "TENDER":
            self._projects.require_writable(
                self._projects.get_visible(document.project_id, actor_id, role_codes)  # type: ignore[arg-type]
            )
            if not can_write_project_documents(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重建文档索引", 403)
        elif document.document_type == "ENTERPRISE":
            if not can_manage_enterprise_materials(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重建文档索引", 403)
        elif document.document_type in {"LEGAL", "CASE"}:
            if not can_manage_knowledge(role_codes):
                raise DomainError("PERMISSION_DENIED", "无权重建文档索引", 403)
        else:
            raise DomainError("PERMISSION_DENIED", "无权重建文档索引", 403)

        version = self._resolve_version(document, None)
        version.parse_status = "QUEUED"
        version.error_code = None
        version.error_message = None
        version.completed_at = None
        version.pipeline_thread_id = f"bid-{version.id}-{uuid4().hex[:6]}"
        if document.document_type in {"LEGAL", "CASE"}:
            knowledge_version = self._session.query(KnowledgeVersion).filter(
                KnowledgeVersion.source_document_version_id == version.id
            ).order_by(KnowledgeVersion.version_no.desc()).first()
            if knowledge_version is None:
                raise DomainError("RESOURCE_NOT_FOUND", "知识版本不存在，无法重建", 404)
            # knowledge_pipeline intentionally accepts only a draft: clearing
            # its old derived index happens inside the worker before publish.
            knowledge_version.status = "DRAFT"
        task_service = TaskService(self._session)
        task = task_service.create_pipeline_task(version, document.project_id, actor_id)
        self._audit.record(
            actor_id=actor_id,
            action="REPROCESS_DOCUMENT",
            target_type="DOCUMENT_VERSION",
            target_id=version.id,
            project_id=document.project_id,
        )
        self._session.commit()

        if document.document_type in {"LEGAL", "CASE"}:
            self._enqueue_retry_knowledge(document, version, actor_id)
        else:
            self._enqueue_pipeline(
                document_version_id=str(version.id),
                project_id=str(document.project_id) if document.project_id else None,
                enterprise_name=(
                    document.logical_name if document.document_type == "ENTERPRISE" else ""
                ),
                thread_id=version.pipeline_thread_id,
            )
        return DocumentTaskResponse(
            document_id=document.id,
            document_version_id=version.id,
            version_no=version.version_no,
            task=self._task_response(task),
        )

    def create_authorized_download(
        self, document_id: UUID, version_no: int | None, actor_id: UUID, role_codes: set[str]
    ) -> AuthorizedDownload:
        document = self._get_visible_document(document_id, actor_id, role_codes)
        version = self._resolve_version(document, version_no)
        self._audit.record(
            actor_id=actor_id,
            action="DOWNLOAD_DOCUMENT",
            target_type="DOCUMENT_VERSION",
            target_id=version.id,
            project_id=document.project_id,
        )
        self._session.commit()

        def iterator() -> Iterator[bytes]:
            with self._storage.open_object(version.object_key) as source:
                yield from source.stream(amt=1024 * 1024)

        return AuthorizedDownload(version.file_name, version.mime_type, iterator())

    def _stage_upload(self, upload: UploadFile, max_upload_bytes: int) -> StagedUpload:
        if max_upload_bytes <= 0:
            raise DomainError("SERVICE_UNAVAILABLE", "上传大小限制配置无效", 503)
        file_name = self._safe_file_name(upload.filename)
        extension = Path(file_name).suffix.lower()
        expected_mime = _ALLOWED_FILES.get(extension)
        if expected_mime is None:
            raise DomainError("FILE_SECURITY_REJECTED", "不支持的文件类型", 422)
        # WeKnora 做法：不要完全依赖浏览器发送的 content_type，用扩展名判断即可
        # 实际文件类型通过 _sniff_mime 检测内容来确定

        hasher = hashlib.sha256()
        file_size = 0
        descriptor, temporary_name = tempfile.mkstemp(prefix="ai-bid-upload-", suffix=extension)
        path = Path(temporary_name)
        try:
            with open(descriptor, "wb", closefd=True) as destination:
                while chunk := upload.file.read(1024 * 1024):
                    file_size += len(chunk)
                    if file_size > max_upload_bytes:
                        raise DomainError("FILE_SECURITY_REJECTED", "文件超过允许大小", 422)
                    hasher.update(chunk)
                    destination.write(chunk)
            if file_size == 0:
                raise DomainError("FILE_SECURITY_REJECTED", "不允许上传空文件", 422)
            detected_mime = self._sniff_mime(path, extension)
            if detected_mime != expected_mime:
                raise DomainError("FILE_SECURITY_REJECTED", "文件内容与扩展名不匹配", 422)
            return StagedUpload(
                path=path,
                file_name=file_name,
                logical_name=file_name,
                mime_type=expected_mime,
                file_size=file_size,
                sha256=hasher.hexdigest(),
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            upload.file.close()

    def _persist_tender_upload(
        self, project_id: UUID, actor_id: UUID, staged_upload: StagedUpload
    ) -> DocumentTaskResponse:
        object_key: str | None = None
        try:
            document = self._documents.get_by_project_logical_name_for_update(
                project_id, "TENDER", staged_upload.logical_name
            )
            if document is None:
                document = Document(
                    id=uuid4(),
                    project_id=project_id,
                    document_type="TENDER",
                    logical_name=staged_upload.logical_name,
                    created_at=self._now(),
                    created_by=actor_id,
                )
                self._documents.add_document(document)
                self._session.flush()

            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_no=self._documents.next_version_no(document.id),
                file_name=staged_upload.file_name,
                file_size=staged_upload.file_size,
                mime_type=staged_upload.mime_type,
                object_key="",
                sha256=staged_upload.sha256,
                parse_status="QUEUED",
                created_at=self._now(),
                created_by=actor_id,
            )
            version.object_key = f"documents/{document.id}/{version.id}/source"
            object_key = version.object_key
            self._storage.put_file(object_key, staged_upload.path, staged_upload.mime_type)
            self._documents.add_version(version)
            # The current-version FK is immediate. Persist the immutable version
            # before updating the logical document that references it.
            self._session.flush()
            document.current_version_id = version.id
            version.pipeline_thread_id = f"bid-{version.id}"
            # LangGraph pipeline task 包含 parse→clean→index+extract→risk+match
            task = TaskService(self._session).create_pipeline_task(version, project_id, actor_id)
            self._audit.record(
                actor_id=actor_id,
                action="UPLOAD_DOCUMENT",
                target_type="DOCUMENT_VERSION",
                target_id=version.id,
                project_id=project_id,
                after={"sha256": version.sha256, "version_no": version.version_no},
            )
            self._session.commit()
        except (IntegrityError, ObjectStorageUnavailable) as exc:
            self._session.rollback()
            self._compensate_object(object_key)
            if isinstance(exc, IntegrityError):
                raise DomainError("VERSION_CONFLICT", "文件版本冲突，请重试上传", 409) from exc
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        except Exception:
            self._session.rollback()
            self._compensate_object(object_key)
            raise

        # 通过 ARQ enqueue_job 触发 bid pipeline
        self._enqueue_pipeline(
            document_version_id=str(version.id),
            project_id=str(project_id),
            enterprise_name="",
            thread_id=version.pipeline_thread_id or f"bid-{version.id}",
        )
        return DocumentTaskResponse(
            document_id=document.id,
            document_version_id=version.id,
            version_no=version.version_no,
            task=self._task_response(task),
        )

    def _persist_enterprise_upload(
        self, actor_id: UUID, staged_upload: StagedUpload
    ) -> DocumentTaskResponse:
        object_key: str | None = None
        try:
            document = self._documents.get_enterprise_by_logical_name_for_update(
                staged_upload.logical_name
            )
            if document is None:
                document = Document(
                    id=uuid4(),
                    project_id=None,
                    document_type="ENTERPRISE",
                    logical_name=staged_upload.logical_name,
                    created_at=self._now(),
                    created_by=actor_id,
                )
                self._documents.add_document(document)
                self._session.flush()
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_no=self._documents.next_version_no(document.id),
                file_name=staged_upload.file_name,
                file_size=staged_upload.file_size,
                mime_type=staged_upload.mime_type,
                object_key="",
                sha256=staged_upload.sha256,
                parse_status="QUEUED",
                created_at=self._now(),
                created_by=actor_id,
            )
            version.object_key = f"documents/{document.id}/{version.id}/source"
            object_key = version.object_key
            self._storage.put_file(object_key, staged_upload.path, staged_upload.mime_type)
            self._documents.add_version(version)
            self._session.flush()
            document.current_version_id = version.id
            version.pipeline_thread_id = f"bid-{version.id}"
            # 统一走 bid_pipeline，enterprise 无 project_id
            task = TaskService(self._session).create_pipeline_task(version, None, actor_id)
            self._audit.record(
                actor_id=actor_id,
                action="UPLOAD_ENTERPRISE_MATERIAL_DOCUMENT",
                target_type="DOCUMENT_VERSION",
                target_id=version.id,
                after={"sha256": version.sha256, "version_no": version.version_no},
            )
            self._session.commit()
        except (IntegrityError, ObjectStorageUnavailable) as exc:
            self._session.rollback()
            self._compensate_object(object_key)
            if isinstance(exc, IntegrityError):
                raise DomainError("VERSION_CONFLICT", "文件版本冲突，请重试上传", 409) from exc
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        except Exception:
            self._session.rollback()
            self._compensate_object(object_key)
            raise
        # 企业材料同样走 ARQ bid_pipeline（project_id 为空，用文件名作为企业材料标识）
        self._enqueue_pipeline(
            document_version_id=str(version.id),
            project_id=None,
            enterprise_name=staged_upload.logical_name,
            thread_id=version.pipeline_thread_id or f"bid-{version.id}",
        )
        return DocumentTaskResponse(
            document_id=document.id,
            document_version_id=version.id,
            version_no=version.version_no,
            task=self._task_response(task),
        )

    def _persist_knowledge_upload(
        self,
        actor_id: UUID,
        staged_upload: StagedUpload,
        *,
        knowledge_type: str | None,
        title: str | None,
        authority: str | None,
        source_reference: str | None,
        issued_on: date | None,
        effective_on: date | None,
        citation_note: str | None,
        entry_id: UUID | None,
    ) -> KnowledgeDocumentTaskResponse:
        object_key: str | None = None
        try:
            now = self._now()
            if entry_id is None:
                if knowledge_type is None or title is None or source_reference is None:
                    raise ValueError("new knowledge source is incomplete")
                entry = KnowledgeEntry(
                    id=uuid4(),
                    knowledge_type=knowledge_type,
                    title=title,
                    authority=authority,
                    source_reference=source_reference,
                    created_at=now,
                    created_by=actor_id,
                    updated_at=now,
                    deleted_at=None,
                )
                self._knowledge.add(entry)
                self._session.flush()
            else:
                entry = self._knowledge.get_entry(entry_id, for_update=True)
                if entry is None or entry.deleted_at is not None:
                    raise DomainError("RESOURCE_NOT_FOUND", "知识条目不存在", 404)
                entry.updated_at = now

            document = Document(
                id=uuid4(),
                project_id=None,
                document_type=entry.knowledge_type,
                logical_name=entry.title,
                created_at=now,
                created_by=actor_id,
            )
            self._documents.add_document(document)
            self._session.flush()
            version = DocumentVersion(
                id=uuid4(),
                document_id=document.id,
                version_no=1,
                file_name=staged_upload.file_name,
                file_size=staged_upload.file_size,
                mime_type=staged_upload.mime_type,
                object_key="",
                sha256=staged_upload.sha256,
                parse_status="QUEUED",
                created_at=now,
                created_by=actor_id,
            )
            version.object_key = f"knowledge-source/{entry.id}/{version.id}/source"
            object_key = version.object_key
            self._storage.put_file(object_key, staged_upload.path, staged_upload.mime_type)
            self._documents.add_version(version)
            self._session.flush()
            document.current_version_id = version.id
            knowledge_version = KnowledgeVersion(
                id=uuid4(),
                knowledge_entry_id=entry.id,
                source_document_version_id=version.id,
                version_no=self._knowledge.next_version_no(entry.id),
                status="DRAFT",
                content="",
                issued_on=issued_on,
                effective_on=effective_on,
                citation_note=citation_note,
                published_at=None,
                published_by=None,
                created_at=now,
                created_by=actor_id,
            )
            self._knowledge.add(knowledge_version)
            # [Claude] 统一走 bid_pipeline，knowledge 无 project_id 传 None
            task = TaskService(self._session).create_pipeline_task(version, None, actor_id)
            self._audit.record(
                actor_id=actor_id,
                action="UPLOAD_KNOWLEDGE_SOURCE_DOCUMENT",
                target_type="KNOWLEDGE_VERSION",
                target_id=knowledge_version.id,
                after={
                    "document_type": document.document_type,
                    "document_version_id": str(version.id),
                    "version_no": knowledge_version.version_no,
                    "sha256": version.sha256,
                },
            )
            self._session.commit()
        except (IntegrityError, ObjectStorageUnavailable) as exc:
            self._session.rollback()
            self._compensate_object(object_key)
            if isinstance(exc, IntegrityError):
                raise DomainError("VERSION_CONFLICT", "知识版本冲突，请重新上传", 409) from exc
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
        except Exception:
            self._session.rollback()
            self._compensate_object(object_key)
            raise

        # 数据已提交后才入队；若队列不可用，明确失败而不是留下永远 RUNNING 的假任务。
        try:
            job_id = self._enqueue_pipeline(
                document_version_id=str(version.id),
                project_id=None,
                chunk_type=document.document_type,
                document_id=str(document.id),
                knowledge_version_id=str(knowledge_version.id),
                title=entry.title,
                authority=entry.authority,
                source_reference=entry.source_reference,
                content_summary=entry.title,
                actor_id=str(actor_id),
                object_key=object_key,
                file_name=version.file_name,
                mime_type=version.mime_type,
            )
        except Exception as exc:
            self._mark_enqueue_failed(version, task, str(exc))
            raise DomainError(
                "TASK_QUEUE_UNAVAILABLE", "知识文档任务入队失败，请稍后重试", 503
            ) from exc
        if not job_id:
            self._mark_enqueue_failed(
                version, task, "Redis queue is not configured or returned no job id"
            )
            raise DomainError("TASK_QUEUE_UNAVAILABLE", "知识文档任务未进入队列，请稍后重试", 503)
        return KnowledgeDocumentTaskResponse(
            knowledge=self._knowledge_response(entry, knowledge_version),
            document_id=document.id,
            document_version_id=version.id,
            version_no=knowledge_version.version_no,
            task=self._task_response(task),
        )

    @staticmethod
    def _enqueue_pipeline(
        document_version_id: str,
        project_id: str | None,
        enterprise_name: str = "",
        thread_id: str | None = None,
        *,
        chunk_type: str | None = None,
        document_id: str | None = None,
        knowledge_version_id: str | None = None,
        title: str | None = None,
        authority: str | None = None,
        source_reference: str | None = None,
        content_summary: str | None = None,
        actor_id: str | None = None,
        object_key: str | None = None,
        file_name: str | None = None,
        mime_type: str | None = None,
    ) -> str:
        """通过 ARQ enqueue_job 触发文档处理 pipeline。

        chunk_type 有值 → run_knowledge_pipeline（LEGAL/CASE）
        否则 → run_bid_pipeline（TENDER / ENTERPRISE，参数对齐 app/worker.py）
        """
        import asyncio
        import concurrent.futures

        from arq import create_pool
        from arq.connections import RedisSettings

        from app.core.config import get_settings

        settings = get_settings()
        if not settings.redis_url:
            return ""

        async def _enqueue() -> str:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            try:
                if chunk_type is not None:
                    # knowledge_pipeline：参数对齐 worker.py run_knowledge_pipeline
                    job = await pool.enqueue_job(
                        "run_knowledge_pipeline",
                        document_version_id,
                        document_id or "",
                        knowledge_version_id or "",
                        chunk_type,
                        title or "",
                        authority,
                        source_reference or "",
                        content_summary or "",
                        actor_id or "",
                        object_key or "",
                        file_name or "",
                        mime_type or "application/pdf",
                    )
                else:
                    # bid_pipeline：参数对齐 worker.py run_bid_pipeline
                    job = await pool.enqueue_job(
                        "run_bid_pipeline",
                        document_version_id,
                        project_id or "",
                        enterprise_name or "",
                        thread_id or f"bid-{document_version_id}",
                    )
                return job.job_id if job else ""
            finally:
                await pool.close()

        # 上传路由是 sync def（线程池执行），但兜底处理已有事件循环的场景，
        # 避免在 async 上下文中 asyncio.run 抛 RuntimeError。
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_enqueue())
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(_enqueue())).result()

    def _enqueue_retry_knowledge(
        self, document: Document, version: DocumentVersion, actor_id: UUID
    ) -> None:
        """重试 LEGAL/CASE 文档：从知识条目重建 knowledge_pipeline 入队参数。"""
        from sqlalchemy import select

        knowledge_version = self._session.execute(
            select(KnowledgeVersion)
            .where(KnowledgeVersion.source_document_version_id == version.id)
            .order_by(KnowledgeVersion.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        if knowledge_version is None:
            version.parse_status = "FAILED"
            version.error_code = "KNOWLEDGE_VERSION_MISSING"
            version.error_message = "知识版本记录缺失，无法重新执行。"
            self._session.commit()
            return
        entry = self._session.get(KnowledgeEntry, knowledge_version.knowledge_entry_id)
        if entry is None:
            return
        self._enqueue_pipeline(
            document_version_id=str(version.id),
            project_id=None,
            chunk_type=document.document_type,
            document_id=str(document.id),
            knowledge_version_id=str(knowledge_version.id),
            title=entry.title,
            authority=entry.authority,
            source_reference=entry.source_reference,
            content_summary=entry.title,
            actor_id=str(actor_id),
            object_key=version.object_key,
            file_name=version.file_name,
            mime_type=version.mime_type,
        )

    def _mark_enqueue_failed(self, version: DocumentVersion, task: Task, message: str) -> None:
        """Persist a terminal state when the post-commit queue handoff fails."""
        version.parse_status = "FAILED"
        version.error_code = "TASK_QUEUE_UNAVAILABLE"
        version.error_message = message[:4_000]
        task.status = "FAILED"
        task.error_code = "TASK_QUEUE_UNAVAILABLE"
        task.error_message = message[:4_000]
        task.completed_at = self._now()
        self._session.commit()

    def _get_visible_tender_document(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> Document:
        document = self._documents.get_document(document_id)
        if (
            document is None
            or document.document_type != "TENDER"
            or document.project_id is None
            or document.deleted_at is not None
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        self._projects.get_visible(document.project_id, actor_id, role_codes)
        return document

    def _get_visible_document(
        self, document_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> Document:
        document = self._documents.get_document(document_id)
        if document is None or document.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        if document.document_type == "TENDER":
            return self._get_visible_tender_document(document_id, actor_id, role_codes)
        if document.document_type == "ENTERPRISE" and can_manage_enterprise_materials(role_codes):
            return document
        if document.document_type in {"LEGAL", "CASE"} and can_manage_knowledge(role_codes):
            return document
        raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)

    def _resolve_version(self, document: Document, version_no: int | None) -> DocumentVersion:
        version = (
            self._documents.get_version_by_no(document.id, version_no)
            if version_no is not None
            else self._documents.get_version(document.current_version_id)  # type: ignore[arg-type]
        )
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return version

    @staticmethod
    def _safe_file_name(value: str | None) -> str:
        file_name = (value or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1].strip()
        if not file_name or len(file_name) > 512:
            raise DomainError("FILE_SECURITY_REJECTED", "文件名无效", 422)
        return file_name

    @staticmethod
    def _sniff_mime(path: Path, extension: str) -> str | None:
        with path.open("rb") as source:
            head = source.read(8192)
        if extension == ".pdf":
            return "application/pdf" if head.startswith(b"%PDF-") else None
        if extension in {".jpg", ".jpeg"}:
            return "image/jpeg" if head.startswith(b"\\xff\\xd8\\xff") else None
        if extension == ".png":
            return "image/png" if head.startswith(b"\\x89PNG\\r\\n\\x1a\\n") else None
        if not head.startswith(b"PK\x03\x04"):
            return None
        expected_member = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
        }[extension]
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        return (
            _ALLOWED_FILES[extension]
            if "[Content_Types].xml" in names and expected_member in names
            else None
        )

    def _compensate_object(self, object_key: str | None) -> None:
        if object_key is None:
            return
        try:
            self._storage.delete_object(object_key)
        except ObjectStorageUnavailable:
            # The transaction has already failed. Only this exact, server-owned
            # key may be considered for later orphan-object reconciliation.
            pass

    @staticmethod
    def _now():
        from datetime import UTC, datetime

        return datetime.now(UTC)

    def _document_response(self, document: Document) -> DocumentResponse:
        return DocumentResponse(
            id=document.id,
            project_id=document.project_id,
            document_type=document.document_type,
            logical_name=document.logical_name,
            current_version_id=document.current_version_id,
            versions=[
                self._version_response(version)
                for version in self._documents.list_versions(document.id)
            ],
        )

    def _knowledge_response(
        self, entry: KnowledgeEntry, version: KnowledgeVersion
    ) -> KnowledgeResponse:
        source_version = (
            self._documents.get_version(version.source_document_version_id)
            if version.source_document_version_id is not None
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
            source_parse_status=None if source_version is None else source_version.parse_status,
            source_cleaning_summary=None
            if source_version is None
            else source_version.cleaning_summary,
            published_at=version.published_at,
            created_at=version.created_at,
        )

    @staticmethod
    def _version_response(version: DocumentVersion) -> DocumentVersionResponse:
        return DocumentVersionResponse(
            id=version.id,
            version_no=version.version_no,
            file_name=version.file_name,
            file_size=version.file_size,
            mime_type=version.mime_type,
            sha256=version.sha256,
            parse_status=version.parse_status,
            error_code=version.error_code,
            error_message=version.error_message,
            cleaning_summary=version.cleaning_summary,
            created_at=version.created_at,
            completed_at=version.completed_at,
        )

    @staticmethod
    def _task_response(task: Task) -> TaskResponse:
        return TaskResponse(
            id=task.id,
            task_type=task.task_type,
            target_type=task.target_type,
            target_id=task.target_id,
            status=task.status,
            attempt=task.attempt,
            error_code=task.error_code,
            error_message=task.error_message,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    @staticmethod
    def _node_response(node: DocumentNode) -> DocumentNodeResponse:
        return DocumentNodeResponse(
            id=node.id,
            document_version_id=node.document_version_id,
            node_type=node.node_type,
            page_number=node.page_number,
            section_path=node.section_path,
            order_no=node.order_no,
            content=node.content,
            content_hash=node.content_hash,
            cleaned_content=node.cleaned_content,
            cleaning_metadata=node.cleaning_metadata,
            bbox=node.bbox,
            metadata=node.metadata_,
        )
