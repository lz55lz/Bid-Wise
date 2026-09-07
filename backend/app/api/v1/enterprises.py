"""投标企业的基础维护接口。"""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.projects.enterprise_schemas import (
    EnterpriseCreateRequest,
    EnterpriseMemberCreateRequest,
    EnterpriseMemberResponse,
    EnterpriseMemberUpdateRequest,
    EnterpriseResponse,
    EnterpriseUpdateRequest,
)
from app.modules.projects.enterprise_service import EnterpriseService

router = APIRouter(prefix="/enterprises", tags=["投标企业"])


@router.get("", response_model=list[EnterpriseResponse])
async def list_enterprises(
    current_user: CurrentUser, session: DatabaseSession
) -> list[EnterpriseResponse]:
    return await EnterpriseService(session).list(current_user)


@router.post("", response_model=EnterpriseResponse, status_code=status.HTTP_201_CREATED)
async def create_enterprise(
    payload: EnterpriseCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EnterpriseResponse:
    return await EnterpriseService(session).create(current_user, payload)


@router.get("/{enterprise_id}", response_model=EnterpriseResponse)
async def get_enterprise(
    enterprise_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EnterpriseResponse:
    return await EnterpriseService(session).get(enterprise_id, current_user)


@router.patch("/{enterprise_id}", response_model=EnterpriseResponse)
async def update_enterprise(
    enterprise_id: UUID,
    payload: EnterpriseUpdateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EnterpriseResponse:
    return await EnterpriseService(session).update(enterprise_id, current_user, payload)


@router.delete("/{enterprise_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_enterprise(
    enterprise_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> Response:
    """仅允许删除未绑定项目的企业，避免让项目材料范围失真。"""
    await EnterpriseService(session).delete(enterprise_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{enterprise_id}/members", response_model=list[EnterpriseMemberResponse])
async def list_members(
    enterprise_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[EnterpriseMemberResponse]:
    return await EnterpriseService(session).list_members(enterprise_id, current_user)


@router.post(
    "/{enterprise_id}/members",
    response_model=EnterpriseMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    enterprise_id: UUID,
    payload: EnterpriseMemberCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EnterpriseMemberResponse:
    return await EnterpriseService(session).add_member(enterprise_id, current_user, payload)


@router.patch("/{enterprise_id}/members/{member_id}", response_model=EnterpriseMemberResponse)
async def update_member(
    enterprise_id: UUID,
    member_id: UUID,
    payload: EnterpriseMemberUpdateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EnterpriseMemberResponse:
    return await EnterpriseService(session).update_member(
        enterprise_id, member_id, current_user, payload
    )


@router.delete("/{enterprise_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    enterprise_id: UUID, member_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> Response:
    await EnterpriseService(session).remove_member(enterprise_id, member_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
