"""工作台的授权聚合只读用例。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analysis.models import ProjectAnalysisRun
from app.modules.documents.models import DocumentVersion, ProjectDocument
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.reports.models import ProjectReport
from app.modules.requirements.repository import RequirementRepository
from app.modules.risks.models import ProjectRisk


class DashboardService:
    """聚合工作台展示数据；只读查询不触发解析、分析或状态修复。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._requirements = RequirementRepository(session)

    async def summary(self, actor: AuthenticatedUser) -> dict[str, object]:
        projects = await ProjectService(self._session).list_visible(actor)
        project_ids = [item.id for item in projects]
        if not project_ids:
            return {"stats": self._stats(0, 0, 0, 0), "projects": [], "tasks": []}
        ready_documents = await self._count_ready_documents(project_ids)
        pending_risks = await self._count_pending_risks(project_ids)
        completed_reports = await self._count_ready_reports(project_ids)
        phases = await self._project_phases(project_ids)
        tasks = []
        pending_requirements = await self._requirements.count_pending_by_projects(project_ids)
        names = {item.id: item.name for item in projects}
        for project_id, count in pending_requirements:
            tasks.append(
                {
                    "id": f"review-{project_id}",
                    "type": "match",
                    "project_id": str(project_id),
                    "title": "需求待复核",
                    "description": f"「{names[project_id]}」有 {count} 条关键需求待确认",
                    "action_text": "进入复核",
                }
            )
        pending_risk_rows = await self._session.execute(
            select(ProjectRisk.project_id, func.count())
            .where(ProjectRisk.project_id.in_(project_ids), ProjectRisk.status == "OPEN")
            .group_by(ProjectRisk.project_id)
        )
        for project_id, count in pending_risk_rows.all():
            tasks.append(
                {
                    "id": f"risk-{project_id}",
                    "type": "risk",
                    "project_id": str(project_id),
                    "title": "风险待处理",
                    "description": f"「{names[project_id]}」有 {count} 条风险待处理",
                    "action_text": "查看项目",
                }
            )
        return {
            "stats": self._stats(
                sum(item.status == "ACTIVE" for item in projects),
                pending_risks,
                completed_reports,
                ready_documents,
            ),
            "projects": [
                {
                    **item.model_dump(mode="json"),
                    "phase": (
                        {"code": "ARCHIVED", "label": "项目已归档"}
                        if item.status == "ARCHIVED"
                        else phases.get(item.id, {"code": "NO_DOCUMENT", "label": "待上传招标文件"})
                    ),
                }
                for item in projects[:10]
            ],
            "tasks": tasks[:20],
        }

    async def status_projections(self, actor: AuthenticatedUser) -> list[dict[str, object]]:
        """返回项目列表和工作台共用的只读业务阶段投影。"""
        projects = await ProjectService(self._session).list_visible(actor)
        phases = await self._project_phases([item.id for item in projects])
        return [
            {
                "project_id": str(item.id),
                "phase": (
                    {"code": "ARCHIVED", "label": "项目已归档"}
                    if item.status == "ARCHIVED"
                    else phases.get(item.id, {"code": "NO_DOCUMENT", "label": "待上传招标文件"})
                ),
            }
            for item in projects
        ]

    @staticmethod
    def _stats(active: int, risks: int, reports: int, documents: int) -> dict[str, int]:
        return {
            "active_projects": active,
            "pending_risks": risks,
            "completed_reports": reports,
            "documents": documents,
        }

    async def _count_ready_documents(self, project_ids) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                # 两张表之间存在双向外键（版本归属 + 文档当前版本），必须显式指定按归属关系连接。
                .join(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
                .where(
                    ProjectDocument.project_id.in_(project_ids),
                    DocumentVersion.parse_status == "READY",
                )
            )
            or 0
        )

    async def _project_phases(self, project_ids) -> dict:
        document_rows = await self._session.execute(
            select(ProjectDocument.project_id, DocumentVersion.parse_status)
            .join(DocumentVersion, DocumentVersion.id == ProjectDocument.current_version_id)
            .where(ProjectDocument.project_id.in_(project_ids))
        )
        statuses: dict[object, set[str]] = {project_id: set() for project_id in project_ids}
        for project_id, parse_status in document_rows.all():
            statuses[project_id].add(parse_status)
        analysis_rows = await self._session.execute(
            select(
                ProjectAnalysisRun.project_id,
                ProjectAnalysisRun.status,
                ProjectAnalysisRun.current_stage,
            )
            .where(ProjectAnalysisRun.project_id.in_(project_ids))
            .order_by(ProjectAnalysisRun.project_id, ProjectAnalysisRun.created_at.desc())
        )
        analysis = {}
        for project_id, status, stage in analysis_rows.all():
            analysis.setdefault(project_id, (status, stage))
        report_rows = await self._session.execute(
            select(ProjectReport.project_id, ProjectReport.status, ProjectReport.is_stale).where(
                ProjectReport.project_id.in_(project_ids)
            )
        )
        reports = {project_id: (status, stale) for project_id, status, stale in report_rows.all()}
        phases = {}
        for project_id, values in statuses.items():
            if not values:
                phases[project_id] = {"code": "NO_DOCUMENT", "label": "待上传招标文件"}
            elif values & {"QUEUED", "PARSING", "CLEANING", "BUILDING_EVIDENCE", "INDEXING"}:
                phases[project_id] = {"code": "DOCUMENT_PROCESSING", "label": "招标文件解析中"}
            elif "FAILED" in values:
                phases[project_id] = {"code": "DOCUMENT_FAILED", "label": "文件解析失败，待重试"}
            elif project_id in reports:
                report_status, stale = reports[project_id]
                phases[project_id] = (
                    {"code": "REPORT_STALE", "label": "报告已过期，待重新分析"}
                    if stale
                    else {"code": "REPORT_READY", "label": "分析报告已完成"}
                    if report_status == "READY"
                    else {"code": "REPORT_GENERATING", "label": "分析报告生成中"}
                    if report_status in {"QUEUED", "GENERATING"}
                    else {"code": "REPORT_FAILED", "label": "报告生成失败，待重试"}
                )
            elif project_id in analysis:
                status, stage = analysis[project_id]
                phases[project_id] = (
                    {"code": "ANALYSIS_RUNNING", "label": f"完整分析中：{stage}"}
                    if status in {"QUEUED", "RUNNING", "REPORT_QUEUED"}
                    else {"code": "ANALYSIS_FAILED", "label": "完整分析失败，待重试"}
                    if status == "FAILED"
                    else {"code": "ANALYSIS_FINISHED", "label": "完整分析已完成，待生成报告"}
                )
            elif "READY" in values:
                phases[project_id] = {"code": "DOCUMENT_READY", "label": "文件已解析，待发起分析"}
            else:
                phases[project_id] = {"code": "DOCUMENT_UPLOADED", "label": "文件已上传，待解析"}
        return phases

    async def _count_pending_risks(self, project_ids) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(ProjectRisk)
                .where(ProjectRisk.project_id.in_(project_ids), ProjectRisk.status == "OPEN")
            )
            or 0
        )

    async def _count_ready_reports(self, project_ids) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(ProjectReport)
                .where(
                    ProjectReport.project_id.in_(project_ids),
                    ProjectReport.status == "READY",
                    ProjectReport.is_stale.is_(False),
                )
            )
            or 0
        )
