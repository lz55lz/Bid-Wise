"""项目范围 RAG 问答接口。"""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.integrations.embedding import BgeM3EmbeddingClient
from app.integrations.llm import MiniMaxM3Client
from app.integrations.reranker import RankV2Reranker
from app.modules.retrieval.rag_service import ProjectRagService
from app.modules.retrieval.schemas import RagAnswerRequest, RagAnswerResponse

router = APIRouter(prefix="/projects/{project_id}/rag", tags=["项目问答"])


@router.post("/answer", response_model=RagAnswerResponse)
async def answer_project_question(
    project_id: UUID,
    payload: RagAnswerRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> RagAnswerResponse:
    """只依据当前用户已授权项目内的 Evidence 回答，不命中时明确拒绝编造。"""
    service = ProjectRagService(
        session,
        BgeM3EmbeddingClient(settings),
        RankV2Reranker(settings),
        MiniMaxM3Client(settings),
    )
    return await service.answer(project_id, current_user, payload.question)
