"""项目文档 HTTP 接口。"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.documents.schemas import (
    DocumentNodePage,
    DocumentParseJobResponse,
    DocumentVersionResponse,
    ProjectDocumentResponse,
)
from app.modules.documents.service import DocumentService
from app.modules.tender_analysis.document_tag_service import DocumentTagService

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["项目文档"])


@router.get("", response_model=list[ProjectDocumentResponse])
async def list_project_documents(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> list[ProjectDocumentResponse]:
    """查询当前用户有权访问的项目文件。"""
    return await DocumentService(session, settings).list_project_documents(project_id, current_user)


@router.post("", response_model=ProjectDocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_project_document(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    logical_name: Annotated[str, Form(min_length=1, max_length=512)],
    file: Annotated[UploadFile, File()],
) -> ProjectDocumentResponse:
    """上传项目源文件；解析需在后续 Worker 接入后显式触发。"""
    document_service = DocumentService(session, settings)
    return await document_service.upload(project_id, current_user, logical_name, file)


@router.get("/{document_id}", response_model=ProjectDocumentResponse)
async def get_project_document(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ProjectDocumentResponse:
    """读取单个项目文档及其当前版本。"""
    return await DocumentService(session, settings).get_project_document(
        project_id, document_id, current_user
    )


@router.post("/{document_id}/parse", response_model=DocumentParseJobResponse)
async def request_document_parse(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> DocumentParseJobResponse:
    """显式提交解析任务；Worker 只接收任务 ID 并回查数据库。"""
    return await DocumentService(session, settings).request_parse(
        project_id,
        document_id,
        current_user,
    )


@router.get("/{document_id}/nodes", response_model=DocumentNodePage)
async def list_document_nodes(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    version_no: int | None = Query(default=None, ge=1),
) -> DocumentNodePage:
    return await DocumentService(session, settings).list_nodes(
        project_id, document_id, current_user, offset, limit, version_no
    )


@router.get("/{document_id}/tags")
async def list_document_tags(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> list[dict[str, object]]:
    """查看 bid_pipeline 的条款/字段提取事实，供人工定位和复核。"""
    del settings
    return await DocumentTagService(session).list(project_id, document_id, current_user)


@router.get("/{document_id}/parse-jobs/{job_id}", response_model=DocumentParseJobResponse)
async def get_document_parse_job(
    project_id: UUID,
    document_id: UUID,
    job_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> DocumentParseJobResponse:
    """查询指定文档的解析任务状态。"""
    return await DocumentService(session, settings).get_parse_job(
        project_id, document_id, job_id, current_user
    )


@router.get("/{document_id}/versions", response_model=list[DocumentVersionResponse])
async def list_document_versions(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> list[DocumentVersionResponse]:
    """查询逻辑文档的版本历史，不返回对象键。"""
    return await DocumentService(session, settings).list_document_versions(
        project_id, document_id, current_user
    )


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document_version(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    file: Annotated[UploadFile, File()],
) -> DocumentVersionResponse:
    """上传新版本，解析成功前当前有效版本保持不变。"""
    return await DocumentService(session, settings).upload_new_version(
        project_id, document_id, current_user, file
    )


@router.post("/{document_id}/versions/{version_id}/parse", response_model=DocumentParseJobResponse)
async def request_document_version_parse(
    project_id: UUID,
    document_id: UUID,
    version_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> DocumentParseJobResponse:
    """显式解析指定版本；同一逻辑文档同一时刻只允许一个版本解析。"""
    return await DocumentService(session, settings).request_parse(
        project_id, document_id, current_user, version_id
    )


@router.get("/{document_id}/download")
async def download_project_document(
    project_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> StreamingResponse:
    """仅在项目成员授权通过后代理流式下载，不暴露对象键或长期外链。"""
    download = await DocumentService(session, settings).create_authorized_download(
        project_id, document_id, current_user
    )
    safe_name = quote(download.file_name, safe="")
    return StreamingResponse(
        download.stream,
        media_type=download.mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"},
    )
