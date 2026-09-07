"""完整投标分析编排：匹配、风险、决策和报告始终基于同一次冻结输入。"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.analysis.models import ProjectAnalysisRun
from app.modules.analysis.state_machine import transition_full
from app.modules.decisions.service import DecisionService
from app.modules.matching.service import MatchingService
from app.modules.materials.models import EnterpriseMaterial
from app.modules.projects.models import Enterprise, ProjectEnterprise, TenderProject
from app.modules.projects.service import ProjectService
from app.modules.reports.service import ProjectReportService
from app.modules.requirements.repository import RequirementRepository
from app.modules.risks.service import RiskService
from app.modules.tender_analysis.downstream_status import update_downstream_stage_for_analysis

logger = logging.getLogger(__name__)


class FullAnalysisService:
    """用一个运行记录替代前端串联四个接口，阶段状态可审计、可恢复。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._projects = ProjectService(session)
        self._requirements = RequirementRepository(session)

    async def submit(self, project_id: UUID, actor) -> ProjectAnalysisRun:
        """人工发起一轮新的冻结分析；只有事务提交后才允许唤醒 Worker。"""
        await self._projects.require_project_management(project_id, actor)
        run = await self._prepare_authorized(project_id, actor.id, reuse_active=False)
        await self._session.commit()
        await self.publish_if_queued(run)
        return run

    async def prepare_from_pipeline(
        self, project_id: UUID, requested_by: UUID
    ) -> ProjectAnalysisRun:
        """在调用方事务中准备下游完整分析，但不提交、不接触 Redis。

        调用方先独立提交人工确认事实，再用一个短事务创建 ProjectAnalysisRun 并建立
        pipeline 关联；Redis 始终只在该短事务成功后负责唤醒 Worker。
        """
        return await self._prepare_authorized(project_id, requested_by, reuse_active=True)

    async def publish_if_queued(self, run: ProjectAnalysisRun) -> None:
        """提交事务后幂等唤醒 QUEUED 分析；Redis 不可用时由 reconciler 补投。"""
        if run.status != "QUEUED":
            return
        try:
            await ArqTaskQueue(self._settings).publish_full_analysis(str(run.id))
        except TaskQueueUnavailable:
            logger.warning(
                "ARQ 暂不可用，完整分析等待 reconciler 补投 run_id=%s",
                run.id,
            )

    async def _prepare_authorized(
        self, project_id: UUID, requested_by: UUID, *, reuse_active: bool
    ) -> ProjectAnalysisRun:
        # 只锁“创建活动分析运行”这一小段事务，避免人工审核恢复和手工启动分析
        # 同时通过 active 查询后撞唯一索引；真正的长任务执行不会持有该锁。
        project_exists = await self._session.scalar(
            select(TenderProject.id)
            .where(TenderProject.id == project_id, TenderProject.deleted_at.is_(None))
            .with_for_update()
        )
        if project_exists is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
        requirements = await self._requirements.list_confirmed(project_id)
        if not requirements:
            raise DomainError("ANALYSIS_INPUT_NOT_READY", "请先确认至少一条招标需求", 409)
        snapshot = await self._manifest(project_id, requirements)
        if not snapshot["enterprise_ids"]:
            raise DomainError("PROJECT_ENTERPRISE_REQUIRED", "项目尚未绑定投标企业", 409)
        input_hash = self._manifest_hash(snapshot)
        active = await self._session.scalar(
            select(ProjectAnalysisRun).where(
                ProjectAnalysisRun.project_id == project_id,
                ProjectAnalysisRun.status.in_(("QUEUED", "RUNNING", "REPORT_QUEUED")),
            )
        )
        if active is not None:
            if reuse_active and active.input_hash == input_hash:
                return active
            raise DomainError(
                "ANALYSIS_ALREADY_RUNNING", "项目已有基于其他输入的完整分析正在执行", 409
            )
        now = datetime.now(UTC)
        run = ProjectAnalysisRun(
            id=uuid4(),
            project_id=project_id,
            status="QUEUED",
            current_stage="SNAPSHOT",
            input_hash=input_hash,
            input_snapshot=snapshot,
            stage_outputs={"SNAPSHOT": {"status": "SUCCEEDED"}},
            created_by=requested_by,
            created_at=now,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def process(self, run_id: UUID) -> None:
        """依次执行匹配、风险、决策和报告准备；每阶段都校验冻结输入未漂移。"""
        run = await self._session.get(ProjectAnalysisRun, run_id, with_for_update=True)
        if run is None or run.status != "QUEUED":
            return
        transition_full(run, "RUNNING")
        run.current_stage = "MATCHING"
        run.started_at = datetime.now(UTC)
        await update_downstream_stage_for_analysis(
            self._session,
            run.id,
            status="RUNNING",
            analysis_status=run.status,
            current_stage=run.current_stage,
        )
        await self._session.commit()
        try:
            # 队列等待期间若项目需求、关联企业或企业基础材料发生变化，本轮输入已不再
            # 是提交时看到的事实。直接失败并要求重新发起，避免把不同版本的数据混到
            # 同一份匹配、风险和报告中。
            await self._assert_input_unchanged(run)
            matches = await MatchingService(self._session).run(run.project_id)
            await self._complete_stage(run, "MATCHING", {"count": len(matches)})

            await self._assert_input_unchanged(run)
            risks = await RiskService(self._session).run(run.project_id, run.created_by)
            await self._complete_stage(run, "RISK_CHECK", {"count": len(risks)})

            await self._assert_input_unchanged(run)
            decision = await DecisionService(self._session).generate(run.project_id)
            await self._complete_stage(
                run, "DECISION", {"value": decision.decision, "score": decision.score}
            )

            # 报告准备会冻结最终正文输入；此前再校验一次，防止长任务运行期间
            # 项目/企业/材料被修改后把两个版本的数据混进同一份报告。
            await self._assert_input_unchanged(run)
            report_service = ProjectReportService(self._session, self._settings)
            report = await report_service.prepare_from_analysis(
                run.project_id, run.created_by, run.id
            )
            transition_full(run, "REPORT_QUEUED")
            run.current_stage = "REPORT"
            if report.status == "READY":
                # 输入完全一致时允许复用当前报告正文；此时不会再触发 Report Worker，
                # 因此完整分析必须在本事务中同步进入成功终态。
                transition_full(run, "SUCCEEDED")
                run.completed_at = datetime.now(UTC)
                run.stage_outputs = {
                    **run.stage_outputs,
                    "REPORT": {
                        "status": "SUCCEEDED",
                        "report_id": str(report.id),
                        "reused": True,
                    },
                }
                await update_downstream_stage_for_analysis(
                    self._session,
                    run.id,
                    status="SUCCEEDED",
                    analysis_status=run.status,
                    current_stage=run.current_stage,
                    extra={"report_id": str(report.id), "reused_report": True},
                )
            else:
                run.stage_outputs = {
                    **run.stage_outputs,
                    "REPORT": {"status": "QUEUED", "report_id": str(report.id)},
                }
                await update_downstream_stage_for_analysis(
                    self._session,
                    run.id,
                    status="RUNNING",
                    analysis_status=run.status,
                    current_stage=run.current_stage,
                    extra={"report_id": str(report.id)},
                )
            await self._session.commit()
            await report_service.publish_if_queued(report)
        except Exception as exc:
            await self._session.rollback()
            run = await self._session.get(ProjectAnalysisRun, run_id, with_for_update=True)
            if run is not None:
                error_code = "ANALYSIS_FAILED"
                error_message = "完整分析执行失败"
                # 对使用者可操作的输入变更，保留明确的失败原因；其它异常仍不暴露
                # 内部实现和第三方服务细节。
                if isinstance(exc, DomainError):
                    error_code = exc.code
                    error_message = exc.message
                if error_code == "ANALYSIS_INPUT_CHANGED":
                    # 输入本身已变化，上一套当前分析结果也不再可信。
                    await AnalysisInvalidationService(self._session).invalidate_inputs(
                        {run.project_id}
                    )
                transition_full(run, "FAILED")
                run.current_stage = "FAILED"
                run.error_code = error_code
                run.error_message, run.completed_at = error_message, datetime.now(UTC)
                await update_downstream_stage_for_analysis(
                    self._session,
                    run.id,
                    status="FAILED",
                    analysis_status=run.status,
                    current_stage=run.current_stage,
                    extra={"error_code": error_code},
                )
                await self._session.commit()
            raise

    async def _assert_input_unchanged(self, run: ProjectAnalysisRun) -> None:
        requirements = await self._requirements.list_confirmed(run.project_id)
        current_manifest = await self._manifest(run.project_id, requirements)
        if self._manifest_hash(current_manifest) != run.input_hash:
            raise DomainError(
                "ANALYSIS_INPUT_CHANGED",
                "项目、需求、投标企业或确认材料已变化，请重新发起完整分析",
                409,
            )

    async def _complete_stage(
        self, run: ProjectAnalysisRun, stage: str, output: dict[str, object]
    ) -> None:
        run.current_stage = stage
        run.stage_outputs = {
            **run.stage_outputs,
            stage: {"status": "SUCCEEDED", **output},
        }
        await update_downstream_stage_for_analysis(
            self._session,
            run.id,
            status="RUNNING",
            analysis_status=run.status,
            current_stage=run.current_stage,
            extra={"last_completed_stage": stage},
        )

    async def _manifest(self, project_id: UUID, requirements) -> dict[str, object]:
        """冻结完整分析真正会读取的项目、企业、需求和材料版本。

        仅记录 enterprise_id/material_id 不够：投标截止时间、企业名称或联合体牵头方
        在排队期间变化，同样会改变风险判断和最终报告。把这些版本信息放入 manifest，
        Worker 开始执行时即可拒绝混用两个时点的数据。
        """
        project = await self._session.get(TenderProject, project_id)
        if project is None or project.deleted_at is not None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
        enterprise_rows = list(
            (
                await self._session.execute(
                    select(ProjectEnterprise, Enterprise)
                    .join(Enterprise, Enterprise.id == ProjectEnterprise.enterprise_id)
                    .where(
                        ProjectEnterprise.project_id == project_id,
                        Enterprise.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        enterprise_ids = [binding.enterprise_id for binding, _ in enterprise_rows]
        materials = []
        if enterprise_ids:
            materials = list(
                (
                    await self._session.scalars(
                        select(EnterpriseMaterial).where(
                            EnterpriseMaterial.enterprise_id.in_(enterprise_ids),
                            EnterpriseMaterial.status == "CONFIRMED",
                            EnterpriseMaterial.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
        return {
            "project": {
                "id": str(project.id),
                "updated_at": project.updated_at.isoformat(),
                "name": project.name,
                "code": project.code,
                "purchaser": project.purchaser,
                "bid_deadline": (
                    None if project.bid_deadline is None else project.bid_deadline.isoformat()
                ),
            },
            "requirements": sorted(
                [
                    {"id": str(item.id), "updated_at": item.updated_at.isoformat()}
                    for item in requirements
                ],
                key=lambda item: item["id"],
            ),
            "enterprise_ids": sorted(str(item) for item in enterprise_ids),
            "enterprises": sorted(
                [
                    {
                        "id": str(enterprise.id),
                        "updated_at": enterprise.updated_at.isoformat(),
                        "is_lead": binding.is_lead,
                    }
                    for binding, enterprise in enterprise_rows
                ],
                key=lambda item: item["id"],
            ),
            "materials": sorted(
                [
                    {"id": str(item.id), "updated_at": item.updated_at.isoformat()}
                    for item in materials
                ],
                key=lambda item: item["id"],
            ),
        }

    @staticmethod
    def _manifest_hash(manifest: dict[str, object]) -> str:
        """统一计算冻结输入哈希，避免提交和 Worker 比较规则漂移。"""
        return hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
