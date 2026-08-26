"""Tender document management API.

Upload, parse, version, and download tender bid documents (PDF/DOCX).
Provides document nodes (chunks), clause extraction, and task status polling.
"""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, get_current_user, get_document_service
from app.core.config import Settings, get_settings
from app.schemas.documents import (
    BidDocumentCard,
    DocumentNodePage,
    DocumentResponse,
    DocumentTaskResponse,
    DocumentVersionResponse,
    TaskResponse,
    TenderClauseResponse,
)
from app.services.document_service import DocumentService

router = APIRouter(tags=["documents"])


@router.get("/projects/{project_id}/documents", response_model=list[BidDocumentCard])
def list_project_documents(
    # List all documents uploaded to a project.
    # Returns document cards with name, parse status, and creation time.
    project_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[BidDocumentCard]:
    # 必须经服务层回查项目成员资格；不能因为列表查询而绕过授权。
    documents = service.list_project_documents(
        project_id, current_user.id, current_user.role_codes
    )
    return [
        BidDocumentCard(
            doc_id=document.id,
            doc_name=(
                document.versions[0].file_name
                if document.versions
                else document.logical_name
            ),
            parse_status=(
                document.versions[0].parse_status if document.versions else "UPLOADED"
            ),
            created_at=(
                document.versions[0].created_at.isoformat()
                if document.versions
                else None
            ),
        )
        for document in documents
    ]


@router.post(
    "/projects/{project_id}/documents",
    response_model=DocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_project_document(
    # Upload a tender document (PDF/DOCX) to a project.
    # Triggers async parsing and chunking; returns task ID for status polling.
    project_id: UUID,
    document_type: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    service: DocumentService = Depends(get_document_service),
) -> DocumentTaskResponse:
    return service.upload_tender_document(
        project_id,
        current_user.id,
        current_user.role_codes,
        document_type,
        file,
        settings.max_upload_bytes,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(
    # Get document metadata and current version info.
    document_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    return service.get_document(document_id, current_user.id, current_user.role_codes)


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersionResponse])
def list_document_versions(
    # List all versions of a document.
    document_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[DocumentVersionResponse]:
    return service.list_versions(document_id, current_user.id, current_user.role_codes)


@router.get("/documents/{document_id}/nodes", response_model=DocumentNodePage)
def list_document_nodes(
    # Paginated access to document chunks (nodes).
    # Nodes are ordered by position; version_no filters by document version.
    document_id: UUID,
    version_no: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentNodePage:
    return service.list_nodes(
        document_id, version_no, offset, limit, current_user.id, current_user.role_codes
    )


@router.get("/documents/{document_id}/clauses", response_model=list[TenderClauseResponse])
def list_document_clauses(
    # List extracted tender clauses from a document.
    document_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[TenderClauseResponse]:
    return service.list_clauses(document_id, current_user.id, current_user.role_codes)


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_document(
    # Re-submit a failed document parsing task.
    document_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentTaskResponse:
    return service.retry_document(document_id, current_user.id, current_user.role_codes)


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=DocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    # Re-run parsing on a successfully parsed document with current rules.
    document_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> DocumentTaskResponse:
    return service.reprocess_document(document_id, current_user.id, current_user.role_codes)


@router.get("/documents/{document_id}/download")
def download_document(
    # Download original document file (or specific version).
    document_id: UUID,
    version_no: int | None = Query(default=None, ge=1),
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> StreamingResponse:
    download = service.create_authorized_download(
        document_id, version_no, current_user.id, current_user.role_codes
    )
    return StreamingResponse(
        download.stream,
        media_type=download.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(download.file_name)}"
        },
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    # Poll async task status (parse, index, etc.).
    task_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentService = Depends(get_document_service),
) -> TaskResponse:
    return service.get_task(task_id, current_user.id, current_user.role_codes)
