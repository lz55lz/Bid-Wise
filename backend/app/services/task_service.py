from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import AgentRun, AnalysisRun, DocumentVersion, Report, Task, TaskEvent
from app.db.repositories.task_repository import TaskRepository

PARSE_DOCUMENT = "PARSE_DOCUMENT"
CLEAN_DOCUMENT = "CLEAN_DOCUMENT"
INDEX_DOCUMENT = "INDEX_DOCUMENT"
EXTRACT_REQUIREMENTS = "EXTRACT_REQUIREMENTS"
PIPELINE_DOCUMENT = "PIPELINE_DOCUMENT"
RUN_MATCH = "RUN_MATCH"
RUN_RISK_CHECK = "RUN_RISK_CHECK"
RUN_DECISION = "GENERATE_DECISION"
RUN_REPORT = "GENERATE_REPORT"
RUN_PROJECT_ANALYSIS = "RUN_PROJECT_ANALYSIS"
RUN_BID_READINESS_AGENT = "RUN_BID_READINESS_AGENT"
DOCUMENT_VERSION_TARGET = "DOCUMENT_VERSION"
PROJECT_TARGET = "PROJECT"
REPORT_TARGET = "REPORT"
AGENT_RUN_TARGET = "AGENT_RUN"
ANALYSIS_RUN_TARGET = "ANALYSIS_RUN"


class RetryableDocumentTaskError(RuntimeError):
    """Signals the task queue to retry work whose database state was safely re-queued."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = TaskRepository(session)

    def create_parse_task(
        self,
        document_version: DocumentVersion,
        actor_id: UUID,
        parent_task_id: UUID | None = None,
    ) -> Task:
        idempotency_key = str(document_version.id)
        task = Task(
            id=uuid4(),
            task_type=PARSE_DOCUMENT,
            target_type=DOCUMENT_VERSION_TARGET,
            target_id=document_version.id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(PARSE_DOCUMENT, idempotency_key),
            parent_task_id=parent_task_id,
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message="解析任务已进入队列",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def create_pipeline_task(
        self,
        document_version: DocumentVersion,
        project_id: UUID,
        actor_id: UUID,
    ) -> Task:
        """LangGraph 流水线任务（pipeline 包含 parse/clean/index/extract/risk/match）。"""
        idempotency_key = f"pipeline:{document_version.id}"
        task = Task(
            id=uuid4(),
            task_type=PIPELINE_DOCUMENT,
            target_type=DOCUMENT_VERSION_TARGET,
            target_id=document_version.id,
            idempotency_key=idempotency_key,
            status="RUNNING",
            attempt=self._tasks.next_attempt(PIPELINE_DOCUMENT, idempotency_key),
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="RUNNING",
                message="LangGraph 流水线任务已启动",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def create_index_task(
        self, document_version: DocumentVersion, parent_task_id: UUID | None = None
    ) -> Task:
        idempotency_key = f"index:{document_version.id}"
        task = Task(
            id=uuid4(),
            task_type=INDEX_DOCUMENT,
            target_type=DOCUMENT_VERSION_TARGET,
            target_id=document_version.id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(INDEX_DOCUMENT, idempotency_key),
            parent_task_id=parent_task_id,
            created_at=datetime.now(UTC),
            created_by=document_version.created_by,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message="文档向量索引任务已进入队列。",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def create_clean_task(
        self, document_version: DocumentVersion, parent_task_id: UUID | None = None
    ) -> Task:
        return self._create_document_task(
            CLEAN_DOCUMENT,
            f"clean:{document_version.id}",
            document_version,
            "Document cleaning task queued.",
            parent_task_id=parent_task_id,
        )

    def create_extraction_task(
        self, document_version: DocumentVersion, parent_task_id: UUID | None = None
    ) -> Task:
        idempotency_key = f"extract:{document_version.id}:v1"
        return self._create_document_task(
            EXTRACT_REQUIREMENTS,
            idempotency_key,
            document_version,
            "Requirement extraction task queued.",
            parent_task_id=parent_task_id,
        )

    def create_match_task(self, project_id: UUID, actor_id: UUID, state_hash: str) -> Task:
        return self._create_project_task(
            RUN_MATCH,
            f"match:{project_id}:{state_hash}",
            project_id,
            actor_id,
            "材料匹配任务已进入队列。",
        )

    def create_risk_check_task(self, project_id: UUID, actor_id: UUID, state_hash: str) -> Task:
        return self._create_project_task(
            RUN_RISK_CHECK,
            f"risk-check:{project_id}:{state_hash}",
            project_id,
            actor_id,
            "风险检查任务已进入队列。",
        )

    def create_decision_task(self, project_id: UUID, actor_id: UUID, state_hash: str) -> Task:
        return self._create_project_task(
            RUN_DECISION,
            f"decision:{project_id}:{state_hash}",
            project_id,
            actor_id,
            "投标建议生成任务已进入队列。",
        )

    def create_report_task(self, report: Report, actor_id: UUID) -> Task:
        task = Task(
            id=uuid4(),
            task_type=RUN_REPORT,
            target_type=REPORT_TARGET,
            target_id=report.id,
            idempotency_key=f"report:{report.id}",
            status="QUEUED",
            attempt=self._tasks.next_attempt(RUN_REPORT, f"report:{report.id}"),
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message="报告生成任务已进入队列。",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def create_project_analysis_task(self, run: AnalysisRun) -> Task:
        idempotency_key = f"project-analysis:{run.id}"
        task = Task(
            id=uuid4(),
            task_type=RUN_PROJECT_ANALYSIS,
            target_type=ANALYSIS_RUN_TARGET,
            target_id=run.id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(RUN_PROJECT_ANALYSIS, idempotency_key),
            created_at=datetime.now(UTC),
            created_by=run.created_by,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message="项目投标分析工作流已进入队列。",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def start_project_analysis_task(self, task_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_PROJECT_ANALYSIS
            or task.target_type != ANALYSIS_RUN_TARGET
            or task.status != "QUEUED"
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        task.status, task.started_at = "RUNNING", now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="RUNNING",
                message="Worker 已开始执行项目投标分析工作流。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_project_analysis_task(
        self, task_id: UUID, message: str, *, error_code: str | None = None
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != RUN_PROJECT_ANALYSIS or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status, task.completed_at, task.error_code = "SUCCEEDED", now, error_code
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def wait_for_project_analysis_review(self, task_id: UUID, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != RUN_PROJECT_ANALYSIS or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "WAITING_HUMAN_REVIEW"
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="WAITING_HUMAN_REVIEW",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def resume_project_analysis_task(self, task_id: UUID, message: str) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_PROJECT_ANALYSIS
            or task.status != "WAITING_HUMAN_REVIEW"
        ):
            self._session.rollback()
            return None
        task.status, task.error_code, task.error_message = "QUEUED", None, None
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="WAITING_HUMAN_REVIEW",
                to_status="QUEUED",
                message=message,
                created_at=datetime.now(UTC),
            )
        )
        self._session.commit()
        return task

    def cancel_project_analysis_task(self, task_id: UUID, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_PROJECT_ANALYSIS
            or task.status != "WAITING_HUMAN_REVIEW"
        ):
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status, task.completed_at = "CANCELLED", now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="WAITING_HUMAN_REVIEW",
                to_status="CANCELLED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def fail_project_analysis_task(self, task_id: UUID, error_code: str, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_PROJECT_ANALYSIS
            or task.status not in {"QUEUED", "RUNNING"}
        ):
            self._session.rollback()
            return
        now = datetime.now(UTC)
        previous = task.status
        task.status, task.error_code, task.error_message, task.completed_at = (
            "FAILED",
            error_code,
            message,
            now,
        )
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous,
                to_status="FAILED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def create_bid_readiness_agent_task(self, run: AgentRun) -> Task:
        idempotency_key = f"bid-readiness:{run.id}"
        task = Task(
            id=uuid4(),
            task_type=RUN_BID_READINESS_AGENT,
            target_type=AGENT_RUN_TARGET,
            target_id=run.id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(RUN_BID_READINESS_AGENT, idempotency_key),
            created_at=datetime.now(UTC),
            created_by=run.created_by,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message="投标研判多 Agent 任务已进入队列。",
                created_at=datetime.now(UTC),
            )
        )
        return task

    def start_bid_readiness_agent_task(self, task_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_BID_READINESS_AGENT
            or task.target_type != AGENT_RUN_TARGET
            or task.status != "QUEUED"
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        task.status = "RUNNING"
        task.started_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="RUNNING",
                message="Worker 已开始执行投标研判多 Agent 工作流。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_bid_readiness_agent_task(self, task_id: UUID, message: str) -> None:
        self._complete_agent_task(task_id, "SUCCEEDED", message)

    def fail_bid_readiness_agent_task(self, task_id: UUID, error_code: str, message: str) -> None:
        self._complete_agent_task(task_id, "FAILED", message, error_code)

    def mark_bid_readiness_dispatch_failure(self, task_id: UUID, run_id: UUID) -> None:
        task = self._tasks.get_for_update(task_id)
        run = self._session.get(AgentRun, run_id, with_for_update=True)
        if task is None or run is None or task.status != "QUEUED":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "FAILED"
        task.error_code = "TASK_QUEUE_UNAVAILABLE"
        task.error_message = "投标研判任务未能投递到队列。"
        task.completed_at = now
        run.status = "FAILED"
        run.error_code = task.error_code
        run.error_message = task.error_message
        run.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="FAILED",
                message=task.error_message,
                created_at=now,
            )
        )
        self._session.commit()

    def record_celery_task_id(self, task_id: UUID, celery_task_id: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.status != "QUEUED":
            self._session.rollback()
            return
        task.celery_task_id = celery_task_id
        self._session.commit()

    def _complete_agent_task(
        self, task_id: UUID, status: str, message: str, error_code: str | None = None
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_BID_READINESS_AGENT
            or task.target_type != AGENT_RUN_TARGET
            or task.status != "RUNNING"
        ):
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = status
        task.error_code = error_code
        task.error_message = message if error_code else None
        task.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status=status,
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def _create_project_task(
        self,
        task_type: str,
        idempotency_key: str,
        project_id: UUID,
        actor_id: UUID,
        message: str,
    ) -> Task:
        existing = self._tasks.latest_for_idempotency_key(task_type, idempotency_key)
        if existing is not None and existing.status in {"QUEUED", "RUNNING", "SUCCEEDED"}:
            return existing
        task = Task(
            id=uuid4(),
            task_type=task_type,
            target_type=PROJECT_TARGET,
            target_id=project_id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(task_type, idempotency_key),
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message=message,
                created_at=datetime.now(UTC),
            )
        )
        return task

    def _create_document_task(
        self,
        task_type: str,
        idempotency_key: str,
        version: DocumentVersion,
        message: str,
        parent_task_id: UUID | None = None,
    ) -> Task:
        task = Task(
            id=uuid4(),
            task_type=task_type,
            target_type=DOCUMENT_VERSION_TARGET,
            target_id=version.id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempt=self._tasks.next_attempt(task_type, idempotency_key),
            parent_task_id=parent_task_id,
            created_at=datetime.now(UTC),
            created_by=version.created_by,
        )
        self._tasks.add(task)
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=None,
                to_status="QUEUED",
                message=message,
                created_at=datetime.now(UTC),
            )
        )
        return task

    def mark_dispatch_failure(self, task_id: UUID, version_id: UUID) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.status != "QUEUED":
            return
        version = self._session.get(DocumentVersion, version_id)
        if version is None:
            return
        now = datetime.now(UTC)
        task.status = "FAILED"
        task.error_code = "TASK_QUEUE_UNAVAILABLE"
        task.error_message = "任务队列暂不可用，请稍后重新执行。"
        task.completed_at = now
        version.parse_status = "FAILED"
        version.error_code = task.error_code
        version.error_message = task.error_message
        version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="FAILED",
                message=task.error_message,
                created_at=now,
            )
        )
        self._session.commit()

    def start_parse(self, task_id: UUID, version_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.target_type != DOCUMENT_VERSION_TARGET
            or task.target_id != version_id
            or task.status not in {"QUEUED", "FAILED"}
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "RUNNING"
        task.started_at = now
        version.parse_status = "PARSING"
        version.error_code = None
        version.error_message = None
        if previous_status == "FAILED":
            task.attempt += 1
            task.error_code = None
            task.error_message = None
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="RUNNING",
                message="Worker 已开始调用 MinerU",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_parse(
        self,
        task_id: UUID,
        version_id: UUID,
        parse_output_key: str,
        nodes_added: int,
        requires_followup_tasks: bool = True,
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if task is None or version is None or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.completed_at = now
        version.parse_status = "STRUCTURING" if requires_followup_tasks else "READY"
        version.parse_output_key = parse_output_key
        version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=(
                    f"已保存 {nodes_added} 个原始解析节点，等待清洗任务。"
                    if requires_followup_tasks
                    else f"已保存 {nodes_added} 个可引用解析节点。"
                ),
                created_at=now,
            )
        )
        self._session.commit()

    def mark_structuring(self, version_id: UUID) -> None:
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if version is None:
            raise ValueError("document version not found")
        version.parse_status = "STRUCTURING"

    def start_clean(self, task_id: UUID, version_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.task_type != CLEAN_DOCUMENT
            or task.target_type != DOCUMENT_VERSION_TARGET
            or task.target_id != version_id
            or task.status not in {"QUEUED", "FAILED"}
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "RUNNING"
        task.started_at = now
        version.parse_status = "CLEANING"
        version.error_code = None
        version.error_message = None
        if previous_status == "FAILED":
            task.attempt += 1
            task.error_code = None
            task.error_message = None
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="RUNNING",
                message="Worker 正在清洗解析文本并校验质量。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_clean(
        self,
        task_id: UUID,
        version_id: UUID,
        summary: dict[str, object],
        *,
        requires_followup_tasks: bool,
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if task is None or version is None or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status, task.completed_at = "SUCCEEDED", now
        version.cleaning_summary = summary
        version.parse_status = "STRUCTURING" if requires_followup_tasks else "READY"
        if version.parse_status == "READY":
            version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=(
                    f"清洗完成，可索引节点 {summary.get('indexable_nodes', 0)} 个，等待后续任务。"
                    if requires_followup_tasks
                    else f"清洗完成，可用节点 {summary.get('indexable_nodes', 0)} 个。"
                ),
                created_at=now,
            )
        )
        self._session.commit()

    def start_index(self, task_id: UUID, version_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.task_type != INDEX_DOCUMENT
            or task.target_type != DOCUMENT_VERSION_TARGET
            or task.target_id != version_id
            or task.status not in {"QUEUED", "FAILED"}
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "RUNNING"
        task.started_at = now
        if previous_status == "FAILED":
            task.attempt += 1
            task.error_code = None
            task.error_message = None
        version.parse_status = "INDEXING"
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="RUNNING",
                message="Worker 已开始生成 bge-m3 向量索引。"
                if previous_status == "QUEUED"
                else f"重试第 {task.attempt + 1} 次索引。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def start_extraction(self, task_id: UUID, version_id: UUID) -> Task | None:
        return self._start_document_task(
            task_id, version_id, EXTRACT_REQUIREMENTS, "Worker 已开始抽取 Requirement 候选。"
        )

    def _start_document_task(
        self, task_id: UUID, version_id: UUID, task_type: str, message: str
    ) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.task_type != task_type
            or task.target_id != version_id
            or task.status not in {"QUEUED", "FAILED"}
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "RUNNING"
        task.started_at = now
        if previous_status == "FAILED":
            task.attempt += 1
            task.error_code = None
            task.error_message = None
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="RUNNING",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_index(
        self,
        task_id: UUID,
        version_id: UUID,
        chunks_indexed: int,
        *,
        requires_extraction: bool = True,
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if task is None or version is None or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.completed_at = now
        extraction = self._tasks.latest_for_target(EXTRACT_REQUIREMENTS, version_id)
        if not requires_extraction or (extraction and extraction.status == "SUCCEEDED"):
            version.parse_status = "READY"
        elif extraction and extraction.status == "FAILED":
            version.parse_status = "FAILED"
        else:
            version.parse_status = "STRUCTURING"
        if version.parse_status == "READY":
            version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=f"已写入 {chunks_indexed} 个可重建向量片段。",
                created_at=now,
            )
        )
        self._session.commit()

    def complete_extraction(self, task_id: UUID, version_id: UUID, persisted: int) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if task is None or version is None or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status, task.completed_at = "SUCCEEDED", now
        indexed = self._tasks.latest_for_target(INDEX_DOCUMENT, version_id)
        if indexed and indexed.status == "SUCCEEDED":
            version.parse_status = "READY"
        elif indexed and indexed.status == "FAILED":
            version.parse_status = "FAILED"
        else:
            version.parse_status = "STRUCTURING"
        if version.parse_status == "READY":
            version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=f"已保存 {persisted} 条带 Evidence 的 Requirement 候选。",
                created_at=now,
            )
        )
        self._session.commit()

    def fail_index(self, task_id: UUID, version_id: UUID, error_code: str, message: str) -> None:
        self._fail_document_task(task_id, version_id, INDEX_DOCUMENT, error_code, message)

    def fail_clean(self, task_id: UUID, version_id: UUID, error_code: str, message: str) -> None:
        self._fail_document_task(task_id, version_id, CLEAN_DOCUMENT, error_code, message)

    def fail_extraction(
        self, task_id: UUID, version_id: UUID, error_code: str, message: str
    ) -> None:
        self._fail_document_task(task_id, version_id, EXTRACT_REQUIREMENTS, error_code, message)

    def retry_parse_or_fail(
        self, task_id: UUID, version_id: UUID, error_code: str, message: str, max_attempts: int = 3
    ) -> bool:
        return self._retry_document_task_or_fail(
            task_id, version_id, PARSE_DOCUMENT, error_code, message, max_attempts
        )

    def retry_index_or_fail(
        self, task_id: UUID, version_id: UUID, error_code: str, message: str, max_attempts: int = 3
    ) -> bool:
        return self._retry_document_task_or_fail(
            task_id, version_id, INDEX_DOCUMENT, error_code, message, max_attempts
        )

    def retry_clean_or_fail(
        self, task_id: UUID, version_id: UUID, error_code: str, message: str, max_attempts: int = 3
    ) -> bool:
        return self._retry_document_task_or_fail(
            task_id, version_id, CLEAN_DOCUMENT, error_code, message, max_attempts
        )

    def retry_extraction_or_fail(
        self, task_id: UUID, version_id: UUID, error_code: str, message: str, max_attempts: int = 3
    ) -> bool:
        return self._retry_document_task_or_fail(
            task_id, version_id, EXTRACT_REQUIREMENTS, error_code, message, max_attempts
        )

    def _retry_document_task_or_fail(
        self,
        task_id: UUID,
        version_id: UUID,
        task_type: str,
        error_code: str,
        message: str,
        max_attempts: int,
    ) -> bool:
        """Re-queue a recoverable document task, preserving all task history."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.task_type != task_type
            or task.target_id != version_id
            or task.status not in {"QUEUED", "RUNNING"}
        ):
            self._session.rollback()
            return False
        if task.attempt >= max_attempts:
            self._fail_document_task(task_id, version_id, task_type, error_code, message)
            return False

        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "QUEUED"
        task.attempt += 1
        task.error_code = error_code
        task.error_message = message
        task.started_at = None
        task.completed_at = None
        version.parse_status = "QUEUED" if task_type == PARSE_DOCUMENT else "STRUCTURING"
        version.error_code = None
        version.error_message = None
        version.completed_at = None
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="QUEUED",
                message=f"{message} 将按退避策略进行第 {task.attempt} 次尝试。",
                created_at=now,
            )
        )
        self._session.commit()
        return True

    def _fail_document_task(
        self, task_id: UUID, version_id: UUID, task_type: str, error_code: str, message: str
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if (
            task is None
            or version is None
            or task.task_type != task_type
            or task.status not in {"QUEUED", "RUNNING"}
        ):
            self._session.rollback()
            return
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "FAILED"
        task.error_code = error_code
        task.error_message = message
        task.completed_at = now
        version.parse_status = "FAILED"
        version.error_code = error_code
        version.error_message = message
        version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="FAILED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def fail_parse(self, task_id: UUID, version_id: UUID, error_code: str, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        version = self._session.get(DocumentVersion, version_id, with_for_update=True)
        if task is None or version is None or task.status not in {"QUEUED", "RUNNING"}:
            self._session.rollback()
            return
        now = datetime.now(UTC)
        old_status = task.status
        task.status = "FAILED"
        task.error_code = error_code
        task.error_message = message
        task.completed_at = now
        version.parse_status = "FAILED"
        version.error_code = error_code
        version.error_message = message
        version.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=old_status,
                to_status="FAILED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def start_project_task(self, task_id: UUID, task_type: str) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != task_type
            or task.target_type != PROJECT_TARGET
            or task.status != "QUEUED"
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        task.status = "RUNNING"
        task.started_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="RUNNING",
                message="Worker 已开始执行项目任务。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_project_task(self, task_id: UUID, task_type: str, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != task_type or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def fail_project_task(
        self, task_id: UUID, task_type: str, error_code: str, message: str
    ) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != task_type or task.status not in {"QUEUED", "RUNNING"}:
            self._session.rollback()
            return
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "FAILED"
        task.error_code = error_code
        task.error_message = message
        task.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="FAILED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def start_report_task(self, task_id: UUID) -> Task | None:
        task = self._tasks.get_for_update(task_id)
        if (
            task is None
            or task.task_type != RUN_REPORT
            or task.target_type != REPORT_TARGET
            or task.status != "QUEUED"
        ):
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        task.status = "RUNNING"
        task.started_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="QUEUED",
                to_status="RUNNING",
                message="Worker 已开始生成报告。",
                created_at=now,
            )
        )
        self._session.commit()
        return task

    def complete_report_task(self, task_id: UUID, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != RUN_REPORT or task.status != "RUNNING":
            self._session.rollback()
            return
        now = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status="RUNNING",
                to_status="SUCCEEDED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()

    def fail_report_task(self, task_id: UUID, error_code: str, message: str) -> None:
        task = self._tasks.get_for_update(task_id)
        if task is None or task.task_type != RUN_REPORT or task.status not in {"QUEUED", "RUNNING"}:
            self._session.rollback()
            return
        now = datetime.now(UTC)
        previous_status = task.status
        task.status = "FAILED"
        task.error_code = error_code
        task.error_message = message
        task.completed_at = now
        self._tasks.add_event(
            TaskEvent(
                task_id=task.id,
                from_status=previous_status,
                to_status="FAILED",
                message=message,
                created_at=now,
            )
        )
        self._session.commit()
