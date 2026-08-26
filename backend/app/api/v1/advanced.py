"""Knowledge base management API.

CRUD operations for policy/regulation and technical standard entries,
supporting document upload, version management, and publish/unpublish.

Typical workflow:
1. Create entry (create_knowledge_entry)
2. Upload document (upload_knowledge_document)
3. Revise entry metadata (revise_knowledge_entry)
4. Publish version for RAG retrieval (publish_knowledge_version)
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, get_document_service
from app.core.config import get_settings
from app.db.session import get_db_session
from app.integrations.object_storage import MinioObjectStorage
from app.integrations.vector_store import PgVectorStore
from app.schemas.knowledge import (
    KnowledgeCreateRequest,
    KnowledgeDocumentTaskResponse,
    KnowledgeResponse,
    KnowledgeRevisionRequest,
)
from app.services.document_service import DocumentService
from app.services.knowledge_service import KnowledgeService

router = APIRouter(tags=["knowledge"])


def _service(session: Session) -> KnowledgeService:
    """Build KnowledgeService with storage and vector store dependencies."""
    settings = get_settings()
    return KnowledgeService(session, MinioObjectStorage(settings), PgVectorStore(settings))


@router.post(
    "/knowledge-entries", response_model=KnowledgeResponse, status_code=status.HTTP_201_CREATED
)
def create_knowledge_entry(
    payload: KnowledgeCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> KnowledgeResponse:
    """Create a knowledge base entry (without document).

    Create a new knowledge record; document can be uploaded later via
    upload_knowledge_document.
    """
    return _service(session).create(current_user.id, current_user.role_codes, payload)


@router.get("/knowledge-entries", response_model=list[KnowledgeResponse])
def list_knowledge_entries(
    query: str | None = Query(default=None, max_length=256),
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[KnowledgeResponse]:
    """Query knowledge base entries.

    - query: fuzzy search by title
    - Returns all entries the current user has permission to view.
    """
    return _service(session).list(current_user.role_codes, query)


@router.delete("/knowledge-entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_entry(
    entry_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> None:
    """Delete a knowledge base entry (soft delete).

    Sets deleted_at to current time.
    """
    _service(session).delete(entry_id, current_user.id, current_user.role_codes)


@router.post(
    "/knowledge-entries/documents",
    response_model=KnowledgeDocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_knowledge_document(
    # Upload knowledge document (create new entry with document)
    # Async parse document, return task ID for status polling.
    # Document will be chunked, embedded, and stored in vector DB for RAG.
    knowledge_type: Annotated[str, Form()],
    title: Annotated[str, Form()],
    source_reference: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    authority: Annotated[str | None, Form()] = None,
    issued_on: Annotated[date | None, Form()] = None,
    effective_on: Annotated[date | None, Form()] = None,
    citation_note: Annotated[str | None, Form()] = None,
    current_user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
    service: DocumentService = Depends(get_document_service),
) -> KnowledgeDocumentTaskResponse:
    return service.upload_knowledge_document(
        current_user.id,
        current_user.role_codes,
        knowledge_type,
        title,
        authority,
        source_reference,
        issued_on,
        effective_on,
        citation_note,
        file,
        settings.max_upload_bytes,
    )


@router.post(
    "/knowledge-entries/{entry_id}/documents",
    response_model=KnowledgeDocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_knowledge_document_revision(
    # Upload new document revision for existing entry
    # entry_id: knowledge base entry ID
    # Preserves history; new document creates a new version record.
    entry_id: UUID,
    file: Annotated[UploadFile, File()],
    issued_on: Annotated[date | None, Form()] = None,
    effective_on: Annotated[date | None, Form()] = None,
    citation_note: Annotated[str | None, Form()] = None,
    current_user: CurrentUser = Depends(get_current_user),
    settings=Depends(get_settings),
    service: DocumentService = Depends(get_document_service),
) -> KnowledgeDocumentTaskResponse:
    return service.upload_knowledge_document_revision(
        entry_id,
        current_user.id,
        current_user.role_codes,
        issued_on,
        effective_on,
        citation_note,
        file,
        settings.max_upload_bytes,
    )


@router.post("/knowledge-entries/{entry_id}/versions", response_model=KnowledgeResponse)
def revise_knowledge_entry(
    # Revise knowledge base entry metadata
    # Update basic info (title, type, publish status); does not touch document content.
    entry_id: UUID,
    payload: KnowledgeRevisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> KnowledgeResponse:
    return _service(session).revise(entry_id, current_user.id, current_user.role_codes, payload)


@router.post("/knowledge-versions/{version_id}/publish", response_model=KnowledgeResponse)
def publish_knowledge_version(
    # Publish knowledge version for RAG retrieval.
    # Already-published versions are not affected.
    version_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> KnowledgeResponse:
    return _service(session).publish(version_id, current_user.id, current_user.role_codes)


@router.post("/knowledge-versions/{version_id}/unpublish", response_model=KnowledgeResponse)
def unpublish_knowledge_version(
    # Unpublish knowledge version.
    # After unpublish, the version is no longer retrieved by RAG, but data is preserved.
    version_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> KnowledgeResponse:
    return _service(session).unpublish(version_id, current_user.id, current_user.role_codes)
