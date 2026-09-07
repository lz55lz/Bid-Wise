"""项目范围 Evidence 索引与检索接口。"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.integrations.embedding import BgeM3EmbeddingClient
from app.integrations.reranker import RankV2Reranker
from app.modules.evidence.schemas import EvidenceResponse
from app.modules.evidence.service import EvidenceService
from app.modules.indexing.schemas import EvidenceIndexJobResponse
from app.modules.indexing.service import EvidenceIndexingService
from app.modules.retrieval.schemas import (
    EvidenceSearchRequest,
    EvidenceSearchResponse,
)
from app.modules.retrieval.ranked_service import RankedEvidenceRetrievalService
from app.modules.retrieval.candidate_service import EvidenceCandidateRetrievalService

router = APIRouter(prefix="/projects/{project_id}/evidences", tags=["Evidence 检索"])


@router.get("/{evidence_id}", response_model=EvidenceResponse)
async def get_project_evidence(
    project_id: UUID,
    evidence_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> EvidenceResponse:
    """按项目回查授权后返回可定位的 Evidence 原文片段。"""
    return await EvidenceService(session).get(project_id, evidence_id, current_user)


@router.post(
    "/index",
    response_model=EvidenceIndexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def index_project_evidences(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> EvidenceIndexJobResponse:
    """冻结未索引 Evidence 后提交后台索引任务，限负责人或系统管理员。"""
    return await EvidenceIndexingService(session, settings).submit(project_id, current_user)


@router.get("/index-jobs/{job_id}", response_model=EvidenceIndexJobResponse)
async def get_evidence_index_job(
    project_id: UUID,
    job_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> EvidenceIndexJobResponse:
    """查询索引任务状态；服务端会验证项目成员身份和任务归属。"""
    return await EvidenceIndexingService(session, settings).get(project_id, job_id, current_user)


@router.post("/search", response_model=EvidenceSearchResponse)
async def search_project_evidences(
    project_id: UUID,
    payload: EvidenceSearchRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> EvidenceSearchResponse:
    """仅在当前授权项目中召回 Evidence，不支持跨项目查询。"""
    candidates = EvidenceCandidateRetrievalService(session, BgeM3EmbeddingClient(settings))
    ranked = RankedEvidenceRetrievalService(session, candidates, RankV2Reranker(settings))
    return EvidenceSearchResponse(
        items=await ranked.retrieve(
            project_id, current_user, payload.query, limit=payload.limit
        )
    )
