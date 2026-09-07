"""项目报告的受权只读检索。"""

from uuid import UUID

import jieba
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.reports.repository import ReportRepository


class ReportRetrievalService:
    """报告是项目结论层，只返回当前项目唯一有效的报告快照。"""

    def __init__(self, session: AsyncSession) -> None:
        self._projects = ProjectService(session)
        self._reports = ReportRepository(session)

    async def search(
        self, project_id: UUID, actor: AuthenticatedUser, query: str
    ) -> dict[str, object] | None:
        await self._projects.require_project_access(project_id, actor)
        report = await self._reports.get_latest_retrievable(project_id)
        if report is None or not (report.content_markdown or "").strip():
            return None
        content = self._relevant_sections(
            list(report.sections or []), report.content_markdown or "", query
        )
        return {
            "report_id": str(report.id),
            "content": content,
            "citations": report.citations,
            "completed_at": report.completed_at.isoformat() if report.completed_at else None,
        }

    @staticmethod
    def _relevant_sections(
        sections: list[dict[str, object]], fallback_content: str, query: str
    ) -> str:
        """按标题和正文关键词选择报告章节，避免整份报告挤占项目原文上下文。"""
        query_tokens = {
            token.strip().lower() for token in jieba.cut_for_search(query) if len(token.strip()) > 1
        }
        ranked = sorted(
            sections,
            key=lambda section: ReportRetrievalService._section_score(section, query_tokens),
            reverse=True,
        )
        selected = [
            str(section.get("content_markdown") or "").strip()
            for section in ranked[:2]
            if str(section.get("content_markdown") or "").strip()
        ]
        return ("\n\n".join(selected) or fallback_content)[:4_000]

    @staticmethod
    def _section_score(section: dict[str, object], query_tokens: set[str]) -> int:
        haystack = f"{section.get('title', '')}\n{section.get('content_markdown', '')}".lower()
        return sum(token in haystack for token in query_tokens)
