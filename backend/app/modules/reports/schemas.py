"""项目报告 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ReportCitation(BaseModel):
    """报告实际使用的 Evidence 摘要。"""

    evidence_id: UUID
    quoted_text: str
    locator: dict[str, object]


class ProjectReportResponse(BaseModel):
    """项目当前报告的任务状态和完成后的 Markdown 内容。"""

    id: UUID
    project_id: UUID
    report_type: str
    analysis_run_id: UUID | None
    status: str
    is_stale: bool
    finding_count: int
    content_markdown: str | None
    sections: list[dict[str, object]]
    citations: list[ReportCitation]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
