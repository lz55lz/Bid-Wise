# ruff: noqa: E501
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.materials.schemas import MaterialResponse, MaterialUpsertRequest
from app.modules.materials.service import MaterialService

router = APIRouter(prefix="/enterprise-materials", tags=["企业材料库"])


@router.get("", response_model=list[MaterialResponse])
async def list_materials(
    current_user: CurrentUser,
    session: DatabaseSession,
    enterprise_id: Annotated[UUID | None, Query()] = None,
) -> list[MaterialResponse]:
    return await MaterialService(session).list(current_user, enterprise_id)


@router.post("", response_model=MaterialResponse, status_code=201)
async def create_material(
    payload: MaterialUpsertRequest, current_user: CurrentUser, session: DatabaseSession
) -> MaterialResponse:
    return await MaterialService(session).create(current_user, payload)


@router.put("/{material_id}", response_model=MaterialResponse)
async def update_material(
    material_id: UUID,
    payload: MaterialUpsertRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> MaterialResponse:
    return await MaterialService(session).update(material_id, current_user, payload)


@router.post("/{material_id}/confirm", response_model=MaterialResponse)
async def confirm_material(
    material_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> MaterialResponse:
    return await MaterialService(session).confirm(material_id, current_user)


@router.post("/{material_id}/archive", response_model=MaterialResponse)
async def archive_material(
    material_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> MaterialResponse:
    return await MaterialService(session).archive(material_id, current_user)
