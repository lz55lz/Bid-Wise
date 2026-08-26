from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.base import ApiSchema


class RagQuestion(ApiSchema):
    question: str = Field(min_length=1, max_length=2_000)
    session_id: UUID | None = Field(default=None, description="会话 ID，为空则创建新会话")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class RagAnswerDraft(ApiSchema):
    # 回答模型偶尔会携带其它分析字段；只接受本轮 RAG 合同所需字段。
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    answer: str = Field(min_length=1, max_length=8_000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=10)


class RagCitation(ApiSchema):
    evidence_id: UUID
    document_id: UUID
    document_version_id: UUID
    file_name: str
    version_no: int
    page_number: int | None
    quoted_text: str | None


class RagAnswerResponse(ApiSchema):
    answer: str
    citations: list[RagCitation]
    no_evidence: bool
