"""项目报告用例：冻结已确认发现项、异步生成、引用校验与审计。"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.llm import LlmUnavailable, MiniMaxM3Client
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.models import TenderProject
from app.modules.projects.service import ProjectService
from app.modules.reports.models import ProjectReport
from app.modules.reports.repository import ReportRepository
from app.modules.reports.schemas import ProjectReportResponse, ReportCitation
from app.modules.reports.snapshot_builder import ProjectReportSnapshotBuilder
from app.modules.reports.state_machine import transition
from app.modules.retrieval.structured_chunking import retrieval_text, table_markdown
from app.modules.tender_analysis.downstream_status import update_downstream_stage_for_analysis

logger = logging.getLogger(__name__)

_REPORT_INTERNAL_LABELS = {
    "MATCHED": "满足要求",
    "UNCERTAIN": "待补充核验",
    "MISSING": "存在材料缺口",
    "OPEN": "待处置",
    "ACCEPTED": "已接受",
    "RESOLVED": "已解决",
    "DISMISSED": "已忽略",
    "CRITICAL": "严重",
    "HIGH": "高",
    "MEDIUM": "中",
    "LOW": "低",
    "INFO": "提示",
}

_DECISION_LABELS = {
    "BID": "建议投标",
    "NO_BID": "暂不建议投标",
    "PENDING": "暂缓决策",
}


class ProjectReportService:
    """报告只使用已确认事实、匹配/风险/决策和可验证 Evidence。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._reports = ReportRepository(session)
        self._snapshot_builder = ProjectReportSnapshotBuilder(session)
        self._projects = ProjectService(session)

    async def submit(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        *,
        report_type: str = "SIMPLE",
        analysis_run_id: UUID | None = None,
    ) -> ProjectReportResponse:
        """冻结当前确认结论和 Evidence，生成/覆盖项目当前报告。"""
        await self._projects.require_project_management(project_id, actor)
        report = await self._prepare_authorized(
            project_id,
            actor.id,
            report_type=report_type,
            analysis_run_id=analysis_run_id,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DomainError("REPORT_ALREADY_GENERATING", "该项目已有报告正在生成", 409) from exc
        await self.publish_if_queued(report)
        return self._response(report)

    async def prepare_from_analysis(
        self,
        project_id: UUID,
        requested_by: UUID,
        analysis_run_id: UUID,
    ) -> ProjectReport:
        """在 FullAnalysis 当前事务中准备项目当前报告，不提交、不发布 ARQ。

        ProjectReport.analysis_run_id 只表示“当前这次报告生成由哪个分析运行触发”；
        AnalysisRun 本身不持有可变报告正文，避免把运行记录误建模成报告历史。
        """
        return await self._prepare_authorized(
            project_id,
            requested_by,
            report_type="FULL",
            analysis_run_id=analysis_run_id,
        )

    async def publish_if_queued(self, report: ProjectReport) -> None:
        if report.status != "QUEUED":
            return
        try:
            await ArqTaskQueue(self._settings).publish_project_report(str(report.id))
        except TaskQueueUnavailable:
            # PostgreSQL 的 QUEUED 是事实源，由 reconciler 后续补投。
            logger.warning(
                "ARQ 暂不可用，报告任务等待 reconciler 补投 report_id=%s",
                report.id,
            )

    async def _prepare_authorized(
        self,
        project_id: UUID,
        created_by: UUID,
        *,
        report_type: str,
        analysis_run_id: UUID | None,
    ) -> ProjectReport:
        """在调用方事务中创建/复用报告记录，不自行提交或访问队列。"""
        # 报告的事实表与证据由后端确定性渲染；LLM 只补充管理摘要，未配置或临时
        # 不可用时也不能阻断报告交付。
        # 只在准备报告记录时锁项目行，串行化“检查/覆盖当前报告”。
        # 报告 LLM 生成在独立 Worker 中执行，不会持有此锁。
        project_exists = await self._session.scalar(
            select(TenderProject.id)
            .where(TenderProject.id == project_id, TenderProject.deleted_at.is_(None))
            .with_for_update()
        )
        if project_exists is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
        snapshot = await self._snapshot_builder.build(project_id)
        snapshot["report_type"] = report_type
        # 提示词章节契约升级时必须改变冻结输入哈希，不能静默复用旧版报告正文。
        snapshot["report_layout_version"] = 7
        has_reportable_fact = bool(
            snapshot.get("findings")
            or snapshot.get("qualification_requirements")
            or snapshot.get("matches")
            or snapshot.get("risks")
            or snapshot.get("decision")
        )
        if not has_reportable_fact:
            raise DomainError("REPORT_INPUT_NOT_READY", "当前项目没有可生成报告的确认结论", 409)
        if not snapshot.get("evidence"):
            raise DomainError("REPORT_INPUT_NOT_READY", "当前分析结论缺少可验证 Evidence", 409)
        input_hash = hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        current = await self._reports.get_for_project(project_id, for_update=True)
        if current is not None and current.status in {"QUEUED", "GENERATING"}:
            raise DomainError("REPORT_ALREADY_GENERATING", "该项目已有报告正在生成", 409)
        if (
            current is not None
            and current.status == "READY"
            and not current.is_stale
            and current.input_hash == input_hash
        ):
            current.analysis_run_id = analysis_run_id
            return current
        now = datetime.now(UTC)
        if current is None:
            report = ProjectReport(
                id=uuid4(),
                project_id=project_id,
                report_type=report_type,
                status="QUEUED",
                input_hash=input_hash,
                analysis_run_id=analysis_run_id,
                input_snapshot=snapshot,
                citations=[],
                sections=[],
                finding_count=len(snapshot["findings"]),
                created_by=created_by,
                created_at=now,
            )
            self._reports.add(report)
        else:
            report = current
            report.report_type = report_type
            report.status = "QUEUED"
            report.input_hash = input_hash
            report.analysis_run_id = analysis_run_id
            report.input_snapshot = snapshot
            report.content_markdown = None
            report.is_stale = False
            report.sections = []
            report.citations = []
            report.finding_count = len(snapshot["findings"])
            report.created_by = created_by
            report.created_at = now
            report.started_at = None
            report.completed_at = None
            report.error_code = None
            report.error_message = None
        self._session.add(
            AuditLog(
                actor_id=created_by,
                action="SUBMIT_PROJECT_REPORT",
                target_type="PROJECT_REPORT",
                target_id=report.id,
                project_id=project_id,
                created_at=now,
            )
        )
        await self._session.flush()
        return report

    async def get(
        self, project_id: UUID, report_id: UUID, actor: AuthenticatedUser
    ) -> ProjectReportResponse:
        """按报告项目归属和成员资格返回报告，UUID 本身不构成授权。"""
        await self._projects.require_project_access(project_id, actor)
        report = await self._reports.get(report_id)
        if report is None or report.project_id != project_id:
            raise DomainError("REPORT_NOT_FOUND", "报告不存在或无权访问", 404)
        return self._response(report)

    async def latest(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> ProjectReportResponse | None:
        """返回项目当前成功报告，不泄漏其他项目报告。"""
        await self._projects.require_project_access(project_id, actor)
        report = await self._reports.get_latest_ready(project_id)
        return None if report is None else self._response(report)

    async def process(self, report_id: UUID) -> None:
        """Worker 入口：只用报告 ID 回查冻结快照，绝不从队列传业务正文。"""
        report = await self._reports.get(report_id, for_update=True)
        if report is None or report.status != "QUEUED":
            return
        if report.is_stale:
            # 排队期间上游事实已经变化时，不再浪费一次 LLM 生成；由调用方基于
            # 最新 snapshot 重新提交。完整分析 run 也必须明确失败，不能一直 RUNNING。
            await self._mark_failed(
                report_id,
                "REPORT_INPUT_CHANGED",
                "报告排队期间项目事实已变化，请重新生成",
            )
            return
        transition(report, "GENERATING")
        report.started_at = datetime.now(UTC)
        report.error_code = None
        report.error_message = None
        await self._session.commit()
        try:
            management_summary = self._fallback_management_summary(report.input_snapshot)
            try:
                generated_summary, _ = await MiniMaxM3Client(self._settings).generate_report(
                    report.input_snapshot
                )
                management_summary = self._clean_management_summary(generated_summary)
            except LlmUnavailable as exc:
                logger.warning(
                    "报告管理摘要不可用，使用确定性摘要 report_id=%s: %s", report_id, exc
                )
            # LLM 运行期间上游事实可能变化。返回后重新锁定并强制刷新当前报告，
            # 保证 invalidation 与 READY 落库之间有确定顺序。
            current = await self._reports.get(report_id, for_update=True)
            if current is None:
                return
            report = current
            if report.is_stale:
                await self._mark_failed(
                    report_id,
                    "REPORT_INPUT_CHANGED",
                    "报告生成期间项目事实已变化，请重新生成",
                )
                return
            citations = self._fallback_citations(report.input_snapshot)
            if not citations:
                raise LlmUnavailable("报告冻结快照缺少可验证 Evidence")
            content = self._render_fixed_report(
                report.input_snapshot, management_summary, citations
            )
            content = self._replace_internal_labels(content)
            report.content_markdown = content
            report.sections = self._sections(content)
            report.citations = [citation.model_dump(mode="json") for citation in citations]
            transition(report, "READY")
            report.completed_at = datetime.now(UTC)
            if report.analysis_run_id is not None:
                # 完整分析的最终成功只能由报告落库后决定，不能在“已入队”时提前完成。
                from app.modules.analysis.models import ProjectAnalysisRun
                from app.modules.analysis.state_machine import transition_full

                analysis_run = await self._session.get(
                    ProjectAnalysisRun, report.analysis_run_id, with_for_update=True
                )
                if analysis_run is not None:
                    transition_full(analysis_run, "SUCCEEDED")
                    analysis_run.current_stage = "REPORT"
                    analysis_run.completed_at = report.completed_at
                    analysis_run.stage_outputs = {
                        **analysis_run.stage_outputs,
                        "REPORT": {"status": "SUCCEEDED", "report_id": str(report.id)},
                    }
                    await update_downstream_stage_for_analysis(
                        self._session,
                        analysis_run.id,
                        status="SUCCEEDED",
                        analysis_status=analysis_run.status,
                        current_stage=analysis_run.current_stage,
                        extra={"report_id": str(report.id)},
                    )
            self._session.add(
                AuditLog(
                    actor_id=report.created_by,
                    action="COMPLETE_PROJECT_REPORT",
                    target_type="PROJECT_REPORT",
                    target_id=report.id,
                    project_id=report.project_id,
                    after_summary=f"引用 {len(citations)} 条 Evidence",
                    created_at=report.completed_at,
                )
            )
            await self._session.commit()
        except LlmUnavailable as exc:
            await self._session.rollback()
            await self._mark_failed(report_id, "REPORT_EVIDENCE_UNAVAILABLE", str(exc))
            raise exc
        except Exception:
            await self._session.rollback()
            await self._mark_failed(
                report_id, "REPORT_GENERATION_FAILED", "报告生成失败，请稍后重试"
            )
            raise

    async def _mark_failed(self, report_id: UUID, code: str, message: str) -> None:
        report = await self._reports.get(report_id, for_update=True)
        if report is None:
            return
        transition(report, "FAILED")
        report.error_code = code
        report.error_message = message
        report.completed_at = datetime.now(UTC)
        if report.analysis_run_id is not None:
            from app.modules.analysis.models import ProjectAnalysisRun
            from app.modules.analysis.state_machine import transition_full

            analysis_run = await self._session.get(
                ProjectAnalysisRun, report.analysis_run_id, with_for_update=True
            )
            if analysis_run is not None:
                transition_full(analysis_run, "FAILED")
                analysis_run.current_stage = "REPORT_FAILED"
                analysis_run.error_code = code
                analysis_run.error_message = message
                analysis_run.completed_at = report.completed_at
                await update_downstream_stage_for_analysis(
                    self._session,
                    analysis_run.id,
                    status="FAILED",
                    analysis_status=analysis_run.status,
                    current_stage=analysis_run.current_stage,
                    extra={"error_code": analysis_run.error_code or code},
                )
        await self._session.commit()

    @staticmethod
    def _verified_citations(
        snapshot: dict[str, object], selected_ids: list[UUID]
    ) -> list[ReportCitation]:
        """只接受冻结快照中的 Evidence，拒绝模型编造的引用 UUID。"""
        source_rows = snapshot.get("evidence")
        if not isinstance(source_rows, list):
            return []
        by_id = {
            str(item.get("evidence_id")): item
            for item in source_rows
            if isinstance(item, dict) and item.get("evidence_id")
        }
        return [
            ReportCitation(
                evidence_id=evidence_id,
                quoted_text=str(by_id[str(evidence_id)]["content"]),
                locator=dict(by_id[str(evidence_id)]["locator"]),
            )
            for evidence_id in dict.fromkeys(selected_ids)
            if str(evidence_id) in by_id
        ]

    @staticmethod
    def _fallback_citations(snapshot: dict[str, object], limit: int = 8) -> list[ReportCitation]:
        """报告固定引用冻结快照中的真实原文，避免 LLM 自选引用造成漂移。"""
        source_rows = snapshot.get("evidence")
        if not isinstance(source_rows, list):
            return []
        citations: list[ReportCitation] = []
        for item in source_rows:
            if not isinstance(item, dict):
                continue
            try:
                evidence_id = UUID(str(item.get("evidence_id")))
            except (TypeError, ValueError, AttributeError):
                continue
            content = str(item.get("content") or "").strip()
            locator = item.get("locator")
            if not content or not isinstance(locator, dict):
                continue
            citations.append(
                ReportCitation(
                    evidence_id=evidence_id,
                    quoted_text=content,
                    locator=locator,
                )
            )
            if len(citations) >= limit:
                break
        return citations

    @staticmethod
    def _clean_management_summary(content: str) -> str:
        """摘要仅保留可嵌入固定模板的简短正文。"""
        content = re.sub(r"【Evidence:\s*[0-9a-fA-F-]{36}】", "", content)
        lines = [re.sub(r"^#{1,6}\s*", "", line).strip() for line in content.splitlines()]
        cleaned = "\n".join(line for line in lines if line)[:800]
        return cleaned or "请依据下列资格匹配、风险和行动项安排投标准备工作。"

    @staticmethod
    def _fallback_management_summary(snapshot: dict[str, object]) -> str:
        decision = snapshot.get("decision")
        if isinstance(decision, dict):
            label = _DECISION_LABELS.get(str(decision.get("value")), "暂缓决策")
            summary = str(decision.get("summary") or "").strip()
            return f"当前结论为“{label}”。{summary}".strip()
        return "请依据下列资格匹配、风险和行动项安排投标准备工作。"

    @staticmethod
    def _markdown_cell(value: object) -> str:
        return " ".join(str(value or "—").split()).replace("|", "\\|")

    @staticmethod
    def _source_label(locator: object) -> str:
        if not isinstance(locator, dict):
            return "—"
        document = str(locator.get("document_name") or "招标文件")
        page = locator.get("page_start") or locator.get("page_number")
        return f"{document}第 {page} 页" if page else document

    @classmethod
    def _render_fixed_report(
        cls,
        snapshot: dict[str, object],
        management_summary: str,
        citations: list[ReportCitation],
    ) -> str:
        """确定性报告骨架：事实表由后端输出，LLM 只提供一段管理摘要。"""
        project = snapshot.get("project")
        project_data = project if isinstance(project, dict) else {}
        enterprises = snapshot.get("enterprises")
        enterprise_rows = enterprises if isinstance(enterprises, list) else []
        lead = next(
            (
                str(item.get("name"))
                for item in enterprise_rows
                if isinstance(item, dict) and item.get("is_lead")
            ),
            "未指定",
        )
        evidence_rows = snapshot.get("evidence")
        evidence_by_id = (
            {
                str(item.get("evidence_id")): item
                for item in evidence_rows
                if isinstance(item, dict) and item.get("evidence_id")
            }
            if isinstance(evidence_rows, list)
            else {}
        )

        requirements = snapshot.get("matching_requirements") or snapshot.get(
            "qualification_requirements"
        )
        requirement_rows = requirements if isinstance(requirements, list) else []
        matches = snapshot.get("matches")
        match_rows = matches if isinstance(matches, list) else []
        match_by_requirement = {
            str(item.get("requirement_id")): item
            for item in match_rows
            if isinstance(item, dict) and item.get("requirement_id")
        }

        # 先将匹配状态投影为“需求 ID -> 状态”，避免在报告各处重复展开 JSON 快照。
        # 快照中的 requirement/match 都是不可信 JSON，读取时始终保留安全默认值。
        requirement_status = {
            requirement_id: str(match.get("status") or "")
            for requirement_id, match in match_by_requirement.items()
        }

        missing_mandatory = sum(
            1
            for requirement in requirement_rows
            if isinstance(requirement, dict)
            and requirement.get("is_mandatory")
            and requirement_status.get(str(requirement.get("requirement_id"))) == "存在材料缺口"
        )
        uncertain_mandatory = sum(
            1
            for requirement in requirement_rows
            if isinstance(requirement, dict)
            and requirement.get("is_mandatory")
            and requirement_status.get(str(requirement.get("requirement_id"))) == "待补充核验"
        )
        open_risk_count = sum(
            1
            for item in (snapshot.get("risks") or [])
            if isinstance(item, dict) and item.get("status") == "OPEN"
        )
        decision = snapshot.get("decision")
        decision_data = decision if isinstance(decision, dict) else {}
        decision_value = str(decision_data.get("value") or "")
        decision_label = _DECISION_LABELS.get(decision_value, "尚未生成投标决策")
        decision_score = decision_data.get("score")
        decision_summary = str(decision_data.get("summary") or "").strip()
        if decision_value == "NO_BID":
            readiness = "当前不建议投标，完成关键核验后再重新评估"
        elif decision_value == "BID":
            readiness = "建议推进投标准备，提交前完成例行复核"
        elif decision_value == "PENDING":
            readiness = "暂缓决策，等待关键条件完成核验"
        elif not enterprise_rows:
            readiness = "尚未绑定投标企业"
        elif not match_rows:
            readiness = "待完成企业匹配分析"
        elif missing_mandatory:
            readiness = "当前企业暂不具备投标条件"
        elif uncertain_mandatory:
            readiness = "企业适配，但关键材料待核验"
        else:
            readiness = "当前绑定企业可进入投标准备"
        match_counts = {
            status: sum(
                1
                for item in match_rows
                if isinstance(item, dict) and str(item.get("status")) == status
            )
            for status in ("满足要求", "待补充核验", "存在材料缺口")
        }
        decisive_items: list[tuple[dict[str, object], dict[str, object]]] = []
        for requirement in requirement_rows:
            if not isinstance(requirement, dict):
                continue
            match = match_by_requirement.get(str(requirement.get("requirement_id")))
            if not isinstance(match, dict) or match.get("status") not in {
                "存在材料缺口",
                "待补充核验",
            }:
                continue
            decisive_items.append((requirement, match))
        decisive_items.sort(
            key=lambda item: (
                not bool(item[0].get("is_mandatory")),
                0 if item[1].get("status") == "存在材料缺口" else 1,
            )
        )
        lines = [
            "# 绑定企业投标适配度分析报告",
            "",
            "## 企业适配度结论",
            "",
            f"**{readiness}**",
            "",
            f"- 已绑定企业：{len(enterprise_rows)} 家（牵头企业：{cls._markdown_cell(lead)}）",
            f"- 已完成匹配：{len(match_rows)} 项招标要求",
            f"- 系统投标决策：{decision_label}"
            + (
                f"（评分：{cls._markdown_cell(decision_score)}）"
                if decision_score is not None
                else ""
            ),
            f"- 满足要求：{match_counts['满足要求']} 项",
            f"- 待补充核验：{match_counts['待补充核验']} 项",
            f"- 存在材料缺口：{match_counts['存在材料缺口']} 项",
            f"- 强制资格缺口：{missing_mandatory} 项",
            f"- 强制资格待核验：{uncertain_mandatory} 项",
            f"- 待处置风险：{open_risk_count} 项",
            "",
            "适配度结论基于已确认的招标要求、绑定企业已确认材料、匹配结果和风险状态；未核验事项不视为满足。",
            "决策说明：" + cls._markdown_cell(decision_summary or "尚未形成补充决策说明。"),
            "",
            "## 项目概况",
            "",
            f"- 项目名称：{cls._markdown_cell(project_data.get('name'))}",
            f"- 项目编号：{cls._markdown_cell(project_data.get('code'))}",
            f"- 招标人：{cls._markdown_cell(project_data.get('purchaser'))}",
            f"- 投标截止时间：{cls._markdown_cell(project_data.get('bid_deadline'))}",
            f"- 牵头企业：{cls._markdown_cell(lead)}",
            "",
            "## 绑定企业与材料概览",
            "",
            *[
                f"- {cls._markdown_cell(item.get('name'))}"
                + ("（牵头企业）" if item.get("is_lead") else "")
                + f"：已确认材料 {cls._markdown_cell(item.get('confirmed_material_count'))} 份"
                for item in enterprise_rows
                if isinstance(item, dict)
            ],
            "",
            "## 决定性缺口与推进条件",
            "",
        ]
        if decisive_items:
            for requirement, match in decisive_items[:5]:
                condition = (
                    match.get("missing_conditions") or match.get("reason") or "需补充核验依据"
                )
                lines.append(
                    f"- **{cls._markdown_cell(requirement.get('title'))}**"
                    f"（{'强制项' if requirement.get('is_mandatory') else '非强制项'}，"
                    f"{cls._markdown_cell(match.get('status'))}）："
                    f"{cls._markdown_cell(condition)}"
                )
            lines.extend(
                [
                    "",
                    "完成上述关键项的材料补充或人工核验后，应重新执行匹配、风险和投标决策；"
                    "在结论变更前，不应将待核验项按已满足处理。",
                ]
            )
        else:
            lines.append(
                "当前未发现待补充或缺失的匹配项，仍应在提交前复核材料有效期和投标主体信息。"
            )

        lines.extend(["", "## 企业与招标要求适配明细"])
        if requirement_rows:
            for requirement in requirement_rows:
                if not isinstance(requirement, dict):
                    continue
                match = match_by_requirement.get(str(requirement.get("requirement_id")))
                match_data = match if isinstance(match, dict) else {}
                evidence_ids = requirement.get("evidence_ids")
                evidence_id = (
                    str(evidence_ids[0]) if isinstance(evidence_ids, list) and evidence_ids else ""
                )
                evidence = evidence_by_id.get(evidence_id)
                evidence_data = evidence if isinstance(evidence, dict) else {}
                locator = evidence_data.get("locator")
                node_types = locator.get("node_types") or () if isinstance(locator, dict) else ()
                raw_text = str(evidence_data.get("content") or "")
                quote = " ".join(retrieval_text(raw_text, node_types).split()) if raw_text else ""
                if len(quote) > 500:
                    quote = f"{quote[:500]}……"
                conclusion = cls._markdown_cell(match_data.get("status") or "尚未执行匹配")
                requirement_text = cls._markdown_cell(
                    requirement.get("description") or requirement.get("title")
                )
                enterprise_name = cls._markdown_cell(
                    match_data.get("enterprise_name") or "未匹配到企业材料"
                )
                material_name = cls._markdown_cell(match_data.get("material_name") or "未关联材料")
                verification_note = cls._markdown_cell(match_data.get("reason") or "尚未执行匹配")
                missing_conditions = cls._markdown_cell(
                    match_data.get("missing_conditions") or "无"
                )
                source_quote = (
                    f"> {quote}"
                    if quote
                    else "> 未找到该检查项可展示的原文，请回到需求复核补充证据。"
                )
                lines.extend(
                    [
                        "",
                        f"### {cls._markdown_cell(requirement.get('title'))}",
                        "",
                        f"**结论：{conclusion}**"
                        + ("（强制项）" if requirement.get("is_mandatory") else ""),
                        "",
                        f"招标要求：{requirement_text}",
                        f"匹配企业：{enterprise_name}",
                        f"对应材料：{material_name}",
                        f"核验说明：{verification_note}",
                        f"待补条件：{missing_conditions}",
                        "",
                        f"**原文依据｜{cls._source_label(locator)}**",
                        "",
                        source_quote,
                    ]
                )
        else:
            lines.extend(["", "暂无已确认招标要求，请先完成需求复核。"])

        submission_notes = snapshot.get("submission_original_notes")
        submission_rows = submission_notes if isinstance(submission_notes, list) else []
        lines.extend(["", "## 制作与递交原文补充", ""])
        if submission_rows:
            lines.append(
                "以下内容用于制作和递交投标文件时核对，直接保留招标文件原文，不作为需人工填写的项目字段。"
            )
            for item in submission_rows:
                if not isinstance(item, dict):
                    continue
                locator = item.get("locator")
                locator_data = locator if isinstance(locator, dict) else {}
                raw_text = str(item.get("content") or "")
                node_types = locator_data.get("node_types") or ()
                quote = " ".join(retrieval_text(raw_text, node_types).split())
                if len(quote) > 500:
                    quote = f"{quote[:500]}……"
                lines.extend(
                    [
                        "",
                        f"**原文依据｜{cls._source_label(locator_data)}**",
                        "",
                        f"> {quote}" if quote else "> 未提取到可展示的原文。",
                    ]
                )
        else:
            lines.append("未在当前招标文件中定位到制作或递交相关的原文片段。")

        risks = snapshot.get("risks")
        risk_rows = risks if isinstance(risks, list) else []
        lines.extend(["", "## 风险与待办", ""])
        if risk_rows:
            for risk in risk_rows:
                if not isinstance(risk, dict):
                    continue
                lines.extend(
                    [
                        f"### {cls._markdown_cell(risk.get('title'))}",
                        "",
                        f"风险等级：{cls._markdown_cell(risk.get('severity'))}；处理状态：{cls._markdown_cell(risk.get('status'))}",
                        f"说明：{cls._markdown_cell(risk.get('description'))}",
                        "",
                    ]
                )
        else:
            lines.append("当前未识别需要处置的风险。")

        uncertain_count = sum(
            1 for item in match_by_requirement.values() if str(item.get("status")) == "待补充核验"
        )
        missing_count = sum(
            1 for item in match_by_requirement.values() if str(item.get("status")) == "存在材料缺口"
        )
        actions: list[str] = []
        if missing_count:
            actions.append(f"补齐 {missing_count} 项存在材料缺口的要求证明。")
        if uncertain_count:
            actions.append(f"完成 {uncertain_count} 项待补充核验要求的材料补充或人工核验。")
        if open_risk_count:
            actions.append(f"逐项闭环 {open_risk_count} 项待处置风险。")
        if not actions:
            actions.append("复核报告中的资格匹配和风险状态后，再安排投标提交。")
        if decisive_items:
            actions.append("关键项闭环后，重新执行匹配、风险分析和投标决策。")
        lines.extend(
            [
                "",
                "## 下一步",
                "",
                *[f"  - {action}" for action in actions],
                "",
                "## 分析补充说明",
                "",
                cls._replace_internal_labels(management_summary),
            ]
        )
        return cls._replace_internal_labels("\n".join(lines))

    @staticmethod
    def _append_fallback_evidence_markers(content: str, citations: list[ReportCitation]) -> str:
        """以附录形式呈现保底原文，保证读者能追溯而不暴露 UUID。"""
        markers = "\n\n".join(f"【Evidence: {citation.evidence_id}】" for citation in citations)
        return f"{content.rstrip()}\n\n## 原文依据\n\n{markers}"

    @staticmethod
    def _render_evidence_quotes(content: str, citations: list[ReportCitation]) -> str:
        """把模型内部引用标记转换为读者可核对的原文依据，绝不在报告暴露 UUID。"""
        by_id = {str(item.evidence_id): item for item in citations}
        marker = re.compile(
            r"【Evidence:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})】"
        )

        def replacement(match: re.Match[str]) -> str:
            citation = by_id.get(match.group(1))
            if citation is None:
                return ""
            locator = citation.locator or {}
            document = str(locator.get("document_name") or "招标文件")
            page = locator.get("page_start") or locator.get("page_number")
            source = f"{document}第 {page} 页" if page else document
            node_types = (locator.get("node_types") or ()) if isinstance(locator, dict) else ()
            markdown_table = table_markdown(citation.quoted_text) if "TABLE" in node_types else None
            if markdown_table:
                return f"\n\n**原文依据（{source}）**\n\n{markdown_table}"
            readable = str(locator.get("retrieval_text") or "")
            quote = " ".join(
                (readable or retrieval_text(citation.quoted_text, node_types or ())).split()
            )
            if len(quote) > 500:
                quote = f"{quote[:500]}……"
            return f"\n\n> 原文依据（{source}）：{quote}"

        rendered = marker.sub(replacement, content)
        # 模型偶尔会在风险说明中复述内部 UUID；交付件没有读者价值，统一移除。
        return re.sub(
            r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            "",
            rendered,
        )

    @staticmethod
    def _replace_internal_labels(content: str) -> str:
        """最后一道交付防线：报告绝不展示数据库状态/等级枚举。"""
        for code, label in _REPORT_INTERNAL_LABELS.items():
            content = re.sub(rf"\b{re.escape(code)}\b", label, content)
        return content

    @staticmethod
    def _sections(content: str) -> list[dict[str, object]]:
        """按二级标题冻结章节，导出时仍只读同一份 Markdown 事实源。"""
        sections: list[dict[str, object]] = []
        title = "报告正文"
        lines: list[str] = []
        for line in content.splitlines():
            if line.startswith("## "):
                if lines:
                    sections.append({"title": title, "content_markdown": "\n".join(lines).strip()})
                # 前端已将章节标题作为折叠项/卡片标题渲染；正文保留该标题会重复显示。
                title, lines = line[3:].strip(), []
            else:
                lines.append(line)
        if lines:
            sections.append({"title": title, "content_markdown": "\n".join(lines).strip()})
        return sections

    @staticmethod
    def _response(report: ProjectReport) -> ProjectReportResponse:
        # ``content_markdown`` 是报告的单一事实源。历史报告的 sections 曾把二级标题
        # 同时保存在外层标题和正文中，响应时重建可立即修复预览重复标题。
        sections = (
            ProjectReportService._sections(report.content_markdown)
            if report.content_markdown
            else list(report.sections or [])
        )
        return ProjectReportResponse(
            id=report.id,
            project_id=report.project_id,
            report_type=report.report_type,
            analysis_run_id=report.analysis_run_id,
            status=report.status,
            is_stale=report.is_stale,
            finding_count=report.finding_count,
            content_markdown=report.content_markdown,
            sections=sections,
            citations=[ReportCitation.model_validate(item) for item in report.citations],
            error_code=report.error_code,
            error_message=report.error_message,
            created_at=report.created_at,
            started_at=report.started_at,
            completed_at=report.completed_at,
        )
