# ruff: noqa: E501
"""投标核心管线 HTTP 接口。"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.tender_analysis.schemas import (
    TenderPipelineReviewDraftRequest,
    TenderPipelineReviewRequest,
    TenderPipelineRunResponse,
    TenderTagCatalogItem,
)
from app.modules.tender_analysis.service import TenderPipelineService
from app.modules.tender_analysis.tag_catalog_service import TenderTagCatalogService

router = APIRouter(prefix="/projects/{project_id}/tender-pipeline", tags=["投标分析管线"])

tag_catalog_router = APIRouter(prefix="/tender-tags", tags=["招标标签库"])


class TenderTagActiveRequest(BaseModel):
    is_active: bool


@tag_catalog_router.get("", response_model=list[TenderTagCatalogItem])
async def list_tender_tags(
    current_user: CurrentUser,
    session: DatabaseSession,
    category_code: str | None = Query(default=None, min_length=1, max_length=20),
    level_code: str | None = Query(default=None, min_length=1, max_length=10),
) -> list[TenderTagCatalogItem]:
    """返回活动标签基线；认证后只读，不允许请求覆盖提取约束。"""
    del current_user
    return await TenderTagCatalogService(session).list_active(category_code, level_code)


@tag_catalog_router.patch("/{code}/active")
async def set_tender_tag_active(
    code: str,
    payload: TenderTagActiveRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, object]:
    await TenderTagCatalogService(session).set_active(
        code, payload.is_active, current_user.role_codes
    )
    return {"code": code, "is_active": payload.is_active}


@router.get("", response_model=list[TenderPipelineRunResponse])
async def list_runs(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[TenderPipelineRunResponse]:
    """返回最近管线运行，前端据此展示等待人工审核、失败重试等状态。"""
    return await TenderPipelineService(session, settings).list(project_id, current_user, limit)


@router.post(
    "/versions/{version_id}",
    response_model=TenderPipelineRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit(
    project_id: UUID,
    version_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> TenderPipelineRunResponse:
    return await TenderPipelineService(session, settings).submit(
        project_id, version_id, current_user
    )


@router.get("/{run_id}", response_model=TenderPipelineRunResponse)
async def get(
    project_id: UUID,
    run_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> TenderPipelineRunResponse:
    return await TenderPipelineService(session, settings).get(project_id, run_id, current_user)


@router.post(
    "/{run_id}/review",
    response_model=TenderPipelineRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def review(
    project_id: UUID,
    run_id: UUID,
    payload: TenderPipelineReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> TenderPipelineRunResponse:
    return await TenderPipelineService(session, settings).review(
        project_id, run_id, current_user, payload
    )


@router.patch("/{run_id}/review-draft", response_model=TenderPipelineRunResponse)
async def save_review_draft(
    project_id: UUID,
    run_id: UUID,
    payload: TenderPipelineReviewDraftRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> TenderPipelineRunResponse:
    """暂存字段复核表单，不推进人工审核流程。"""
    return await TenderPipelineService(session, settings).save_review_draft(
        project_id, run_id, current_user, payload.drafts, payload.notes
    )


@router.post(
    "/{run_id}/retry",
    response_model=TenderPipelineRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry(
    project_id: UUID,
    run_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> TenderPipelineRunResponse:
    """重试人工审核后的 LangGraph resume；审核前失败请重新提交文档版本。"""
    return await TenderPipelineService(session, settings).retry(project_id, run_id, current_user)
