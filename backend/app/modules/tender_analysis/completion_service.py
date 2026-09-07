"""bid_pipeline 人工审核完成后的原子持久化编排。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import DocumentVersion, ProjectDocument
from app.modules.tender_analysis.project_fact_projection import ProjectFactProjectionService
from app.modules.tender_analysis.repository import TenderPipelineRepository
from app.modules.tender_analysis.state_machine import transition
from app.modules.tender_analysis.tag_persistence_service import TenderTagPersistenceService
from app.modules.tender_analysis.workflow import TenderPipelineState


class TenderPipelineCompletionService:
    """协调审计标签、项目事实投影和运行终态；提交事务由 Worker 统一控制。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._runs = TenderPipelineRepository(session)
        self._tags = TenderTagPersistenceService(session)
        self._projection = ProjectFactProjectionService(session)

    async def finalize(self, state: TenderPipelineState) -> dict[str, object]:
        run_id = UUID(state["pipeline_run_id"])
        version_id = UUID(state["document_version_id"])
        run = await self._runs.get_run(run_id, for_update=True)
        if run is None or run.document_version_id != version_id:
            raise ValueError("管线运行与文档版本不匹配")
        if run.status != "RUNNING":
            raise ValueError(f"管线当前状态 {run.status} 不能执行最终持久化")
        is_current = await self._session.scalar(
            select(ProjectDocument.id)
            .join(DocumentVersion, DocumentVersion.document_id == ProjectDocument.id)
            .where(
                DocumentVersion.id == version_id,
                ProjectDocument.project_id == run.project_id,
                ProjectDocument.current_version_id == version_id,
                ProjectDocument.deleted_at.is_(None),
                DocumentVersion.parse_status == "READY",
            )
        )
        if is_current is None:
            raise ValueError("文档版本已被新版本替代，不能写入当前项目事实")

        extracted = _dict_items(state.get("extracted_tags"))
        reviewed = _dict_items(state.get("reviewed_tags"))
        reviewer_id = _reviewer_id(state.get("reviewed_by"))
        review_outcome = state.get("review_outcome")
        accepted = review_outcome == "approved"
        now = datetime.now(UTC)

        await self._tags.replace_run_tags(
            run_id=run_id,
            version_id=version_id,
            extracted=extracted,
            reviewed=reviewed,
            reviewer_id=reviewer_id,
            now=now,
        )
        if accepted:
            await self._projection.replace_confirmed_facts(
                run=run,
                version_id=version_id,
                reviewed=reviewed,
                reviewer_id=reviewer_id,
                now=now,
            )

        transition(run, "SUCCEEDED" if accepted else "CANCELLED")
        run.pending_review = None
        run.completed_at = now
        await self._finalize_stages(
            run_id,
            accepted=accepted,
            review_outcome=str(review_outcome or "rejected"),
            automatic_tags=len(extracted),
            reviewed_tags=len(reviewed),
            now=now,
        )
        return {}

    async def _finalize_stages(
        self,
        run_id: UUID,
        *,
        accepted: bool,
        review_outcome: str,
        automatic_tags: int,
        reviewed_tags: int,
        now: datetime,
    ) -> None:
        persist_stage = await self._runs.get_stage(run_id, "finalize", for_update=True)
        if persist_stage is not None:
            persist_stage.status = "SUCCEEDED" if accepted else "SKIPPED"
            persist_stage.output_summary = {
                "automatic_tags": automatic_tags,
                "reviewed_tags": reviewed_tags,
                "review_outcome": review_outcome,
            }
            persist_stage.completed_at = now
        human_stage = await self._runs.get_stage(run_id, "human_review", for_update=True)
        if human_stage is not None:
            human_stage.status = "SUCCEEDED"
            human_stage.output_summary = {
                **(human_stage.output_summary or {}),
                "decision": review_outcome,
            }
            human_stage.completed_at = now


def _reviewer_id(value: object) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _dict_items(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict):
        return {}
    return {str(code): item for code, item in value.items() if isinstance(item, dict)}
