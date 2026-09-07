"""bid_pipeline 的无状态节点实现。

实例由每个 Worker 任务创建并注入同一个短生命周期 AsyncSession；节点不自行创建
连接池。模型抽取节点后续单独接入，不能为了赶流程把全文与标签字典一次性提交给模型。
"""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.modules.documents.models import DocumentNode, DocumentVersion, ProjectDocument
from app.modules.evidence.models import Evidence
from app.modules.retrieval.models import EvidenceEmbedding
from app.modules.tender_analysis.models import TenderPipelineStage, TenderTag, TenderTagRelation
from app.modules.tender_analysis.workflow import TenderPipelineState

logger = logging.getLogger(__name__)

# 报告判断与项目事实需要的核心字段。标签库可以继续扩展，但不应驱动每次
# 文件上传都抽取所有类别；否则用户得到一百多条候选、模型也要处理数十个批次。
#
# 文件格式、盖章、密封、递交方式属于制作执行说明：报告直接附原文即可，既不需要
# 模型结构化提取，也不应把它们塞进需要人工填写的字段表单。
_REPORT_PROFILE_TAG_CODES = frozenset(
    {
        "PROJECT_NAME", "PROJECT_NUMBER", "TENDERER_NAME",
        "TIME_BID_DEADLINE", "TIME_BID_OPEN", "TIME_BID_OPEN_LOCATION", "TIME_CONTRACT_SIGN",
        "QUAL_BUSINESS_LICENSE", "QUAL_CREDIT", "QUAL_FINANCIAL",
        "QUAL_JOINT_BID", "QUAL_PERSONNEL", "QUAL_QUALIFICATION", "QUAL_SIMILAR_EXPERIENCE",
        "BID_BOND", "PERFORMANCE_BOND", "MAX_PRICE", "PROJECT_PERIOD_REQ", "WARRANTY_PERIOD",
        "EVAL_METHOD", "EVAL_PRICE_WEIGHT", "EVAL_TECH_WEIGHT", "EVAL_REJECT_CLAUSES",
        "CT_BREACH",
        "RISK_RESTRICTIVE", "RISK_PENALTY",
    }
)
# 只有这些字段的异常会打断自动流程。其他字段即使未命中，也在报告中按待核验
# 呈现，而不是制造一整张人工录入表。
_HUMAN_REVIEW_TAG_CODES = frozenset(
    {
        "PROJECT_NAME", "PROJECT_NUMBER", "TIME_BID_DEADLINE", "BID_BOND",
        "QUAL_BUSINESS_LICENSE", "QUAL_CREDIT", "QUAL_FINANCIAL",
        "QUAL_JOINT_BID", "QUAL_PERSONNEL", "QUAL_QUALIFICATION", "QUAL_SIMILAR_EXPERIENCE",
    }
)
_ATTACHMENT_REFERENCE_PATTERN = re.compile(r"附件|附表|附录|详见")
_PROFILE_EVIDENCE_LIMIT_PER_TAG = 3
_PROFILE_GLOBAL_EVIDENCE_LIMIT = 48
_PROMPT_KEYWORD_PATTERN = re.compile(r'["“]([^"”]{2,24})["”]')


class TenderPipelineNodes:
    """注入图的节点实现；只处理一条已授权、已持久化的管线运行。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preflight(self, state: TenderPipelineState) -> dict[str, object]:
        """一次完成投标分析前置校验。

        文档清洗、Evidence 构建和向量索引属于 Document Ingestion，不是 LangGraph
        的业务节点。这里仅验证这些前置产物已经 READY，避免在图里保留
        ``chunk/clean/index`` 这类只做计数检查的伪阶段。
        """
        version_id = UUID(state["document_version_id"])
        version = await self._session.get(DocumentVersion, version_id)
        if version is None or version.parse_status != "READY":
            raise ValueError("文档版本尚未完成解析，不能启动投标分析")
        document = await self._session.get(ProjectDocument, version.document_id)
        if (
            document is None
            or document.deleted_at is not None
            or document.current_version_id != version.id
        ):
            raise ValueError("文档版本已被新版本替代，请重新发起投标分析")

        usable_nodes = int(
            await self._session.scalar(
                select(func.count())
                .select_from(DocumentNode)
                .where(
                    DocumentNode.document_version_id == version_id,
                    DocumentNode.cleaned_content.is_not(None),
                )
            )
            or 0
        )
        evidence_count = int(
            await self._session.scalar(
                select(func.count()).select_from(Evidence).where(
                    Evidence.document_version_id == version_id
                )
            )
            or 0
        )
        indexed_count = int(
            await self._session.scalar(
                select(func.count())
                .select_from(EvidenceEmbedding)
                .join(Evidence, Evidence.id == EvidenceEmbedding.evidence_id)
                .where(Evidence.document_version_id == version_id)
            )
            or 0
        )
        if usable_nodes == 0:
            raise ValueError("解析结果为空或清洗后无可用正文，不能启动投标分析")
        if evidence_count == 0:
            raise ValueError("文档尚未生成 Evidence，不能进入投标分析")
        if indexed_count != evidence_count:
            raise ValueError(
                f"Evidence 向量索引不完整：{indexed_count}/{evidence_count}，请先完成文档解析"
            )
        await self._mark_stage(
            UUID(state["pipeline_run_id"]),
            "preflight",
            "SUCCEEDED",
            {
                "usable_node_count": usable_nodes,
                "evidence_count": evidence_count,
                "indexed_count": indexed_count,
            },
        )
        return {}

    async def select_candidates(self, state: TenderPipelineState) -> dict[str, object]:
        """按报告字段 Profile 逐标签召回少量 Evidence，而非按大类全量扫库。"""
        version_id = UUID(state["document_version_id"])
        evidences = list(
            (
                await self._session.scalars(
                    select(Evidence)
                    .where(
                        Evidence.document_version_id == version_id,
                        Evidence.quoted_text.is_not(None),
                    )
                    .order_by(Evidence.id)
                )
            ).all()
        )
        tags = await self._session.scalars(
            select(TenderTag).where(
                TenderTag.code.in_(_REPORT_PROFILE_TAG_CODES), TenderTag.is_active.is_(True)
            )
        )
        profile_tags = list(tags.all())
        evidence_candidate_tags: dict[str, set[str]] = {}
        selected_evidence_ids: set[str] = set()
        for tag in profile_tags:
            keywords = self._tag_keywords(tag)
            matches: list[tuple[int, int, str]] = []
            for evidence in evidences:
                locator = evidence.locator or {}
                text = f"{locator.get('section_path') or ''}\n{evidence.quoted_text or ''}"
                score = sum(keyword in text for keyword in keywords)
                # 基本信息通常出现在前几块，即便标题未命中也保留一个受控召回入口。
                if tag.code in {"PROJECT_NAME", "PROJECT_NUMBER", "TENDERER_NAME"} and (
                    locator.get("order_start") or 999
                ) <= 12:
                    score = max(score, 1)
                if score:
                    matches.append((score, -int(locator.get("order_start") or 0), str(evidence.id)))
            for _, _, evidence_id in sorted(matches, reverse=True)[:_PROFILE_EVIDENCE_LIMIT_PER_TAG]:
                if len(selected_evidence_ids) >= _PROFILE_GLOBAL_EVIDENCE_LIMIT:
                    break
                selected_evidence_ids.add(evidence_id)
                evidence_candidate_tags.setdefault(evidence_id, set()).add(tag.code)

        evidence_candidate_tags = {
            evidence_id: sorted(codes) for evidence_id, codes in evidence_candidate_tags.items()
        }
        candidate_codes = sorted({code for values in evidence_candidate_tags.values() for code in values})
        await self._mark_stage(
            UUID(state["pipeline_run_id"]),
            "select_candidates",
            "SUCCEEDED",
            {
                "profile_tag_count": len(profile_tags),
                "candidate_evidences": len(evidence_candidate_tags),
                "candidate_count": len(candidate_codes),
            },
        )
        return {
            "candidate_tag_codes": candidate_codes,
            "evidence_candidate_tags": evidence_candidate_tags,
        }

    @staticmethod
    def _tag_keywords(tag: TenderTag) -> tuple[str, ...]:
        """仅从受控标签定义派生召回词，避免文档内容反向污染标签选择。"""
        prompt = tag.extraction_prompt or ""
        quoted = _PROMPT_KEYWORD_PATTERN.findall(prompt)
        name = tag.name.replace("要求", "").replace("条款", "").strip()
        # 太短的单字词会把无关正文拉进来；最长词优先使排序稳定。
        return tuple(sorted({item.strip() for item in [name, *quoted] if len(item.strip()) >= 2}, key=len, reverse=True))

    async def validate(self, state: TenderPipelineState) -> dict[str, object]:
        """校验 P0 缺失、类型、关联关系和低置信度，为强制人工复核标记重点问题。"""
        extracted = state.get("extracted_tags", {})
        active_tags = list(
            (
                await self._session.scalars(
                    select(TenderTag).where(
                        TenderTag.code.in_(_REPORT_PROFILE_TAG_CODES),
                        TenderTag.is_active.is_(True),
                    )
                )
            ).all()
        )
        tags_by_code = {tag.code: tag for tag in active_tags}
        required_codes = {
            tag.code for tag in active_tags
            if tag.code in _HUMAN_REVIEW_TAG_CODES and (tag.is_required or tag.level_code == "P0")
        }
        valid_extracted_codes = {
            code
            for code, item in extracted.items()
            if code in tags_by_code
            and isinstance(item, dict)
            and self._value_matches_tag(item.get("value"), tags_by_code[code])
        }
        # 类型异常由系统按“未可靠提取”处理，不要求业务人员理解 JSON、日期等技术格式。
        missing = sorted(required_codes - valid_extracted_codes)
        attachment_references = sorted(
            code
            for code, item in extracted.items()
            if code in _HUMAN_REVIEW_TAG_CODES
            and isinstance(item, dict)
            and _ATTACHMENT_REFERENCE_PATTERN.search(
                f"{item.get('source_section_path') or ''}\n{item.get('source_text') or ''}"
            )
        )
        issues = [*(f"MISSING_REQUIRED:{code}" for code in missing)]
        issues.extend(f"ATTACHMENT_REFERENCE:{code}" for code in attachment_references)
        relations = list(
            (
                await self._session.scalars(
                    select(TenderTagRelation).where(
                        TenderTagRelation.relation_type.in_(
                            {"BEFORE", "EQUAL_OR_BEFORE", "DEPENDS_ON"}
                        )
                    )
                )
            ).all()
        )
        issues.extend(
            issue for issue in self._validate_relations(extracted, relations)
            if any(code in issue for code in _HUMAN_REVIEW_TAG_CODES)
        )
        extraction_failures = state.get("extraction_failures", [])
        if isinstance(extraction_failures, list):
            issues.extend(
                f"EXTRACTION_BATCH_FAILED:{item.get('batch_index')}"
                for item in extraction_failures
                if isinstance(item, dict) and isinstance(item.get("batch_index"), int)
            )
        review_codes = {
            part
            for issue in issues
            if isinstance(issue, str)
            for part in issue.split(":")[1:]
            if part in _HUMAN_REVIEW_TAG_CODES
        }
        valid_codes = {
            code for code, item in extracted.items()
            if code in tags_by_code
            and code not in review_codes
            and isinstance(item, dict)
            and self._value_matches_tag(item.get("value"), tags_by_code[code])
        }
        await self._mark_stage(
            UUID(state["pipeline_run_id"]),
            "validate",
            # 校验节点本身已经结束；真正的等待状态属于后续 human_review 节点。
            "SUCCEEDED",
            {"issues": len(issues), "review_required": bool(review_codes), "auto_confirmed_count": len(valid_codes)},
        )
        return {
            "validation_issues": issues,
            "review_tag_codes": sorted(review_codes),
            "auto_approved_tag_codes": sorted(valid_codes),
        }

    @staticmethod
    def _validate_relations(
        extracted: dict[str, dict[str, object]], relations: list[TenderTagRelation]
    ) -> list[str]:
        """校验可确定的标签关系，并把结果交给同一人工审核节点。

        关系表中的金额公式可能依赖合同价、含税口径等上下文；这类没有足够事实输入的
        公式不在这里猜算。日期与字段依赖则能以当前标签事实确定，适合自动发现矛盾。
        """
        issues: list[str] = []
        for relation in relations:
            source = extracted.get(relation.source_tag_code)
            target = extracted.get(relation.target_tag_code)
            if relation.relation_type == "DEPENDS_ON":
                if source is not None and target is None:
                    issues.append(
                        f"RELATION_DEPENDS_ON:{relation.source_tag_code}:{relation.target_tag_code}"
                    )
                continue
            if source is None or target is None:
                continue
            source_time = TenderPipelineNodes._parse_datetime(source.get("value"))
            target_time = TenderPipelineNodes._parse_datetime(target.get("value"))
            if source_time is None or target_time is None:
                continue
            if relation.relation_type == "BEFORE" and source_time >= target_time:
                issues.append(
                    f"RELATION_BEFORE:{relation.source_tag_code}:{relation.target_tag_code}"
                )
            elif relation.relation_type == "EQUAL_OR_BEFORE" and source_time > target_time:
                issues.append(
                    f"RELATION_EQUAL_OR_BEFORE:{relation.source_tag_code}:{relation.target_tag_code}"
                )
        return issues

    @staticmethod
    def _value_matches_tag(value: object, tag: TenderTag) -> bool:
        """在进入人工审核前发现明显的模型类型错误，不做自然语言猜测或自动修正。"""
        if value is None:
            return False
        if tag.is_multi_value:
            return (
                isinstance(value, list)
                and bool(value)
                and all(TenderPipelineNodes._value_matches_data_type(item, tag.data_type) for item in value)
            )
        return TenderPipelineNodes._value_matches_data_type(value, tag.data_type)

    @staticmethod
    def _value_matches_data_type(value: object, data_type: str) -> bool:
        if data_type == "string":
            return isinstance(value, str) and bool(value.strip())
        if data_type == "number":
            if isinstance(value, bool):
                return False
            try:
                float(value)
                return True
            except (TypeError, ValueError):
                return False
        if data_type == "datetime":
            return TenderPipelineNodes._parse_datetime(value) is not None
        if data_type == "boolean":
            return isinstance(value, bool)
        if data_type == "array":
            return isinstance(value, list) and bool(value)
        if data_type == "json":
            return isinstance(value, (dict, list)) and bool(value)
        return False

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        """只接受标签字典约定的 ISO 日期时间，不从自然语言日期猜测。"""
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed

    async def mark_stage_failed(
        self, pipeline_run_id: str, stage_name: str, exc: BaseException
    ) -> None:
        """节点抛错时独立记录阶段失败，让运维从 API 就能定位失败步骤。

        上层会把主事务整体 rollback，阶段表更新若写在同一个 Session 里会被一起丢弃，
        导致所有阶段永远停留在 PENDING。这里用一个独立短事务提交，既不影响主事务的
        回滚语义，也能把失败阶段和可诊断信息固化下来。
        """
        try:
            async with get_session_factory()() as session:
                stage = await session.scalar(
                    select(TenderPipelineStage).where(
                        TenderPipelineStage.pipeline_run_id == UUID(pipeline_run_id),
                        TenderPipelineStage.stage_name == stage_name,
                    )
                )
                if stage is not None:
                    stage.status = "FAILED"
                    stage.error_message = f"{type(exc).__name__}: {exc}"[:500]
                    await session.commit()
        except Exception:
            logger.warning(
                "标记投标管线阶段失败状态出错 pipeline_run_id=%s stage=%s",
                pipeline_run_id,
                stage_name,
                exc_info=True,
            )

    async def _mark_stage(
        self,
        pipeline_run_id: UUID,
        stage_name: str,
        status: str,
        output_summary: dict[str, object] | None = None,
    ) -> None:
        """阶段记录仅保存计数等摘要，避免把原文或模型上下文重复写入数据库。"""
        stage = await self._session.scalar(
            select(TenderPipelineStage)
            .where(
                TenderPipelineStage.pipeline_run_id == pipeline_run_id,
                TenderPipelineStage.stage_name == stage_name,
            )
            .with_for_update()
        )
        if stage is None:
            return
        stage.status = status
        stage.output_summary = output_summary
