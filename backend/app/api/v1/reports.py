"""项目报告 HTTP 接口。"""

from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.reports.export_service import ReportExportService
from app.modules.reports.schemas import ProjectReportResponse
from app.modules.reports.service import ProjectReportService

router = APIRouter(prefix="/projects/{project_id}/reports", tags=["项目报告"])


@router.post("", response_model=ProjectReportResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_project_report(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    report_type: str = Query(default="SIMPLE", pattern="^(SIMPLE|FULL)$"),
) -> ProjectReportResponse:
    """根据已确认发现项提交一份不可变 Markdown 报告。"""
    return await ProjectReportService(session, settings).submit(
        project_id, current_user, report_type=report_type
    )


@router.get("/latest", response_model=ProjectReportResponse | None)
async def get_latest_project_report(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ProjectReportResponse | None:
    """读取项目最近一份已完成报告。"""
    return await ProjectReportService(session, settings).latest(project_id, current_user)


@router.get("/{report_id}", response_model=ProjectReportResponse)
async def get_project_report(
    project_id: UUID,
    report_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ProjectReportResponse:
    """按项目范围读取指定报告。"""
    return await ProjectReportService(session, settings).get(project_id, report_id, current_user)


@router.get("/{report_id}/download")
async def download_project_report(
    project_id: UUID,
    report_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    report_format: str = Query(alias="format", pattern="^(md|docx|pdf)$"),
) -> Response:
    """导出同一冻结报告的 Markdown、DOCX 或 PDF，不重新调用模型。"""
    export = await ReportExportService(session, settings).export(
        project_id, report_id, current_user, report_format
    )
    return Response(
        content=export.content,
        media_type=export.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename=report-{report_id}.{export.extension}"
        },
    )
