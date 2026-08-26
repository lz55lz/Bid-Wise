from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Report, ReportEvidence, ReportSection


class ReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, report_id: UUID, *, for_update: bool = False) -> Report | None:
        statement = select(Report).where(Report.id == report_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def latest_for_project(self, project_id: UUID) -> Report | None:
        return self._session.scalar(
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.version_no.desc())
            .limit(1)
        )

    def next_version_no(self, project_id: UUID) -> int:
        value = self._session.scalar(
            select(func.max(Report.version_no)).where(Report.project_id == project_id)
        )
        return int(value or 0) + 1

    def add(self, report: Report) -> None:
        self._session.add(report)

    def add_section(self, section: ReportSection) -> None:
        self._session.add(section)

    def add_evidence(self, link: ReportEvidence) -> None:
        self._session.add(link)

    def list_sections(self, report_id: UUID) -> list[ReportSection]:
        return list(
            self._session.scalars(
                select(ReportSection)
                .where(ReportSection.report_id == report_id)
                .order_by(ReportSection.order_no)
            )
        )

    def list_evidence_ids(self, report_section_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(ReportEvidence.evidence_id).where(
                    ReportEvidence.report_section_id == report_section_id
                )
            )
        )
