"""投标管线 HTTP 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TenderPipelineReviewRequest(BaseModel):
    """人工确认 LLM 提取字段后的恢复载荷。"""

    decision: str = Field(pattern="^(approved|rejected)$")
    # None 兼容旧客户端的“全量确认”；新客户端显式传入勾选的候选字段。
    approved_tag_codes: list[str] | None = None
    reviewed_tags: dict[str, dict[str, object]] = Field(default_factory=dict)


class TenderPipelineReviewDraftRequest(BaseModel):
    """人工复核表单的暂存内容。

    草稿保留用户正在编辑的原始文本，不在此阶段做字段类型校验；类型校验只在
    最终提交时进行，避免日期、JSON 等编辑到一半就无法暂存。
    """

    drafts: dict[str, str] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)


class TenderPipelineStageResponse(BaseModel):
    """前端可展示的阶段状态；摘要字段只保存脱敏后的结构化信息。"""

    stage_name: str
    status: str
    input_summary: dict[str, object] | None
    output_summary: dict[str, object] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class TenderPipelineRunResponse(BaseModel):
    id: UUID
    document_version_id: UUID
    status: str
    attempt: int
    downstream_analysis_run_id: UUID | None
    pending_review: dict[str, object] | None
    stages: list[TenderPipelineStageResponse] = Field(default_factory=list)
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TenderTagCatalogItem(BaseModel):
    """供前端展示和人工审核使用的标签基线字段。"""

    code: str
    name: str
    category_code: str
    level_code: str
    data_type: str
    is_required: bool
    is_multi_value: bool
    value_example: str | None
    remark: str | None
