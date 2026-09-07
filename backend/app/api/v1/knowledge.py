"""通用知识库管理接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.knowledge.document_service import KnowledgeDocumentService
from app.modules.knowledge.retrieval_service import KnowledgeRetrievalService
from app.modules.knowledge.service import KnowledgeService

router = APIRouter(prefix="/knowledge-entries", tags=["通用知识库"])


class KnowledgeCreateRequest(BaseModel):
    knowledge_type: str = Field(pattern="^(LEGAL|CASE|TECHNICAL)$")
    title: str = Field(min_length=1, max_length=512)
    source_reference: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=20000)
    authority: str | None = Field(default=None, max_length=256)
    issued_on: datetime | None = None
    effective_on: datetime | None = None
    citation_note: str | None = Field(default=None, max_length=4000)


class KnowledgeRevisionRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    title: str | None = Field(default=None, min_length=1, max_length=512)
    source_reference: str | None = Field(default=None, min_length=1, max_length=1024)
    authority: str | None = Field(default=None, max_length=256)
    issued_on: datetime | None = None
    effective_on: datetime | None = None
    citation_note: str | None = Field(default=None, max_length=4000)


@router.get("")
async def list_entries(
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    query: str | None = Query(default=None, max_length=256),
) -> list[dict[str, object]]:
    return await KnowledgeService(session, settings).list(current_user, query)


@router.get("/search")
async def search_published_knowledge(
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    query: str = Query(min_length=1, max_length=2000),
    limit: int = Query(default=8, ge=1, le=20),
) -> list[dict[str, object]]:
    """供 Agent/RAG 调用的公共知识检索入口；仅返回已发布版本。"""
    del current_user  # 已认证用户均可读取企业公共法规库，具体筛选在服务/仓储中完成。
    return await KnowledgeRetrievalService(session, settings).search(query, limit)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_entry(
    payload: KnowledgeCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeService(session, settings).create(current_user, payload.model_dump())


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def create_entry_from_document(
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    file: Annotated[UploadFile, File()],
    knowledge_type: Annotated[str, Form(pattern="^(LEGAL|CASE|TECHNICAL)$")],
    title: Annotated[str, Form(min_length=1, max_length=512)],
    source_reference: Annotated[str, Form(min_length=1, max_length=1024)],
    authority: Annotated[str | None, Form(max_length=256)] = None,
    issued_on: Annotated[datetime | None, Form()] = None,
    effective_on: Annotated[datetime | None, Form()] = None,
    citation_note: Annotated[str | None, Form(max_length=4000)] = None,
) -> dict[str, object]:
    """从源文件创建知识条目并异步解析其首个版本。"""
    knowledge_service = KnowledgeService(session, settings)
    entry = await knowledge_service.create_document_entry(
        current_user,
        knowledge_type=knowledge_type,
        title=title,
        source_reference=source_reference,
        authority=authority,
        issued_on=issued_on,
        effective_on=effective_on,
        citation_note=citation_note,
    )
    return await KnowledgeDocumentService(session, settings).upload(entry.id, current_user, file)


@router.post("/{entry_id}/versions", status_code=status.HTTP_201_CREATED)
async def revise_entry(
    entry_id: UUID,
    payload: KnowledgeRevisionRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeService(session, settings).revise(
        current_user, entry_id, payload.model_dump(exclude_none=True)
    )


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> None:
    await KnowledgeService(session, settings).delete(current_user, entry_id)


@router.post("/versions/{version_id}/publish")
async def publish_version(
    version_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeService(session, settings).publish(current_user, version_id, True)


@router.post("/versions/{version_id}/unpublish")
async def unpublish_version(
    version_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeService(session, settings).publish(current_user, version_id, False)


@router.post("/versions/{version_id}/rebuild-index")
async def rebuild_manual_knowledge_index(
    version_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    """为历史手工录入正文补建向量索引。"""
    return await KnowledgeService(session, settings).rebuild_manual_index(current_user, version_id)


@router.post("/{entry_id}/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_source_document(
    entry_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    """上传法规/案例源文件并异步执行解析、切块和 bge-m3 向量化。"""
    return await KnowledgeDocumentService(session, settings).upload(entry_id, current_user, file)


@router.post("/{entry_id}/documents/{document_id}/parse", status_code=status.HTTP_202_ACCEPTED)
async def retry_source_document_parse(
    entry_id: UUID,
    document_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeDocumentService(session, settings).request_parse(
        entry_id, document_id, current_user
    )


@router.get("/{entry_id}/parse-jobs/{job_id}")
async def get_source_document_parse_job(
    entry_id: UUID,
    job_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    return await KnowledgeDocumentService(session, settings).get_job(entry_id, job_id, current_user)
