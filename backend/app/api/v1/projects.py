"""投标项目 HTTP 接口。"""

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.projects.schemas import (
    ProjectAssignableUserResponse,
    ProjectCreateRequest,
    ProjectMemberResponse,
    ProjectMemberUpsertRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.modules.projects.service import ProjectService
from app.modules.projects.dashboard_service import DashboardService

router = APIRouter(prefix="/projects", tags=["投标项目"])


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[ProjectResponse]:
    """列出当前登录用户可见的项目。"""
    return await ProjectService(session).list_visible(current_user)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectResponse:
    """创建项目；创建人自动成为该项目的 OWNER 成员。"""
    return await ProjectService(session).create(current_user, payload)


@router.get("/status-projections")
async def list_project_status_projections(
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[dict[str, object]]:
    """项目列表的当前业务阶段；与工作台使用同一后端投影。"""
    return await DashboardService(session).status_projections(current_user)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectResponse:
    """获取项目详情，普通用户必须是项目成员。"""
    return await ProjectService(session).get_visible(project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectResponse:
    """更新项目元数据，权限限定为 OWNER 或 SYSTEM_ADMIN。"""
    return await ProjectService(session).update(project_id, current_user, payload)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectResponse:
    """归档项目，后续文档和分析写入会被相应服务拒绝。"""
    return await ProjectService(session).archive(project_id, current_user)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> Response:
    """软删除项目；删除后项目及其下级资源不再通过常规接口暴露。"""
    await ProjectService(session).delete(project_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[ProjectMemberResponse]:
    """列出当前项目成员，仍须由服务端校验项目成员资格。"""
    return await ProjectService(session).list_members(project_id, current_user)


@router.get("/{project_id}/assignable-users", response_model=list[ProjectAssignableUserResponse])
async def list_assignable_users(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[ProjectAssignableUserResponse]:
    """为项目成员管理页返回可选的启用账户及其当前加入状态。"""
    return await ProjectService(session).list_assignable_users(project_id, current_user)


@router.put("/{project_id}/members/{user_id}", response_model=ProjectMemberResponse)
async def upsert_project_member(
    project_id: UUID,
    user_id: UUID,
    payload: ProjectMemberUpsertRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectMemberResponse:
    """负责人授予或调整普通协作成员角色。"""
    return await ProjectService(session).upsert_member(project_id, user_id, current_user, payload)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_member(
    project_id: UUID,
    user_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> Response:
    """负责人移除普通协作成员。"""
    await ProjectService(session).remove_member(project_id, user_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{project_id}/members/{user_id}/transfer-ownership",
    response_model=ProjectMemberResponse,
)
async def transfer_project_ownership(
    project_id: UUID,
    user_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> ProjectMemberResponse:
    """将唯一项目负责人显式交接给已有成员。"""
    return await ProjectService(session).transfer_ownership(project_id, user_id, current_user)
