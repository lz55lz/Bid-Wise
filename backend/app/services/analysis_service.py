import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_write_project_documents
from app.db.models import AnalysisRun, AnalysisSnapshot
from app.db.repositories.analysis_repository import AnalysisRepository
from app.db.repositories.identity_repository import IdentityRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.db.session import get_session_factory
from app.integrations.object_storage import MinioObjectStorage
from app.integrations.task_publisher import ArqTaskPublisher
from app.schemas.analysis import AnalysisRunResponse
from app.schemas.documents import TaskResponse
from app.services.project_service import ProjectService
from app.services.task_service import TaskService


class AnalysisService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._runs = AnalysisRepository(session)
        self._requirements = RequirementRepository(session)
        self._projects = ProjectService(session)

    def submit(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str], publisher
    ) -> TaskResponse:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        if not can_write_project_documents(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权发起项目投标分析", 403)
        if self._requirements.has_pending_for_project(project_id):
            raise DomainError(
                "ANALYSIS_REVIEW_PENDING",
                "请先完成需求复核中的所有高优先需求，再发起匹配分析。",
                409,
            )
        manifest = self._runs.build_input_manifest(project_id)
        if not manifest["tender_versions"]:
            raise DomainError("ANALYSIS_INPUT_NOT_READY", "请先完成至少一份招标文件解析", 409)
        if not manifest["requirements"]:
            raise DomainError(
                "ANALYSIS_INPUT_NOT_READY",
                "请先在需求复核中确认至少一条可匹配的招标 Requirement",
                409,
            )
        tender_ids = [item["id"] for item in manifest["tender_versions"]]
        material_ids = [item["id"] for item in manifest["materials"]]
        rule_ids = [item["id"] for item in manifest["rules"]]
        input_hash = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        now = datetime.now(UTC)
        run = AnalysisRun(
            id=uuid4(),
            project_id=project_id,
            status="QUEUED",
            current_stage="SNAPSHOT",
            input_hash=input_hash,
            task_id=None,
            report_id=None,
            error_code=None,
            error_message=None,
            started_at=None,
            completed_at=None,
            created_at=now,
            created_by=actor_id,
        )
        self._runs.add_run(run)
        self._runs.add_snapshot(
            AnalysisSnapshot(
                id=uuid4(),
                analysis_run_id=run.id,
                input_hash=input_hash,
                tender_version_ids=tender_ids,
                enterprise_material_ids=material_ids,
                rule_version_ids=rule_ids,
                input_manifest=manifest,
                stage_outputs={"SNAPSHOT": {"input_hash": input_hash}},
                created_at=now,
            )
        )
        task = TaskService(self._session).create_project_analysis_task(run)
        # Task.target_id is intentionally a generic UUID rather than an ORM
        # relationship, so SQLAlchemy cannot infer the order required by the
        # analysis_runs.task_id foreign key. Persist the task explicitly first.
        self._session.flush([task])
        run.task_id = task.id
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "ux_analysis_runs_one_active_per_project":
                raise DomainError(
                    "ANALYSIS_ALREADY_RUNNING", "该项目已有正在执行的分析", 409
                ) from exc
            raise
        try:
            task.celery_task_id = publisher.publish_run_project_analysis(task.id, run.id)
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            run = self._runs.get_run(run.id, for_update=True)
            if run:
                run.status, run.current_stage = "FAILED", "DISPATCH"
                run.error_code, run.error_message = (
                    "TASK_QUEUE_UNAVAILABLE",
                    "项目分析任务队列暂不可用。",
                )
                run.completed_at = datetime.now(UTC)
                self._session.commit()
            raise DomainError("TASK_QUEUE_UNAVAILABLE", "项目分析任务队列暂不可用", 503) from exc
        return TaskResponse(
            id=task.id,
            task_type=task.task_type,
            target_type=task.target_type,
            target_id=task.target_id,
            status=task.status,
            attempt=task.attempt,
            error_code=task.error_code,
            error_message=task.error_message,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    def list(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[AnalysisRunResponse]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        return [
            self._response(run, include_snapshot=False) for run in self._runs.list_runs(project_id)
        ]

    def get(self, run_id: UUID, actor_id: UUID, role_codes: set[str]) -> AnalysisRunResponse:
        run = self._runs.get_run(run_id)
        if run is None:
            raise DomainError("RESOURCE_NOT_FOUND", "分析运行不存在", 404)
        self._projects.get_visible(run.project_id, actor_id, role_codes)
        return self._response(run, include_snapshot=True)

    @staticmethod
    def execute(task_id: UUID, run_id: UUID) -> None:
        """Execute ordered project analysis stages from an immutable input manifest."""
        from app.core.config import get_settings
        from app.services.decision_service import DecisionService
        from app.services.matching_service import MatchingService
        from app.services.report_service import ReportService
        from app.services.risk_service import RiskService

        session = get_session_factory()()
        try:
            tasks = TaskService(session)
            task = tasks.start_project_analysis_task(task_id)
            if task is None:
                return
            repo = AnalysisRepository(session)
            run = repo.get_run(run_id, for_update=True)
            if run is None or run.task_id != task_id:
                tasks.fail_project_analysis_task(
                    task_id, "ANALYSIS_RUN_NOT_FOUND", "分析运行不存在或任务不匹配。"
                )
                return
            actor_id = run.created_by
            roles = IdentityRepository(session).list_role_codes(actor_id)
            snapshot = repo.get_snapshot(run.id)
            assert snapshot is not None
            current_manifest = repo.build_input_manifest(run.project_id)
            current_hash = hashlib.sha256(
                json.dumps(current_manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if current_hash != snapshot.input_hash:
                raise DomainError(
                    "ANALYSIS_INPUT_CHANGED",
                    "分析输入已变更，请基于最新文件、材料或规则重新发起分析。",
                    409,
                )
            run.status, run.current_stage, run.started_at = (
                "RUNNING",
                "MATCHING",
                datetime.now(UTC),
            )
            session.commit()

            matches = MatchingService(session).run(run.project_id, actor_id, roles)
            snapshot.stage_outputs = {
                **snapshot.stage_outputs,
                "MATCHING": {"status": "SUCCEEDED", "result_count": len(matches)},
            }
            run.current_stage = "RISK_CHECK"
            session.commit()

            risks = RiskService(session).run(run.project_id, actor_id, roles)
            snapshot.stage_outputs = {
                **snapshot.stage_outputs,
                "RISK_CHECK": {"status": "SUCCEEDED", "result_count": len(risks)},
            }
            run.current_stage = "DECISION"
            session.commit()

            decision = DecisionService(session).generate(run.project_id, actor_id, roles)
            snapshot.stage_outputs = {
                **snapshot.stage_outputs,
                "DECISION": {
                    "status": "SUCCEEDED",
                    "decision_id": str(decision.id),
                    "suggestion": decision.suggestion,
                },
            }
            run.current_stage = "REPORT_QUEUED"
            session.commit()

            report_task = ReportService(session, MinioObjectStorage(get_settings())).submit(
                run.project_id,
                actor_id,
                roles,
                ArqTaskPublisher(),
                report_type="FULL",
                analysis_run_id=run.id,
            )
            run.report_id = report_task.target_id
            run.current_stage = "REPORT_GENERATING"
            snapshot.stage_outputs = {
                **snapshot.stage_outputs,
                "REPORT": {
                    "status": "QUEUED",
                    "report_id": str(report_task.target_id),
                    "task_id": str(report_task.id),
                },
            }
            session.commit()
            tasks.complete_project_analysis_task(task_id, "项目分析已完成，正式报告正在生成。")
        except Exception as exc:
            session.rollback()
            run = AnalysisRepository(session).get_run(run_id, for_update=True)
            if run is not None:
                run.status, run.current_stage = "FAILED", "FAILED"
                run.error_code, run.error_message, run.completed_at = (
                    "ANALYSIS_FAILED",
                    str(exc)[:1000],
                    datetime.now(UTC),
                )
                session.commit()
            TaskService(session).fail_project_analysis_task(
                task_id, "ANALYSIS_FAILED", "项目分析失败，请查看任务详情。"
            )
            raise
        finally:
            session.close()

    def _response(self, run: AnalysisRun, *, include_snapshot: bool) -> AnalysisRunResponse:
        snapshot = self._runs.get_snapshot(run.id) if include_snapshot else None
        return AnalysisRunResponse(
            id=run.id,
            project_id=run.project_id,
            status=run.status,
            current_stage=run.current_stage,
            task_id=run.task_id,
            report_id=run.report_id,
            error_code=run.error_code,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            snapshot=None
            if snapshot is None
            else {
                "tender_version_ids": snapshot.tender_version_ids,
                "enterprise_material_ids": snapshot.enterprise_material_ids,
                "rule_version_ids": snapshot.rule_version_ids,
                "input_manifest": snapshot.input_manifest,
                "stage_outputs": snapshot.stage_outputs,
            },
        )

    @staticmethod
    def mark_report_terminal(
        report_id: UUID, *, succeeded: bool, error_message: str | None = None
    ) -> None:
        session = get_session_factory()()
        try:
            from app.db.models import Report

            report = session.get(Report, report_id)
            if report is None or report.analysis_run_id is None:
                return
            run = AnalysisRepository(session).get_run(report.analysis_run_id, for_update=True)
            if run is None:
                return
            completed_at = datetime.now(UTC)
            run.status = "SUCCEEDED" if succeeded else "FAILED"
            run.current_stage = "REPORT" if succeeded else "REPORT_FAILED"
            run.completed_at = completed_at
            run.error_code = None if succeeded else "REPORT_GENERATION_FAILED"
            run.error_message = error_message
            snapshot = AnalysisRepository(session).get_snapshot(run.id)
            if snapshot is not None:
                stage_outputs = dict(snapshot.stage_outputs or {})
                report_output = dict(stage_outputs.get("REPORT") or {})
                stage_outputs["REPORT"] = {
                    **report_output,
                    "status": "SUCCEEDED" if succeeded else "FAILED",
                    "report_id": str(report_id),
                    "completed_at": completed_at.isoformat(),
                    "error_message": None if succeeded else error_message,
                }
                snapshot.stage_outputs = stage_outputs
            session.commit()
        finally:
            session.close()
