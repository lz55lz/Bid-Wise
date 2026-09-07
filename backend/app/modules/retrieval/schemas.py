"""Evidence 索引与检索 API 契约。"""

from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceSearchRequest(BaseModel):
    """项目范围检索请求；项目范围来自 URL 而非请求体。"""

    query: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=10, ge=1, le=30)


class EvidenceSearchHit(BaseModel):
    """面向 UI 与 Agent 工具的受权 Evidence 召回结果。"""

    evidence_id: UUID
    quoted_text: str
    # 模型检索/重排可使用邻居扩展后的上下文，但审计引用始终锁定 quoted_text。
    context_text: str | None = None
    locator: dict[str, object]
    similarity: float
    # 仅作为模型理解命中片段时的章节上下文；引用仍使用 quoted_text 对应的 Evidence。
    parent_context: str | None = None


class EvidenceSearchResponse(BaseModel):
    """项目范围内的向量召回结果。"""

    items: list[EvidenceSearchHit]


class RagAnswerRequest(BaseModel):
    """项目问答请求；项目范围来自 URL，不允许模型或请求体覆盖。"""

    question: str = Field(min_length=1, max_length=2_000)


class RagCitation(BaseModel):
    """实际被回答引用的 Evidence。"""

    evidence_id: UUID
    quoted_text: str
    locator: dict[str, object]


class RagAnswerResponse(BaseModel):
    """只基于 Evidence 的确定性 RAG 回答。"""

    answer: str
    citations: list[RagCitation]
    no_evidence: bool
