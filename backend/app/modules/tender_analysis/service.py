# ruff: noqa: E501
"""投标管线用例与授权边界。"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.documents.repository import DocumentRepository
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService
from app.modules.tender_analysis.models import TenderPipelineRun, TenderPipelineStage, TenderTag
from app.modules.tender_analysis.repository import TenderPipelineRepository
from app.modules.tender_analysis.schemas import (
    TenderPipelineReviewRequest,
    TenderPipelineRunResponse,
    TenderPipelineStageResponse,
)
from app.modules.tender_analysis.state_machine import transition

_STAGES = (
    "preflight",
    "select_candidates",
    "extract",
    "validate",
    "human_review",
    "finalize",
    "downstream_analysis",
)


logger = logging.getLogger(__name__)


class TenderPipelineService:
    """管理招标文档提取、人工复核与下游完整分析的可恢复状态机。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._documents = DocumentRepository(session)
        self._runs = TenderPipelineRepository(session)

    async def submit(
        self, project_id: UUID, version_id: UUID, actor: AuthenticatedUser
    ) -> TenderPipelineRunResponse:
        """为当前文档版本创建提取运行；提交成功后再投递队列，避免幽灵任务。"""
        await ProjectService(self._session).require_document_write(project_id, actor)
        version, _document = await self._require_current_document_version(project_id, version_id)
        if await self._runs.get_active_for_document_version(version_id) is not None:
            raise DomainError("TENDER_PIPELINE_ALREADY_RUNNING", "该文档版本已有进行中的分析", 409)
        now = datetime.now(UTC)
        run = TenderPipelineRun(
            id=uuid4(),
            project_id=project_id,
            document_version_id=version_id,
            requested_by=actor.id,
            thread_id=f"tender-{uuid4().hex}",
            status="QUEUED",
            attempt=0,
            created_at=now,
        )
        self._runs.add_run(run)
        self._runs.add_stages(
            [
                TenderPipelineStage(
                    id=uuid4(), pipeline_run_id=run.id, stage_name=name, status="PENDING"
                )
                for name in _STAGES
            ]
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DomainError(
                "TENDER_PIPELINE_ALREADY_RUNNING", "该文档版本已有进行中的分析", 409
            ) from exc
        try:
            run.arq_job_id = await ArqTaskQueue(self._settings).publish_tender_pipeline(str(run.id))
            await self._session.commit()
        except TaskQueueUnavailable:
            # PostgreSQL 中的 QUEUED 是任务事实源；Redis 短暂不可用不等于业务失败。
            # Worker 的定时 reconciler 会用固定 job_id 补投。
            run.arq_job_id = None
            await self._session.commit()
            logger.warning(
                "ARQ 暂不可用，投标管线等待 reconciler 补投 run_id=%s",
                run.id,
            )
        return await self._response(run)

    async def get(
        self, project_id: UUID, run_id: UUID, actor: AuthenticatedUser
    ) -> TenderPipelineRunResponse:
        """按项目边界读取单次运行，run UUID 不能绕过项目授权。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        run = await self._runs.get_run(run_id)
        if run is None or run.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "分析运行不存在或无权访问", 404)
        return await self._response(run)

    async def list(
        self, project_id: UUID, actor: AuthenticatedUser, limit: int
    ) -> list[TenderPipelineRunResponse]:
        """列出当前项目运行历史；不允许从其他项目的 run ID 恢复状态。"""
        await ProjectService(self._session).require_project_access(project_id, actor)
        runs = await self._runs.list_runs(project_id, limit)
        stages = await self._runs.list_stages_for_runs([run.id for run in runs])
        by_run: dict[UUID, list[TenderPipelineStage]] = {}
        for stage in stages:
            by_run.setdefault(stage.pipeline_run_id, []).append(stage)
        return [self._response_from_stages(run, by_run.get(run.id, [])) for run in runs]

    async def review(
        self,
        project_id: UUID,
        run_id: UUID,
        actor: AuthenticatedUser,
        payload: TenderPipelineReviewRequest,
    ) -> TenderPipelineRunResponse:
        """保存人工审批结论后再恢复 Worker，确保断线也不会丢失审核事实。"""
        await ProjectService(self._session).require_document_write(project_id, actor)
        run = await self._runs.get_run(run_id, for_update=True)
        if run is None or run.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "分析运行不存在或无权访问", 404)
        if run.status != "WAITING_HUMAN_REVIEW":
            raise DomainError("INVALID_STATE_TRANSITION", "当前任务不在等待人工复核状态", 409)
        await self._require_current_document_version(project_id, run.document_version_id)
        reviewed_tags = self._sanitize_reviewed_tags(payload.reviewed_tags)
        approved_tag_codes = self._approved_tag_codes(run, payload.approved_tag_codes)
        await self._validate_reviewed_tags(
            run, reviewed_tags, approved_tag_codes, require_complete=payload.decision == "approved"
        )
        resume_token = uuid4().hex
        decision = {
            "decision": payload.decision,
            "approved_tag_codes": sorted(approved_tag_codes),
            "reviewed_tags": reviewed_tags,
            "reviewer_id": str(actor.id),
        }
        # 审核事实先落 PostgreSQL，ARQ 只接收 run_id；即使 Redis 暂时不可用，
        # 人工决定也不会丢失，reconciler 可按 resume_token 用同一 job_id 补投。
        run.pending_review = {
            **(run.pending_review or {}),
            "resume_decision": decision,
            "resume_token": resume_token,
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        transition(run, "RESUME_QUEUED")
        human_stage = await self._runs.get_stage(run.id, "human_review", for_update=True)
        if human_stage is not None:
            human_stage.status = "SUCCEEDED"
            human_stage.output_summary = {
                "decision": payload.decision,
                "reviewer_id": str(actor.id),
            }
            human_stage.completed_at = datetime.now(UTC)
        await self._session.commit()
        try:
            run.arq_job_id = await ArqTaskQueue(self._settings).publish_tender_pipeline_resume(
                str(run.id), resume_token
            )
            await self._session.commit()
        except TaskQueueUnavailable:
            run.arq_job_id = None
            await self._session.commit()
            logger.warning(
                "ARQ 暂不可用，人工审核恢复等待 reconciler 补投 run_id=%s",
                run.id,
            )
        return await self._response(run)

    async def save_review_draft(
        self,
        project_id: UUID,
        run_id: UUID,
        actor: AuthenticatedUser,
        drafts: dict[str, str],
        notes: dict[str, str],
    ) -> TenderPipelineRunResponse:
        """保存尚未提交的表单文本，刷新页面后仍可继续编辑。"""
        await ProjectService(self._session).require_document_write(project_id, actor)
        run = await self._runs.get_run(run_id, for_update=True)
        if run is None or run.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "分析运行不存在或无权访问", 404)
        if run.status != "WAITING_HUMAN_REVIEW":
            raise DomainError("INVALID_STATE_TRANSITION", "当前任务不在等待人工复核状态", 409)
        pending = run.pending_review or {}
        catalog = pending.get("tag_catalog", []) if isinstance(pending, dict) else []
        allowed_codes = {
            str(item.get("code"))
            for item in catalog
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        unknown = sorted(set(drafts) - allowed_codes)
        if unknown:
            raise DomainError(
                "INVALID_REVIEW_DRAFT",
                f"草稿包含当前任务不允许的字段：{', '.join(unknown)}",
                422,
            )
        unknown_notes = sorted(set(notes) - allowed_codes)
        if unknown_notes:
            raise DomainError(
                "INVALID_REVIEW_DRAFT",
                f"草稿依据包含当前任务不允许的字段：{', '.join(unknown_notes)}",
                422,
            )
        cleaned = {
            str(code): str(value)[:20_000]
            for code, value in drafts.items()
            if isinstance(value, str)
        }
        run.pending_review = {
            **pending,
            "review_draft": cleaned,
            "review_draft_notes": {
                str(code): str(value)[:2_000]
                for code, value in notes.items()
                if isinstance(value, str)
            },
            "draft_saved_at": datetime.now(UTC).isoformat(),
            "draft_saved_by": str(actor.id),
        }
        await self._session.commit()
        return await self._response(run)

    async def retry(
        self, project_id: UUID, run_id: UUID, actor: AuthenticatedUser
    ) -> TenderPipelineRunResponse:
        """仅重试已经完成人工审核、但 resume Worker 执行失败的运行。

        初始抽取阶段失败应重新提交新的 PipelineRun，避免复用不完整 checkpoint；
        已有人审决定的 resume 失败则可以安全沿用同一 thread_id + Command(resume)。
        """
        await ProjectService(self._session).require_document_write(project_id, actor)
        run = await self._runs.get_run(run_id, for_update=True)
        if run is None or run.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "分析运行不存在或无权访问", 404)
        if run.status != "FAILED":
            raise DomainError("INVALID_STATE_TRANSITION", "只有失败的人工恢复任务可重试", 409)
        await self._require_current_document_version(project_id, run.document_version_id)
        pending = run.pending_review or {}
        decision = pending.get("resume_decision") if isinstance(pending, dict) else None
        if not isinstance(decision, dict) or decision.get("decision") not in {
            "approved",
            "rejected",
        }:
            raise DomainError(
                "PIPELINE_RETRY_REQUIRES_RESUBMIT",
                "该失败发生在人工审核前，请重新提交该文档版本的分析",
                409,
            )
        resume_token = uuid4().hex
        run.pending_review = {
            **pending,
            "resume_token": resume_token,
            "resume_retry_at": datetime.now(UTC).isoformat(),
            "resume_retry_by": str(actor.id),
        }
        transition(run, "RESUME_QUEUED")
        run.error_code = None
        run.error_message = None
        run.completed_at = None
        run.arq_job_id = None
        await self._session.commit()
        try:
            run.arq_job_id = await ArqTaskQueue(self._settings).publish_tender_pipeline_resume(
                str(run.id), resume_token
            )
            await self._session.commit()
        except TaskQueueUnavailable:
            # 保持 RESUME_QUEUED，周期 reconciler 用同一 token 补投。
            logger.warning(
                "ARQ 暂不可用，投标管线等待 reconciler 补投 run_id=%s",
                run.id,
            )
        return await self._response(run)

    async def _require_current_document_version(self, project_id: UUID, version_id: UUID):
        """投标分析只能作用于逻辑文档当前 READY 版本，历史版本仅供追溯。"""
        version = await self._documents.get_version(version_id)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "文档版本不存在", 404)
        document = await self._documents.get_active(version.document_id)
        if document is None or document.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "文档版本不存在或无权访问", 404)
        if version.parse_status != "READY":
            raise DomainError("INVALID_STATE_TRANSITION", "文档尚未解析完成", 409)
        if document.current_version_id != version.id:
            raise DomainError(
                "DOCUMENT_VERSION_NOT_CURRENT",
                "该分析基于历史文档版本，请对当前有效版本重新发起",
                409,
            )
        return version, document

    @staticmethod
    def _sanitize_reviewed_tags(
        reviewed_tags: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        """人工只能修改业务值和备注，不能覆盖模型来源血缘或置信度。"""
        output: dict[str, dict[str, object]] = {}
        allowed_keys = {"value", "note"}
        for code, item in reviewed_tags.items():
            if not isinstance(item, dict):
                raise DomainError("INVALID_REVIEW_VALUE", f"{code} 的审核值必须是对象", 422)
            unexpected = sorted(set(item) - allowed_keys)
            if unexpected:
                raise DomainError(
                    "INVALID_REVIEW_VALUE",
                    f"{code} 包含不允许由人工修改的字段：{', '.join(unexpected)}",
                    422,
                )
            if "value" not in item:
                raise DomainError("INVALID_REVIEW_VALUE", f"{code} 缺少 value", 422)
            cleaned: dict[str, object] = {"value": item.get("value")}
            if "note" in item and item.get("note") is not None:
                cleaned["note"] = str(item.get("note"))[:2_000]
            output[code] = cleaned
        return output

    @staticmethod
    def _approved_tag_codes(run: TenderPipelineRun, requested_codes: list[str] | None) -> set[str]:
        """解析人工补充字段，并合并校验通过的自动确认字段。"""
        pending = run.pending_review or {}
        extracted = pending.get("extracted_tags", {}) if isinstance(pending, dict) else {}
        extracted_codes = set(extracted) if isinstance(extracted, dict) else set()
        automatic_codes = (
            pending.get("auto_approved_tag_codes", []) if isinstance(pending, dict) else []
        )
        automatic = {str(code) for code in automatic_codes if isinstance(code, str)}
        requested = (
            extracted_codes if requested_codes is None else {str(code) for code in requested_codes}
        )
        return automatic | requested

    async def _validate_reviewed_tags(
        self,
        run: TenderPipelineRun,
        reviewed_tags: dict[str, dict[str, object]],
        approved_tag_codes: set[str],
        *,
        require_complete: bool,
    ) -> None:
        """审核值必须来自当前受控标签目录，并满足必填/类型/regex 约束。"""
        pending = run.pending_review or {}
        catalog = pending.get("tag_catalog", []) if isinstance(pending, dict) else []
        allowed_codes = {
            str(item.get("code"))
            for item in catalog
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        allowed_review_codes = allowed_codes or set(reviewed_tags)
        unknown_approved = sorted(approved_tag_codes - allowed_review_codes)
        if unknown_approved:
            raise DomainError(
                "INVALID_APPROVED_TAG",
                "勾选字段不属于当前复核任务",
                422,
            )
        candidate_codes = sorted(allowed_review_codes)
        tags = (
            list(
                (
                    await self._session.scalars(
                        select(TenderTag).where(
                            TenderTag.code.in_(candidate_codes), TenderTag.is_active.is_(True)
                        )
                    )
                ).all()
            )
            if candidate_codes
            else []
        )
        tags_by_code = {tag.code: tag for tag in tags}
        unknown = sorted(set(reviewed_tags) - set(tags_by_code))
        if unknown:
            raise DomainError(
                "INVALID_REVIEWED_TAG",
                f"审核提交包含当前任务不允许的标签：{', '.join(unknown)}",
                422,
            )

        extracted = pending.get("extracted_tags", {}) if isinstance(pending, dict) else {}
        merged: dict[str, object] = (
            {
                code: item
                for code, item in extracted.items()
                if isinstance(extracted, dict) and code in approved_tag_codes
            }
            if isinstance(extracted, dict)
            else {}
        )
        merged.update(reviewed_tags)
        if require_complete and not merged:
            raise DomainError(
                "REVIEW_SELECTION_REQUIRED",
                "请至少勾选一项有效的提取结果后再确认",
                422,
            )
        # 系统提取值可能仍保留原文时间写法（例如“开标时间另行通知”），
        # 直接审批代表业务人员接受该结果，不应再被技术格式阻断。人工修改或
        # 补充的值则必须严格遵守字段类型和正则，避免手工录入污染最终事实。
        values_to_validate = reviewed_tags
        for code, item in values_to_validate.items():
            if code not in tags_by_code:
                continue
            if not isinstance(item, dict) or "value" not in item:
                raise DomainError("INVALID_REVIEW_VALUE", f"{code} 缺少 value", 422)
            tag = tags_by_code[code]
            value = item.get("value")
            if not self._value_matches_type(value, tag.data_type, tag.is_multi_value):
                raise DomainError(
                    "INVALID_REVIEW_VALUE", f"{code} 的值不符合 {tag.data_type} 类型", 422
                )
            if tag.validation_regex and not self._regex_matches(
                value, tag.validation_regex, tag.is_multi_value
            ):
                raise DomainError("INVALID_REVIEW_VALUE", f"{code} 未通过字段格式校验", 422)

    @staticmethod
    def _has_review_value(item: object) -> bool:
        if not isinstance(item, dict) or "value" not in item:
            return False
        value = item.get("value")
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict)):
            return bool(value)
        return True

    @staticmethod
    def _value_matches_type(value: object, data_type: str, is_multi_value: bool) -> bool:
        if value is None:
            return False
        if is_multi_value:
            return (
                isinstance(value, list)
                and bool(value)
                and all(
                    TenderPipelineService._value_matches_type(item, data_type, False)
                    for item in value
                )
            )
        if data_type == "string":
            return isinstance(value, str) and bool(value.strip())
        if data_type == "number":
            if isinstance(value, bool):
                return False
            try:
                Decimal(str(value))
                return True
            except (InvalidOperation, TypeError, ValueError):
                return False
        if data_type == "datetime":
            if not isinstance(value, str) or not value.strip():
                return False
            try:
                datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                return True
            except ValueError:
                return False
        if data_type == "boolean":
            return isinstance(value, bool)
        if data_type == "array":
            return isinstance(value, list)
        if data_type == "json":
            return isinstance(value, (dict, list, str, int, float, bool))
        return False

    @staticmethod
    def _regex_matches(value: object, pattern: str, is_multi_value: bool) -> bool:
        try:
            regex = re.compile(pattern)
        except re.error:
            return False
        values = value if is_multi_value and isinstance(value, list) else [value]
        return all(
            isinstance(item, str) and regex.fullmatch(item.strip()) is not None for item in values
        )

    async def _response(self, run: TenderPipelineRun) -> TenderPipelineRunResponse:
        stages = await self._runs.list_stages_for_runs([run.id])
        return self._response_from_stages(run, stages)

    @staticmethod
    def _response_from_stages(
        run: TenderPipelineRun, stages: list[TenderPipelineStage]
    ) -> TenderPipelineRunResponse:
        stage_order = {name: index for index, name in enumerate(_STAGES)}
        ordered = sorted(stages, key=lambda item: stage_order.get(item.stage_name, 999))
        return TenderPipelineRunResponse(
            id=run.id,
            document_version_id=run.document_version_id,
            status=run.status,
            attempt=run.attempt,
            downstream_analysis_run_id=run.downstream_analysis_run_id,
            pending_review=run.pending_review,
            stages=[
                TenderPipelineStageResponse.model_validate(item, from_attributes=True)
                for item in ordered
            ],
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
