"""人工确认标签到项目事实/资格需求的投影。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analysis.invalidation_service import AnalysisInvalidationService
from app.modules.documents.models import DocumentVersion
from app.modules.evidence.models import Evidence, EvidenceSourceNode
from app.modules.projects.models import TenderProject
from app.modules.requirements.models import ProjectField, TenderRequirement
from app.modules.tender_analysis.models import TenderPipelineRun, TenderTag

_MATCHABLE_QUALIFICATION_TAGS = frozenset(
    {
        "QUAL_BUSINESS_LICENSE",
        "QUAL_REGISTERED_CAPITAL",
        "QUAL_QUALIFICATION",
        "QUAL_SIMILAR_EXPERIENCE",
        "QUAL_FINANCIAL",
        "QUAL_CREDIT",
        "QUAL_TAX",
        "QUAL_SAFETY",
        "QUAL_PERSONNEL",
        "QUAL_EQUIPMENT",
        "QUAL_INSURANCE",
    }
)


class ProjectFactProjectionService:
    """将已人工确认的标签替换为当前文档版本的生效项目事实。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_confirmed_facts(
        self,
        *,
        run: TenderPipelineRun,
        version_id: UUID,
        reviewed: dict[str, dict[str, object]],
        reviewer_id: UUID | None,
        now: datetime,
    ) -> None:
        # Match 通过 requirement_id 外键引用当前需求；必须先失效派生结果，再替换投影。
        await AnalysisInvalidationService(self._session).invalidate_inputs({run.project_id})
        await self._delete_previous_projection(run.project_id, version_id)
        evidence_by_node = await self._evidence_by_node(run.project_id, version_id, reviewed)
        valid_evidence_ids = await self._valid_evidence_ids(
            run.project_id, version_id, reviewed
        )
        tag_names = await self._tag_names(reviewed)
        await self._project_deadline(run.project_id, reviewed, now)

        for code, item in reviewed.items():
            if not isinstance(item, dict):
                continue
            human_edited = bool(item.get("human_edited"))
            evidence_id = _source_evidence_id(item, evidence_by_node, valid_evidence_ids)
            self._session.add(
                ProjectField(
                    id=uuid4(),
                    project_id=run.project_id,
                    field_code=code,
                    value={"value": item.get("value")},
                    confidence=None,
                    review_status="CONFIRMED",
                    primary_evidence_id=evidence_id,
                    reviewed_at=now,
                    reviewed_by=reviewer_id,
                    review_note="由投标字段人工复核确认" if human_edited else "由招标字段自动确认",
                    extraction_source="PIPELINE_HUMAN" if human_edited else "PIPELINE_AUTO",
                    source_document_version_id=version_id,
                    source_pipeline_run_id=run.id,
                    created_at=now,
                    updated_at=now,
                )
            )
            if code not in _MATCHABLE_QUALIFICATION_TAGS:
                continue
            self._session.add(
                TenderRequirement(
                    id=uuid4(),
                    project_id=run.project_id,
                    category="QUALIFICATION",
                    title=tag_names.get(code, code),
                    description=str(item.get("note") or item.get("source_text") or ""),
                    conditions={"items": [{"dimension": code, "value": item.get("value")}]},
                    is_mandatory=code.startswith("QUAL_"),
                    score=None,
                    confidence=None,
                    review_status="CONFIRMED",
                    primary_evidence_id=evidence_id,
                    reviewed_at=now,
                    reviewed_by=reviewer_id,
                    review_note="由投标字段人工复核派生" if human_edited else "由招标字段自动确认派生",
                    extraction_source="PIPELINE_HUMAN" if human_edited else "PIPELINE_AUTO",
                    source_document_version_id=version_id,
                    source_pipeline_run_id=run.id,
                    created_at=now,
                    updated_at=now,
                )
            )

    async def _delete_previous_projection(self, project_id: UUID, version_id: UUID) -> None:
        """同一逻辑文档的新版本覆盖旧投影；其他当前附件的事实继续保留。"""
        document_id = await self._session.scalar(
            select(DocumentVersion.document_id).where(DocumentVersion.id == version_id)
        )
        if document_id is None:
            raise ValueError("文档版本不存在，无法替换项目事实投影")
        version_ids = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
        await self._session.execute(
            delete(ProjectField).where(
                ProjectField.project_id == project_id,
                ProjectField.extraction_source == "PIPELINE_HUMAN",
                ProjectField.source_document_version_id.in_(version_ids),
            )
        )
        await self._session.execute(
            delete(TenderRequirement).where(
                TenderRequirement.project_id == project_id,
                TenderRequirement.extraction_source == "PIPELINE_HUMAN",
                TenderRequirement.source_document_version_id.in_(version_ids),
            )
        )

    async def _evidence_by_node(
        self,
        project_id: UUID,
        version_id: UUID,
        reviewed: dict[str, dict[str, object]],
    ) -> dict[UUID, UUID]:
        node_ids = {
            node_id
            for item in reviewed.values()
            if isinstance(item, dict)
            and (node_id := _uuid_or_none(item.get("source_node_id"))) is not None
        }
        if not node_ids:
            return {}
        rows = await self._session.execute(
            select(EvidenceSourceNode.document_node_id, EvidenceSourceNode.evidence_id)
            .join(Evidence, Evidence.id == EvidenceSourceNode.evidence_id)
            .where(
                Evidence.project_id == project_id,
                Evidence.document_version_id == version_id,
                EvidenceSourceNode.document_node_id.in_(node_ids),
            )
            .order_by(EvidenceSourceNode.document_node_id, EvidenceSourceNode.ordinal)
        )
        result: dict[UUID, UUID] = {}
        for node_id, evidence_id in rows.all():
            result.setdefault(node_id, evidence_id)
        return result

    async def _valid_evidence_ids(
        self,
        project_id: UUID,
        version_id: UUID,
        reviewed: dict[str, dict[str, object]],
    ) -> set[UUID]:
        evidence_ids = {
            evidence_id
            for item in reviewed.values()
            if isinstance(item, dict)
            and (evidence_id := _uuid_or_none(item.get("source_evidence_id"))) is not None
        }
        if not evidence_ids:
            return set()
        rows = await self._session.scalars(
            select(Evidence.id).where(
                Evidence.project_id == project_id,
                Evidence.document_version_id == version_id,
                Evidence.id.in_(evidence_ids),
            )
        )
        return set(rows.all())

    async def _tag_names(self, reviewed: dict[str, dict[str, object]]) -> dict[str, str]:
        codes = [code for code in reviewed if isinstance(code, str)]
        if not codes:
            return {}
        rows = await self._session.execute(
            select(TenderTag.code, TenderTag.name).where(TenderTag.code.in_(codes))
        )
        return {code: name for code, name in rows.all()}

    async def _project_deadline(
        self,
        project_id: UUID,
        reviewed: dict[str, dict[str, object]],
        now: datetime,
    ) -> None:
        project = await self._session.get(TenderProject, project_id, with_for_update=True)
        if project is None or project.bid_deadline_source == "MANUAL":
            return
        item = reviewed.get("TIME_BID_DEADLINE")
        if not isinstance(item, dict):
            return
        deadline = parse_reviewed_deadline(item.get("value"))
        if deadline is not None:
            project.bid_deadline = deadline
            project.bid_deadline_source = "PIPELINE"
            project.updated_at = now


def _uuid_or_none(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _source_evidence_id(
    item: dict[str, object],
    evidence_by_node: dict[UUID, UUID],
    valid_evidence_ids: set[UUID],
) -> UUID | None:
    evidence_id = _uuid_or_none(item.get("source_evidence_id"))
    if evidence_id in valid_evidence_ids:
        return evidence_id
    node_id = _uuid_or_none(item.get("source_node_id"))
    return evidence_by_node.get(node_id) if node_id is not None else None


def parse_reviewed_deadline(value: object) -> datetime | None:
    """解析人工确认 ISO 时间；无时区值按 Asia/Shanghai 解释并统一转 UTC。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(UTC)
