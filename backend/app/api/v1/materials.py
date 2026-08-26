"""Enterprise qualification materials management API.

CRUD for enterprise qualification documents (certificates, case studies, etc.)
used in bid matching. Documents are parsed and indexed for qualification checks.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, get_document_service
from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.schemas.documents import DocumentTaskResponse
from app.schemas.materials import (
    EnterpriseMaterialCreate,
    EnterpriseMaterialResponse,
    EnterpriseMaterialUpdate,
    MaterialDocumentAttachRequest,
)
from app.services.document_service import DocumentService
from app.services.material_service import MaterialService

router = APIRouter(prefix="/enterprise-materials", tags=["enterprise-materials"])


@router.get("", response_model=list[EnterpriseMaterialResponse])
def list_materials(
    # List enterprise materials. Without enterprise_id, returns materials for all
    # enterprises the current user belongs to.
    enterprise_id: UUID | None = Query(None, description="Enterprise ID filter"),
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[EnterpriseMaterialResponse]:
    return MaterialService(session).list(current_user.role_codes, enterprise_id)


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(
    # Soft delete a material record.
    material_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> None:
    MaterialService(session).delete(material_id, current_user.id, current_user.role_codes)


@router.post("", response_model=EnterpriseMaterialResponse, status_code=status.HTTP_201_CREATED)
def create_material(
    # Create a new qualification material entry (without document).
    payload: EnterpriseMaterialCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseMaterialResponse:
    return MaterialService(session).create(current_user.id, current_user.role_codes, payload)


@router.patch("/{material_id}", response_model=EnterpriseMaterialResponse)
def update_material(
    # Update material metadata (name, type, expiry, etc.).
    material_id: UUID,
    payload: EnterpriseMaterialUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseMaterialResponse:
    return MaterialService(session).update(
        material_id, current_user.id, current_user.role_codes, payload
    )


@router.post(
    "/{material_id}/documents",
    response_model=DocumentTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_material_document(
    # Upload a document for an existing material entry.
    # Triggers async parsing and indexing.
    material_id: UUID,
    file: Annotated[UploadFile, File()],
    current_user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db_session),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentTaskResponse:
    materials = MaterialService(session)
    materials.ensure_upload_allowed(material_id, current_user.role_codes)
    result = document_service.upload_enterprise_material_document(
        current_user.id, current_user.role_codes, file, settings.max_upload_bytes
    )
    materials.attach_document(
        material_id,
        result.document_id,
        result.document_version_id,
        current_user.id,
        current_user.role_codes,
    )
    return result


@router.post("/{material_id}/documents/attach", response_model=EnterpriseMaterialResponse)
def attach_existing_material_document(
    material_id: UUID,
    payload: MaterialDocumentAttachRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    document_service: DocumentService = Depends(get_document_service),
) -> EnterpriseMaterialResponse:
    """关联当前用户有权访问的已有证明文件，避免重复上传同一份企业档案。"""
    document = document_service.get_document(
        payload.document_id, current_user.id, current_user.role_codes
    )
    if not any(version.id == payload.document_version_id for version in document.versions):
        from app.core.errors import DomainError

        raise DomainError("RESOURCE_NOT_FOUND", "证明文件版本不存在", 404)
    return MaterialService(session).attach_document(
        material_id,
        payload.document_id,
        payload.document_version_id,
        current_user.id,
        current_user.role_codes,
    )
