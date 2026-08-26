import concurrent.futures
import hashlib
import json
import logging
import re
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.constants import LLM_MODEL_ID
from app.db.models import AiRun, ProjectField, Requirement, RequirementEvidence
from app.db.repositories.clause_repository import ClauseRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.evidence_repository import EvidenceRepository
from app.db.repositories.project_field_repository import ProjectFieldRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.integrations.ai.llm import LlmUnavailable, RequirementLlm
from app.schemas.extraction import (
    ProjectFieldCandidate,
    RequirementCandidate,
    RequirementExtractionResult,
)
from app.services.module_router import ModuleRouter, RoutedNode
from app.services.project_field_registry import (
    canonical_project_field_code,
    compatible_project_field_codes,
)
from app.services.rule_extractor import RuleExtractor
from app.services.task_service import RetryableDocumentTaskError, TaskService

logger = logging.getLogger(__name__)


def _canonical_ai_input(value: object) -> str:
    """Serialize audit payloads deterministically, including UUID evidence anchors."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


# ---------------------------------------------------------------------------
# Category 映射（模块级常量，避免每次调用重新构造）
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = frozenset({"PROJECT", "QUALIFICATION", "BUSINESS", "SCORING"})
_HARD_REQUIREMENT_SIGNAL = re.compile(
    r"不得|必须|应当|须(?!知)|否决|废标|无效|不予|需提供|应提供|应具备|应满足|"
    r"应(?=在|于|按|提供|提交|具备|满足|递交|签署|加盖|遵守|承担|对|以)|"
    r"(?:资质|业绩|财务|信用|信誉|人员|项目经理)[^。；\n]{0,20}?要求\s*[：:]\s*(?:提供|具备|满足)|"
    r"(?:投标人(?!须知)|供应商|申请人|联合体)[^。；\n]{0,100}?"
    r"(?:未(?:在[^。；\n]{0,60})?(?:被)?列入|未曾有|不存在)"
)
_REQUIREMENT_CUE = re.compile(
    r"资格|资质|业绩|人员|证书|保证金|报价|限价|工期|交货|递交|开标|"
    r"评分|评审|技术参数|验收|付款|履约|保修|合同|投标文件|响应文件"
)
_QUANTITATIVE_CUE = re.compile(r"\d+(?:\.\d+)?\s*(?:年|月|日|天|%|分|万元|元|项|人|台|套)")

# 精确关键词（最长匹配优先）
_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("QUALIFICATION", [
        "资格审查", "资格预审", "投标人资格", "资质要求", "资质审查", "资格要求",
        "入围资格", "资质认定", "资格认定", "投标人资质", "营业执照", "联合体",
        "业绩要求", "人员要求", "证书要求",
    ]),
    ("BUSINESS", [
        "投标文件要求", "评标办法", "备选方案", "分包要求", "履约保证金", "投标保证金",
        "合同条款", "支付方式", "验收标准", "变更程序", "质疑程序", "澄清程序",
        "补偿程序", "履约要求", "担保要求", "违约责任", "偏离条款", "实质性要求",
        "商务条款", "商务要求", "保管责任", "卸货责任", "移交手续", "费用承担",
        "检验费用", "总承包服务费", "运输责任", "质保期", "现场管理", "竣工资料",
        "缺陷责任期", "保修", "材料设备", "材料与设备", "风险转移", "知识产权", "中标",
    ]),
    ("PROJECT", [
        "招标范围", "项目范围", "工程范围", "采购需求", "交货期", "交货时间",
        "工期要求", "完工日期", "项目概况", "招标公告", "招标项目", "项目编号", "招标编号",
    ]),
    ("SCORING", [
        "评分标准", "评标标准", "评审标准", "技术标准", "规格要求", "质量标准",
        "施工标准", "技术要求", "性能指标", "评分办法", "分值设置", "标准要求",
    ]),
]

# 回退正则（按类别特征词）
_CATEGORY_FALLBACK_REGEX: list[tuple[str, "re.Pattern[str]"]] = [
    ("QUALIFICATION", re.compile(r"资格|资质|审查|预审|入围|营业执照")),
    ("BUSINESS", re.compile(
        r"评标|投标文件|备选|分包|履约|商务|付款|支付|保函|保证金|合同|违约|验收|变更|"
        r"质疑|澄清|补偿|实质性|时序|安装|担保|保管|卸货|移交|费用|检验|运输|现场管理|"
        r"竣工资料|缺陷责任|保修|材料|风险转移|知识产权|中标"
    )),
    ("PROJECT", re.compile(r"项目|招标|工程|范围|需求|交货|工期|完工")),
    ("SCORING", re.compile(
        r"评分|评审|技术|性能|方案|规格|质量|施工|价格|综合|权重|分值|优选|标准"
    )),
]


def _normalize_category(cat: str) -> str:
    """将中文 category 映射到英文枚举；保留未知值。最长匹配优先。"""
    if not cat:
        return cat
    if cat in _VALID_CATEGORIES:
        return cat

    # 最长关键词匹配
    best_match: str | None = None
    best_len = 0
    for cat_enum, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            if kw in cat and len(kw) > best_len:
                best_match = cat_enum
                best_len = len(kw)
    if best_match:
        return best_match

    # 回退：按类别特征词正则匹配
    for cat_enum, pattern in _CATEGORY_FALLBACK_REGEX:
        if pattern.search(cat):
            return cat_enum

    return cat  # 未知类别保留原文


class RequirementExtractionService:
    """招标文件需求抽取服务：规则引擎 + 模块路由 LLM 并发抽取。"""

    # ---- 配置常量 ----
    _EXTRACTION_BATCH_SIZE = 60          # 单批最大节点数（参考）
    _MAX_CONCURRENT_MODULES = 4         # 最大并发模块数
    _MAX_PROJECT_FIELDS = 12            # 最终保留的项目字段数
    _MAX_REQUIREMENTS = 100             # 最终保留的需求条数
    _MAX_LLM_CANDIDATES = 72            # 全文只保留高价值候选，避免全文灌入模型
    _MAX_LLM_CANDIDATES_PER_MODULE = 18 # 同类、小批调用，保证输出稳定和可追溯
    _MAX_ACTIVE_REVIEW_ITEMS = 20       # Human-in-the-loop 一次只处理高价值队列
    _AUTO_CONFIRM_CONFIDENCE = Decimal("0.95")
    _RETRY_ATTEMPTS = 2                 # 小批失败后交给规则回退，避免无界重试
    _CONFIDENCE_THRESHOLD = Decimal("0.70")  # 低置信度阈值
    _LOW_CONFIDENCE_PENALTY = Decimal("0.9")  # 低置信度降权系数

    def __init__(self, session: Session, llm: RequirementLlm) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._clauses = ClauseRepository(session)
        self._evidences = EvidenceRepository(session)
        self._project_fields = ProjectFieldRepository(session)
        self._requirements = RequirementRepository(session)
        self._llm = llm
        self._tasks = TaskService(session)
        self._rule_extractor: RuleExtractor | None = None
        self._module_router = ModuleRouter()

    # ==================================================================
    # 公共入口
    # ==================================================================

    def process(self, task_id: UUID, document_version_id: UUID) -> None:
        """任务调度入口。"""
        task = self._tasks.start_extraction(task_id, document_version_id)
        if task is None:
            return
        try:
            self.do_extract(document_version_id)
            self._tasks.complete_extraction(task.id, document_version_id, 0)
            self._retry_low_confidence_fields_from_version(document_version_id)
        except LlmUnavailable:
            self._retry_or_fail(
                task.id,
                document_version_id,
                "AI_SERVICE_UNAVAILABLE",
                "Requirement 抽取模型未配置或不可用。",
            )
        except Exception:
            logger.exception("Requirement 抽取失败")
            self._fail(
                task.id,
                document_version_id,
                "EXTRACTION_FAILED",
                f"Requirement 抽取失败: {traceback.format_exc()[:200]}",
            )

    def do_extract(self, document_version_id: UUID) -> int:
        """核心抽取逻辑，不含 TaskService 管理。返回 persisting count。"""
        version = self._documents.get_version(document_version_id)
        document = None if version is None else self._documents.get_document(version.document_id)
        if version is None or document is None or document.project_id is None:
            raise ValueError("invalid tender document")

        source_units = self._load_extraction_units(document_version_id)
        if not source_units:
            logger.warning("[Requirement] 无可抽取节点，跳过")
            return 0

        # ---- 第一层：规则抽取（毫秒级，零 token）----
        self._rule_extractor = RuleExtractor(self._session, document.project_id)
        raw_nodes = [
            {"id": unit["id"], "content": unit["content"]}
            for unit in source_units
        ]
        rule_results = self._rule_extractor.process_nodes(raw_nodes)

        # 建立 node_id → evidence_id 映射，用于写入 ProjectField.primary_evidence_id
        evidence_map = {unit["id"]: unit.get("evidence_id") for unit in source_units}

        rule_persisted = self._rule_extractor.persist_results(rule_results, evidence_map)
        if rule_persisted > 0:
            self._session.commit()
            logger.info(f"[Requirement] 规则抽取完成，写入 {rule_persisted} 个字段")

        # ---- 第二层：带标签条款先分流，再让 LLM 补歧义 ----
        # 单一业务域 + 明确义务词的条款可以完整保留为可复核 Requirement，
        # 不需要让模型重新解释。模型只接收规则无法无损归类的候选。
        rule_units, ambiguous_units = self._route_labeled_units(source_units)
        evidence_by_order = {
            unit["order_no"]: evidence_map.get(unit["id"]) for unit in source_units
        }
        rule_requirement_persisted = self._persist_rule_requirements(
            document.project_id, rule_units, evidence_by_order
        )
        nodes_for_llm = self._select_llm_candidates(ambiguous_units)
        if not nodes_for_llm:
            logger.info("[Requirement] 没有需要 LLM 判定的候选条款")
            self._prioritize_review_queue(document.project_id)
            self._session.commit()
            return rule_persisted + rule_requirement_persisted
        candidates, _ai_run = self._extract_batched_by_module(None, nodes_for_llm)
        persisted = self._persist_candidates(
            document.project_id,
            candidates,
            nodes_for_llm,
            evidence_by_order,
        )
        if not candidates.requirements:
            persisted += self._persist_rule_clause_fallback(
                document.project_id, nodes_for_llm, evidence_by_order
            )
        if not candidates.requirements and persisted == 0 and rule_requirement_persisted == 0:
            persisted += self._persist_legacy_tag_fallback(document.project_id, document_version_id)
        self._prioritize_review_queue(document.project_id)
        self._session.commit()
        return rule_persisted + rule_requirement_persisted + persisted

    @staticmethod
    def _route_labeled_units(units: list[dict]) -> tuple[list[dict], list[dict]]:
        """Separate deterministic clauses from the genuinely ambiguous LLM queue.

        Current parser versions persist ``node_labels`` all the way to
        ``TenderClause.quality_metadata``. Explicit blocking clauses retain a
        rule path even outside the finite LLM budget. Historical clauses
        without labels keep the former safe behaviour: they are eligible for
        the later keyword-based LLM filter, never promoted to a direct rule.
        """
        rule_units: list[dict] = []
        ambiguous_units: list[dict] = []
        skipped = 0
        for unit in units:
            labels = unit.get("node_labels") or {}
            if not labels:
                ambiguous_units.append(unit)
                continue
            if labels.get("noise") or not labels.get("requirement_candidate"):
                skipped += 1
                continue

            # Current label policies distinguish requirements imposed on the
            # bidder from contract-party and evaluator process statements.
            # Older persisted labels lack this field and retain the compatible
            # path below until the document is re-cleaned.
            analysis_scope = labels.get("analysis_scope")
            if analysis_scope == "NON_BIDDER_PROCESS":
                skipped += 1
                continue

            # A blocking statement is a deterministic, evidence-backed fact.
            # It must survive a finite LLM input budget; still retain PENDING
            # review rather than auto-confirming it downstream.
            if (
                labels.get("blocking_signal")
                and analysis_scope != "SCORING_CRITERIA"
            ):
                rule_units.append({
                    **unit,
                    "extraction_route": "RULE",
                    "outside_llm_budget": not labels.get(
                        "selected_candidate", labels["requirement_candidate"]
                    ),
                })
                continue

            domains = {str(domain) for domain in labels.get("domains", []) if domain}
            # A single-domain mandatory clause is deterministic and must not
            # be discarded merely because its LLM candidate slot was consumed
            # elsewhere. ClauseCandidateRecall records this as RULE_SINGLE_DOMAIN;
            # keep the same semantics at extraction time.
            if (
                analysis_scope != "SCORING_CRITERIA"
                and labels.get("mandatory_signal")
                and len(domains) == 1
            ):
                rule_units.append({
                    **unit,
                    "extraction_route": "RULE",
                    "outside_llm_budget": not labels.get(
                        "selected_candidate", labels["requirement_candidate"]
                    ),
                })
                continue

            # selected_candidate is the clean-stage section budget. Old label
            # records predate it, so use requirement_candidate as a compatible
            # default rather than dropping valid historical clauses.
            if not labels.get("selected_candidate", labels["requirement_candidate"]):
                skipped += 1
                continue

            reason = "多业务域" if len(domains) > 1 else "缺少明确义务词或量化语义"
            ambiguous_units.append({
                **unit,
                "extraction_route": "LLM",
                "ambiguity_reason": reason,
            })
        logger.info(
            "[Requirement] 标签分流: rule=%d, llm_ambiguous=%d, skipped=%d",
            len(rule_units), len(ambiguous_units), skipped,
        )
        return rule_units, ambiguous_units

    def _persist_rule_requirements(
        self,
        project_id: UUID,
        units: list[dict],
        evidence_by_order: dict[int, UUID | None],
    ) -> int:
        """Persist single-domain mandatory clauses directly, with full evidence provenance."""
        persisted = 0
        seen_keys: set[tuple[str, str]] = set()
        for unit in units:
            content = (unit.get("content") or "").strip()
            if not content:
                continue
            category = self._infer_rule_category(content, unit.get("section_path"))
            title = self._rule_requirement_title(content)
            if not title:
                continue
            key = (category, re.sub(r"[\s\W_]+", "", title, flags=re.UNICODE).lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidate = RequirementCandidate(
                category=category,
                title=title,
                description=content,
                conditions={
                    "all": [{"dimension": "evidence", "operator": "REQUIRED", "value": True}]
                },
                is_mandatory=self._is_unit_hard_requirement(unit),
                score=None,
                confidence=Decimal("0.93"),
                evidence_order_nos=[unit["order_no"]],
            )
            evidence_id = evidence_by_order.get(unit["order_no"])
            if evidence_id is None:
                logger.warning(
                    "[Requirement] 跳过无 Evidence 的规则条款: order_no=%s",
                    unit["order_no"],
                )
                continue
            self._upsert(
                project_id,
                candidate,
                order_nos=[unit["order_no"]],
                evidence_ids=[evidence_id] if evidence_id else [],
                extraction_source="rule",
            )
            persisted += 1
        logger.info("[Requirement] 规则确定性条款写入 %d 条", persisted)
        return persisted

    def _persist_rule_clause_fallback(
        self,
        project_id: UUID,
        units: list[dict],
        evidence_by_order: dict[int, UUID | None],
    ) -> int:
        """将已标注的强业务条款降级为可复核 Requirement，避免模型格式失败丢业务事实。"""
        persisted = 0
        seen_keys: set[tuple[str, str]] = set()
        for unit in units:
            labels = set(unit.get("labels") or [])
            if "强制性条款" not in labels and "量化约束" not in labels:
                continue
            content = (unit.get("content") or "").strip()
            if not content:
                continue
            category = self._infer_rule_category(content, unit.get("section_path"))
            title = self._rule_requirement_title(content)
            if not title:
                continue
            key = (category, re.sub(r"[\s\W_]+", "", title, flags=re.UNICODE).lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidate = RequirementCandidate(
                category=category,
                title=title,
                description=content,
                conditions={
                    "all": [{"dimension": "evidence", "operator": "REQUIRED", "value": True}]
                },
                is_mandatory=self._is_unit_hard_requirement(unit),
                score=None,
                confidence=Decimal("0.90") if "强制性条款" in labels else Decimal("0.78"),
                evidence_order_nos=[unit["order_no"]],
            )
            evidence_id = evidence_by_order.get(unit["order_no"])
            if evidence_id is None:
                logger.warning(
                    "[Requirement] 跳过无 Evidence 的规则回退条款: order_no=%s",
                    unit["order_no"],
                )
                continue
            self._upsert(
                project_id,
                candidate,
                order_nos=[unit["order_no"]],
                evidence_ids=[evidence_id],
                extraction_source="rule",
            )
            persisted += 1
        logger.info("[Requirement] 规则条款回退写入 %d 条", persisted)
        return persisted

    @staticmethod
    def _infer_rule_category(content: str, section_path: str | None = None) -> str:
        context = f"{section_path or ''}\n{content}"
        if re.search(r"评分|评审|分值|得分", context):
            return "SCORING"
        if re.search(r"资格|资质|业绩|人员|证书|财务|审计|信用|失信|黑名单", context):
            return "QUALIFICATION"
        # "本项目"、"项目负责人"等会出现在几乎所有投标人义务中；不能仅因出现
        # "项目"二字就把递交、解密等操作性要求误判成项目事实。
        if re.search(
            r"招标范围|项目范围|工程范围|采购需求|交货期|交货时间|"
            r"工期要求|计划工期|合同工期|完工日期|项目概况|项目名称|"
            r"项目编号|招标编号",
            context,
        ):
            return "PROJECT"
        return "BUSINESS"

    @staticmethod
    def _rule_requirement_title(content: str) -> str:
        """优先使用首个完整句作为可读标题，避免把整段条款当标题。"""
        # 先去掉 clause builder 补入的章节行，再压缩正文空白；顺序不能颠倒。
        body = re.sub(r"^章节：[^\n]+\n?", "", content.strip())
        compact = re.sub(r"\s+", " ", body)
        sentence = re.split(r"[。；;\n]", compact, maxsplit=1)[0].strip(" ：:")
        return sentence[:120]

    @staticmethod
    def _is_unit_hard_requirement(unit: dict) -> bool:
        """Classify rule output by its actual duty text, not its extraction route."""
        labels = unit.get("node_labels") or {}
        if labels.get("analysis_scope") == "NON_BIDDER_PROCESS":
            return False
        if not (labels.get("mandatory_signal") or labels.get("blocking_signal")):
            return False
        source = f"{unit.get('section_path') or ''}\n{unit.get('content') or ''}"
        return bool(_HARD_REQUIREMENT_SIGNAL.search(source))

    @staticmethod
    def _select_llm_candidates(units: list[dict]) -> list[dict]:
        """Only retain ambiguous labelled candidates; legacy rows use a safe keyword fallback."""
        selected: list[dict] = []
        seen_content: set[str] = set()
        for unit in units:
            content = (unit.get("content") or "").strip()
            if not content:
                continue
            normalized = re.sub(r"\s+", "", content)
            if normalized in seen_content:
                continue
            seen_content.add(normalized)

            node_labels = unit.get("node_labels") or {}
            labels: list[str] = []
            score = 0
            mandatory = bool(node_labels.get("mandatory_signal")) or bool(
                _HARD_REQUIREMENT_SIGNAL.search(content)
            )
            quantitative = bool(node_labels.get("quantitative_signal")) or bool(
                _QUANTITATIVE_CUE.search(content)
            )
            if mandatory:
                labels.append("强制性条款")
                score += 100
            if node_labels.get("domains") or _REQUIREMENT_CUE.search(content):
                labels.append("业务要求候选")
                score += 30
            if quantitative:
                labels.append("量化约束")
                score += 20
            if "章节：" in content:
                labels.append("章节上下文")
                score += 5
            category = RequirementExtractionService._infer_rule_category(
                content, unit.get("section_path")
            )
            labels.append(f"类别:{category}")
            if unit.get("ambiguity_reason"):
                labels.append(f"歧义:{unit['ambiguity_reason']}")
            # 纯叙述、目录、页眉页脚不进入 LLM；它们没有待判定的业务价值。
            if not labels or score < 30:
                continue
            selected.append({
                **unit,
                # ClauseCandidateRecall has already bounded the number of
                # complete semantic clauses.  Do not mutilate a source fact
                # at this final hand-off to the LLM.
                "content": content,
                "labels": labels,
                "candidate_score": score,
            })

        selected.sort(key=lambda item: (-item["candidate_score"], item["order_no"]))
        result = selected[: RequirementExtractionService._MAX_LLM_CANDIDATES]
        logger.info(
            "[Requirement] 条款候选压缩: %d -> %d（仅将带业务标签的候选交给 LLM）",
            len(units), len(result),
        )
        return result

    # ==================================================================
    # 节点加载
    # ==================================================================

    def _load_candidate_nodes(self, document_version_id: UUID) -> list:
        """SQL 层面过滤候选节点；为空时回退到全量 indexable 节点。"""
        candidates = [
            node
            for node in self._documents.list_nodes_candidates(document_version_id, 0, 1_000_000)
            if node.cleaning_metadata.get("indexable")
            and node.cleaned_content
            and node.tender_req_candidate
        ]
        if candidates:
            return candidates

        logger.info("[Requirement] 候选节点为空，回退到全量 indexable 节点")
        return [
            node
            for node in self._documents.list_nodes(document_version_id, 0, 1_000_000)
            if node.cleaning_metadata.get("indexable") and node.cleaned_content
        ]

    def _load_extraction_units(self, document_version_id: UUID) -> list[dict]:
        """Prefer clause-level units; keep the node path as a safe compatibility fallback."""
        clauses = self._clauses.list_for_version(document_version_id)
        if clauses:
            evidence = self._clauses.primary_evidence_ids([clause.id for clause in clauses])
            return [
                {
                    "id": str(clause.id),
                    "order_no": clause.order_no + 1,
                    "page_number": clause.start_page,
                    "section_path": clause.section_path,
                    "content": clause.contextualized_content,
                    "evidence_id": evidence.get(clause.id),
                    "node_labels": (clause.quality_metadata or {}).get("node_labels", {}),
                }
                for clause in clauses
                if clause.content
            ]

        nodes = self._load_candidate_nodes(document_version_id)
        all_evidence = self._evidences.list_for_version(document_version_id)
        evidence = {
            node.id: ev.id for ev in all_evidence
            if ev.document_node_id is not None
            for node in nodes if ev.document_node_id == node.id
        }
        return [
            {
                "id": str(node.id), "order_no": node.order_no or 0,
                "page_number": node.page_number, "content": node.cleaned_content or "",
                "section_path": node.section_path,
                "evidence_id": evidence.get(node.id),
                "node_labels": (node.cleaning_metadata or {}).get("node_labels", {}),
            }
            for node in nodes
        ]

    # ==================================================================
    # 结果持久化
    # ==================================================================

    def _persist_candidates(
        self,
        project_id: UUID,
        candidates: RequirementExtractionResult,
        nodes_for_llm: list[dict],
        evidence_by_order: dict[int, UUID | None],
    ) -> int:
        """将 LLM 抽取结果写入数据库，返回写入条数。"""
        allowed_order_nos = {
            int(node["order_no"])
            for node in nodes_for_llm
            if node.get("order_no") is not None
        }
        persisted = 0

        for candidate in candidates.project_fields:
            order_nos = self._resolve_order_nos(
                candidate.evidence_order_nos, allowed_order_nos
            )
            if not order_nos:
                logger.warning(
                    "[Requirement] 丢弃无有效 Evidence 锚点的项目字段: %s",
                    candidate.field_code,
                )
                continue
            evidence_ids = [
                evidence_by_order[order_no]
                for order_no in order_nos
                if evidence_by_order.get(order_no)
            ]
            if not evidence_ids:
                logger.warning(
                    "[Requirement] 丢弃无法解析 Evidence 的项目字段: %s",
                    candidate.field_code,
                )
                continue
            self._upsert_project_field(project_id, candidate, evidence_ids=evidence_ids)
            persisted += 1

        for candidate in candidates.requirements:
            original_cat = candidate.category
            candidate.category = _normalize_category(candidate.category)
            if candidate.category not in _VALID_CATEGORIES:
                logger.debug(
                    f"[Requirement] 跳过未知 category: {original_cat!r} → {candidate.category}"
                )
                continue
            order_nos = self._resolve_order_nos(candidate.evidence_order_nos, allowed_order_nos)
            if not order_nos:
                logger.warning(
                    "[Requirement] 丢弃无有效 Evidence 锚点的模型候选: %s",
                    candidate.title,
                )
                continue
            evidence_ids = [
                evidence_by_order[order_no]
                for order_no in order_nos
                if evidence_by_order.get(order_no)
            ]
            if not evidence_ids:
                logger.warning(
                    "[Requirement] 丢弃无法解析 Evidence 的模型候选: %s",
                    candidate.title,
                )
                continue
            self._upsert(
                project_id,
                candidate,
                order_nos=order_nos,
                evidence_ids=evidence_ids,
            )
            persisted += 1

        return persisted

    @staticmethod
    def _resolve_order_nos(raw: list[int] | None, allowed_order_nos: set[int]) -> list[int]:
        """Keep only Evidence anchors that exist in this LLM input batch.

        Falling back to the first source clause hides a model citation error and
        makes a report look supported when it is not.  Invalid or absent anchors
        must be rejected rather than silently repaired.
        """
        if not raw:
            return []
        return list(dict.fromkeys(
            order_no
            for order_no in raw
            if isinstance(order_no, int) and order_no > 0 and order_no in allowed_order_nos
        ))

    def _retry_low_confidence_fields_from_version(self, document_version_id: UUID) -> None:
        """从 document_version_id 找到 project_id 并执行低置信度降权。"""
        version = self._documents.get_version(document_version_id)
        if version is None:
            return
        document = self._documents.get_document(version.document_id)
        if document is None or document.project_id is None:
            return
        self._apply_low_confidence_penalty(document.project_id)

    def _upsert_project_field(
        self,
        project_id: UUID,
        candidate: ProjectFieldCandidate,
        *,
        evidence_ids: list[UUID],
    ) -> None:
        """Write a pending LLM field with its validated primary Evidence."""
        field_code = canonical_project_field_code(candidate.field_code)
        field = self._project_fields.find_by_codes(
            project_id, compatible_project_field_codes(field_code), for_update=True
        )
        now = datetime.now(UTC)
        if field is not None and field.review_status == "CONFIRMED":
            return
        if field is None:
            self._project_fields.add(
                ProjectField(
                    id=uuid4(),
                    project_id=project_id,
                    field_code=field_code,
                    value_json=candidate.value_json,
                    confidence=candidate.confidence,
                    review_status="PENDING",
                    primary_evidence_id=evidence_ids[0],
                    reviewed_at=None,
                    reviewed_by=None,
                    review_note=None,
                    extraction_source="llm",
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        if field.review_status != "PENDING":
            return
        field.field_code = field_code
        field.value_json = candidate.value_json
        field.confidence = candidate.confidence
        field.primary_evidence_id = evidence_ids[0]
        field.extraction_source = "llm"
        field.updated_at = now

    @staticmethod
    def _normalize_conditions(conditions: dict | None) -> dict | None:
        """Normalize model conditions to the supported non-empty DSL shape."""
        if not isinstance(conditions, dict) or "all" not in conditions:
            return None
        raw_conditions = conditions["all"]
        # Some providers wrap their list as {"item": [...]}; accepting this
        # known shape prevents a valid structured response from being dropped.
        if isinstance(raw_conditions, dict):
            raw_conditions = raw_conditions.get("item")
        if not isinstance(raw_conditions, list):
            return None
        normalized = [
            {
                "dimension": item["dimension"],
                "operator": item["operator"],
                "value": item["value"],
            }
            for item in raw_conditions
            if isinstance(item, dict)
            and all(key in item and item[key] not in (None, "") for key in (
                "dimension", "operator", "value"
            ))
        ]
        return {"all": normalized} if normalized else None

    def _upsert(
        self,
        project_id: UUID,
        candidate: RequirementCandidate,
        *,
        order_nos: list[int],
        evidence_ids: list[UUID],
        extraction_source: str = "llm",
    ) -> None:
        """写入/更新 Requirement，用 order_nos 作为 evidence 锚点。"""
        now = datetime.now(UTC)
        requirement = self._requirements.find_pending(
            project_id, candidate.category, candidate.title
        )
        normalized = self._normalize_conditions(candidate.conditions)
        if order_nos:
            if normalized is None:
                normalized = {}
            normalized["evidence_order_nos"] = order_nos

        if requirement is None:
            requirement = Requirement(
                id=uuid4(),
                project_id=project_id,
                category=candidate.category,
                title=candidate.title,
                description=candidate.description,
                conditions=normalized,
                is_mandatory=self._has_hard_requirement_signal(candidate),
                score=candidate.score,
                confidence=candidate.confidence,
                review_status="PENDING",
                extraction_source=extraction_source,
                created_at=now,
                updated_at=now,
            )
            self._requirements.add(requirement)
        else:
            requirement.description = candidate.description
            requirement.conditions = normalized
            requirement.is_mandatory = self._has_hard_requirement_signal(candidate)
            requirement.score = candidate.score
            requirement.confidence = candidate.confidence
            requirement.extraction_source = extraction_source
            requirement.updated_at = now

        existing_evidence = set(self._requirements.list_evidence_ids(requirement.id))
        for evidence_id in dict.fromkeys(evidence_ids):
            if evidence_id not in existing_evidence:
                self._requirements.add_evidence(
                    RequirementEvidence(
                        requirement_id=requirement.id,
                        evidence_id=evidence_id,
                        relation="EXTRACTED_FROM",
                        created_at=now,
                    )
                )
        if requirement.primary_evidence_id is None and evidence_ids:
            requirement.primary_evidence_id = evidence_ids[0]

    @staticmethod
    def _has_hard_requirement_signal(candidate: RequirementCandidate) -> bool:
        """Do not turn every qualification heading into a mandatory requirement."""
        if not candidate.is_mandatory:
            return False
        source = f"{candidate.title}\n{candidate.description or ''}"
        return bool(_HARD_REQUIREMENT_SIGNAL.search(source))

    @staticmethod
    def _normalized_requirement_key(candidate: RequirementCandidate) -> str:
        value = re.sub(r"[\s\W_]+", "", candidate.title, flags=re.UNICODE).lower()
        value = re.sub(r"^(投标人|供应商|申请人|联合体|我方|要求|条件|关于)+", "", value)
        # Models often vary only the duty verb ("提供" vs "提交") while
        # describing the same evidence-backed obligation.  Remove this small,
        # controlled vocabulary for deduplication, but keep substantive terms
        # such as "有效" and dates so distinct conditions stay separate.
        return re.sub(r"必须|应当|须|应|需|提供|提交|出具|附", "", value)

    def _prioritize_review_queue(self, project_id: UUID) -> None:
        """Keep human attention on the small set of consequential uncertain facts."""
        # The application session intentionally has ``autoflush=False``.  Requirement
        # candidates persisted in the current LLM pass must therefore be flushed before
        # querying the queue; otherwise they escape this pass and remain PENDING beside
        # the bounded high-priority queue.
        self._session.flush()
        now = datetime.now(UTC)
        candidates = [
            requirement for requirement in self._requirements.list_for_project(project_id)
            if requirement.review_status in {"PENDING", "DEFERRED"}
        ]
        evidence_by_requirement = self._requirements.list_evidence_ids_for_requirements(
            [requirement.id for requirement in candidates]
        )
        reviewable: list[Requirement] = []
        for requirement in candidates:
            # Backfill the corrected rule for Requirements extracted before
            # the clause-level mandatory-signal refinement was deployed.
            if requirement.is_mandatory and not _HARD_REQUIREMENT_SIGNAL.search(
                f"{requirement.title}\n{requirement.description or ''}"
            ):
                requirement.is_mandatory = False
            has_evidence = bool(evidence_by_requirement.get(requirement.id))
            is_safe_project_fact = (
                requirement.category == "PROJECT"
                and not requirement.is_mandatory
                and (requirement.confidence or Decimal("0")) >= self._AUTO_CONFIRM_CONFIDENCE
                and has_evidence
                and any(
                    term in requirement.title
                    for term in ("项目编号", "招标范围", "工期", "交货", "开标", "截止")
                )
            )
            if is_safe_project_fact:
                requirement.review_status = "CONFIRMED"
                requirement.review_note = "系统自动确认：高置信度、可溯源的项目事实"
                requirement.reviewed_at = now
                requirement.updated_at = now
                continue
            reviewable.append(requirement)

        def priority(requirement: Requirement) -> tuple[Decimal, str]:
            category_weight = {
                "QUALIFICATION": 40,
                "SCORING": 40,
                "BUSINESS": 20,
            }.get(requirement.category, 0)
            score = Decimal("100") if requirement.is_mandatory else Decimal("0")
            score += Decimal(category_weight)
            score += (requirement.confidence or Decimal("0")) * Decimal("20")
            if evidence_by_requirement.get(requirement.id):
                score += Decimal("10")
            return (-score, requirement.title)

        for index, requirement in enumerate(sorted(reviewable, key=priority)):
            if index < self._MAX_ACTIVE_REVIEW_ITEMS:
                requirement.review_status = "PENDING"
                requirement.review_note = "高优先级复核：强制性、类别、置信度和证据完整性综合排序"
            else:
                requirement.review_status = "DEFERRED"
                requirement.review_note = "延后复核：有效候选，待高优先级队列处理完成后进入"
            requirement.updated_at = now

    def _persist_legacy_tag_fallback(
        self, project_id: UUID, document_version_id: UUID
    ) -> int:
        """Turn validated legacy extraction tags into reviewable Requirements.

        The old bid pipeline already extracts these tags with source node IDs.
        This deterministic fallback is intentionally only used when structured
        Requirement extraction returned no candidates; it keeps the new facts
        evidence-backed and requires the normal human confirmation step.
        """
        rows = self._session.execute(
            text(
                """
                select d.tag_code, d.tag_name, t.tag_value, t.tag_value_json,
                       t.source_text, t.source_node_id, t.confidence
                from app.bid_document_tag t
                join app.bid_tag_dict d on d.tag_id = t.tag_id
                where t.version_id = :version_id
                  and (t.tag_value is not null or t.tag_value_json is not null)
                order by d.tag_code
                """
            ),
            {"version_id": str(document_version_id)},
        ).mappings()
        evidence_by_node = {
            row["document_node_id"]: row["id"]
            for row in self._session.execute(
                text(
                    """
                    select id, document_node_id::text as document_node_id
                    from app.evidences
                    where document_version_id = :version_id and document_node_id is not null
                    """
                ),
                {"version_id": str(document_version_id)},
            ).mappings()
        }
        now = datetime.now(UTC)
        persisted = 0
        for row in rows:
            evidence_id = evidence_by_node.get(row["source_node_id"])
            if evidence_id is None:
                continue
            code = row["tag_code"]
            category = (
                "QUALIFICATION"
                if code.startswith("QUAL_") or code.startswith("REJECT_")
                else "SCORING"
                if "SCORE" in code or code.startswith("EVAL_")
                else "PROJECT"
                if code.startswith("PROJECT_")
                else "BUSINESS"
            )
            title = f"{row['tag_name']}（{code}）"
            requirement = self._requirements.find_pending(project_id, category, title)
            if requirement is None:
                value = row["tag_value"] or json.dumps(row["tag_value_json"], ensure_ascii=False)
                requirement = Requirement(
                    id=uuid4(),
                    project_id=project_id,
                    category=category,
                    title=title,
                description=row["source_text"] or value or "",
                conditions={"source": "legacy_tag", "tag_code": code, "value": value},
                is_mandatory=bool(
                    _HARD_REQUIREMENT_SIGNAL.search(row["source_text"] or "")
                ),
                    score=None,
                    confidence=Decimal(str(row["confidence"] or 0)),
                    review_status="PENDING",
                    primary_evidence_id=evidence_id,
                    reviewed_at=None,
                    reviewed_by=None,
                    review_note=None,
                    extraction_source="rule",
                    created_at=now,
                    updated_at=now,
                )
                self._requirements.add(requirement)
                persisted += 1
            if evidence_id not in set(self._requirements.list_evidence_ids(requirement.id)):
                self._requirements.add_evidence(
                    RequirementEvidence(
                        requirement_id=requirement.id,
                        evidence_id=evidence_id,
                        relation="EXTRACTED_FROM",
                        created_at=now,
                    )
                )
        logger.info("[Requirement] legacy-tag fallback persisted %d candidates", persisted)
        return persisted

    # ==================================================================
    # 模块路由 + LLM 并发抽取
    # ==================================================================

    def _extract_batched_by_module(
        self, task_id: UUID | None, nodes: list[dict]
    ) -> tuple[RequirementExtractionResult, AiRun]:
        """主流入口：节点先路由到各专业模块，每个模块创建独立 AiRun 并发抽取。"""
        payload = _canonical_ai_input(nodes)
        run = AiRun(
            id=uuid4(),
            task_id=task_id,
            scene="requirement_extraction",
            model_id=LLM_MODEL_ID,
            input_hash=hashlib.sha256(payload.encode()).hexdigest(),
            status="RUNNING",
            created_at=datetime.now(UTC),
        )
        self._session.add(run)
        self._session.commit()

        routed: list[RoutedNode] = self._module_router.route(nodes)
        logger.info(f"[ModuleRouter] 路由结果: {self._module_router.route_stats(routed)}")

        module_groups: dict[str, list[RoutedNode]] = defaultdict(list)
        for r in routed:
            module_groups[r.module_id].append(r)

        confirmed_fields = self._get_confirmed_fields()
        module_results = self._run_modules_concurrently(task_id, module_groups)
        all_fields, all_requirements = self._merge_module_results(module_results, confirmed_fields)

        final_result = self._filter_result(
            RequirementExtractionResult(
                project_fields=all_fields[: self._MAX_PROJECT_FIELDS],
                requirements=all_requirements[: self._MAX_REQUIREMENTS],
            ),
            allowed_order_nos={int(node["order_no"]) for node in nodes if node.get("order_no")},
        )
        run.status = "SUCCEEDED"
        run.output_hash = hashlib.sha256(final_result.model_dump_json().encode()).hexdigest()
        run.completed_at = datetime.now(UTC)
        self._session.commit()
        logger.info(
            f"[_extract_batched_by_module] 完成: fields={len(all_fields)} "
            f"reqs={len(all_requirements)} module_runs={len(module_results)}"
        )
        return final_result, run

    def _get_confirmed_fields(self) -> set[str]:
        """获取规则引擎已 CONFIRMED 的字段集合。"""
        if self._rule_extractor is None:
            return set()
        return set(self._rule_extractor.get_confirmed_fields())

    def _get_module_name(self, module_id: str) -> str:
        """安全获取模块名称，取不到时回退为 module_id。"""
        get_module = getattr(self._module_router, "_get_module", lambda _: None)
        module = get_module(module_id)
        return getattr(module, "name", module_id) if module else module_id

    def _run_modules_concurrently(
        self,
        task_id: UUID | None,
        module_groups: dict[str, list[RoutedNode]],
    ) -> list[tuple[RequirementExtractionResult, AiRun | None, str]]:
        """并发运行所有模块抽取。"""
        module_tasks = [
            (mid, self._get_module_name(mid), mrouted)
            for mid, mrouted in module_groups.items()
        ]
        logger.info(
            f"[_run_modules_concurrently] "
            f"{sum(len(g) for g in module_groups.values())} 节点，"
            f"{len(module_tasks)} 模块，并发 {self._MAX_CONCURRENT_MODULES}"
        )

        results: list[tuple[RequirementExtractionResult, AiRun | None, str]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._MAX_CONCURRENT_MODULES
        ) as executor:
            futures = {
                executor.submit(self._run_module, task_id, mid, mname, mrouted): (mid, mname)
                for mid, mname, mrouted in module_tasks
            }
            for future in concurrent.futures.as_completed(futures):
                mid, _mname = futures[future]
                try:
                    results.append(future.result())
                except Exception:
                    logger.exception(f"模块 {mid} 执行异常，跳过")

        # 持久化所有 module_runs
        module_runs = [r[1] for r in results if r[1] is not None]
        if module_runs:
            self._session.add_all(module_runs)
            self._session.flush()
        return results

    def _run_module(
        self,
        task_id: UUID | None,
        module_id: str,
        module_name: str,
        module_routed: list[RoutedNode],
    ) -> tuple[RequirementExtractionResult, AiRun | None, str]:
        """为单个模块创建 AiRun 并执行抽取。"""
        module_nodes = [
            {
                "order_no": r.node.get("order_no") or 0,
                "content": r.node.get("content"),
                "page_number": r.node.get("page_number"),
            }
            for r in module_routed
        ]
        if not module_nodes:
            return RequirementExtractionResult(project_fields=[], requirements=[]), None, module_id

        module_payload = _canonical_ai_input(module_nodes)
        module_run = AiRun(
            id=uuid4(),
            task_id=task_id,
            scene=f"requirement_extraction.module.{module_id}",
            model_id=LLM_MODEL_ID,
            input_hash=hashlib.sha256(module_payload.encode()).hexdigest(),
            status="RUNNING",
            created_at=datetime.now(UTC),
        )
        started = perf_counter()
        try:
            result = self._extract_by_module(module_routed, module_id, module_name)
            module_run.status = "SUCCEEDED"
            module_run.latency_ms = int((perf_counter() - started) * 1000)
            module_run.completed_at = datetime.now(UTC)
            return result, module_run, module_id
        except Exception as exc:
            module_run.status = "FAILED"
            module_run.error_code = type(exc).__name__
            module_run.latency_ms = int((perf_counter() - started) * 1000)
            module_run.completed_at = datetime.now(UTC)
            logger.warning(f"[Module {module_id}] 抽取失败: {exc}")
            return (
                RequirementExtractionResult(project_fields=[], requirements=[]),
                module_run,
                module_id,
            )

    @staticmethod
    def _merge_module_results(
        module_results: list[tuple[RequirementExtractionResult, AiRun | None, str]],
        confirmed_fields: set[str],
    ) -> tuple[list[ProjectFieldCandidate], list[RequirementCandidate]]:
        """合并所有模块结果，按 field_code / title 去重，跳过规则已 CONFIRMED 的字段。"""
        all_fields: list[ProjectFieldCandidate] = []
        all_requirements: list[RequirementCandidate] = []
        seen_field_codes: set[str] = set()
        seen_requirement_titles: set[str] = set()

        for result, _run, _module_id in module_results:
            for field in result.project_fields:
                if field.field_code in confirmed_fields:
                    continue
                if field.field_code not in seen_field_codes:
                    seen_field_codes.add(field.field_code)
                    all_fields.append(field)
            for req in result.requirements:
                if req.title not in seen_requirement_titles:
                    seen_requirement_titles.add(req.title)
                    all_requirements.append(req)
        return all_fields, all_requirements

    def _extract_by_module(
        self,
        routed_nodes: list[RoutedNode],
        module_id: str,
        module_name: str,
    ) -> RequirementExtractionResult:
        """对单个模块的节点列表进行抽取（模块内批量重试 + 逐节点降级）。"""
        if not routed_nodes:
            return RequirementExtractionResult(project_fields=[], requirements=[])

        nodes = [
            {
                "order_no": r.node.get("order_no") or 0,
                "page_number": r.node.get("page_number"),
                "labels": r.node.get("labels", []),
                "content": r.node.get("content"),
            }
            for r in routed_nodes
        ]
        field_codes = self._module_router.get_module_target_fields(module_id)
        logger.info(f"[Module {module_id}] 抽取 {len(nodes)} 节点，字段: {field_codes}")
        started = perf_counter()

        # 同一业务模块继续按小批发送。模型看到的是同类、已标注的候选，
        # 不是数十页原文；这也显著降低 JSON 截断和证据乱引用概率。
        partial_results: list[RequirementExtractionResult] = []
        for start in range(0, len(nodes), self._MAX_LLM_CANDIDATES_PER_MODULE):
            partial = self._extract_module_batch(
                nodes[start : start + self._MAX_LLM_CANDIDATES_PER_MODULE],
                module_id,
                field_codes,
            )
            if partial is not None:
                partial_results.append(partial)
        if partial_results:
            all_fields = [field for partial in partial_results for field in partial.project_fields]
            all_requirements = [
                requirement for partial in partial_results for requirement in partial.requirements
            ]
            result = RequirementExtractionResult(
                project_fields=all_fields[: self._MAX_PROJECT_FIELDS],
                requirements=all_requirements[: self._MAX_REQUIREMENTS],
            )
        else:
            result = None

        # 只有极小批才逐节点重试；大批逐节点会重新把几十个条款塞回 LLM，
        # 既慢又不稳定。大批失败由带 Evidence 的规则候选承接。
        if result is None or (not result.requirements and not result.project_fields):
            if len(nodes) <= 3:
                result = self._extract_module_node_by_node(nodes, module_id, field_codes)
            else:
                logger.warning(
                    "[Module %s] 小批无有效结果，跳过逐节点 LLM 重试并使用规则回退",
                    module_id,
                )
                result = RequirementExtractionResult(project_fields=[], requirements=[])

        if result is None:
            return RequirementExtractionResult(project_fields=[], requirements=[])

        elapsed = perf_counter() - started
        logger.info(
            f"[Module {module_id}] 抽取完成 耗时 {elapsed:.1f}s "
            f"fields={len(result.project_fields)} reqs={len(result.requirements)}"
        )
        return self._filter_result(
            result,
            allowed_order_nos={int(node["order_no"]) for node in nodes if node.get("order_no")},
        )

    def _extract_module_batch(
        self,
        nodes: list[dict],
        module_id: str,
        field_codes: list[str],
    ) -> RequirementExtractionResult | None:
        """模块内批量抽取：最多重试 _RETRY_ATTEMPTS 次，最后一次用 strict 模式兜底。"""
        last_result: RequirementExtractionResult | None = None
        last_attempt = self._RETRY_ATTEMPTS - 1

        for attempt in range(self._RETRY_ATTEMPTS):
            strict = attempt == last_attempt
            try:
                result = (
                    self._llm.extract_requirements_for_fields(
                        nodes, field_codes, strict=strict
                    )
                    if field_codes
                    else self._llm.extract_requirements(nodes, strict=strict)
                )
                if result is not None and (result.requirements or result.project_fields):
                    return result
                last_result = result
                if attempt < last_attempt:
                    logger.warning(f"[Module {module_id}] 第{attempt + 1}次返回空结果，重试...")
            except ValidationError as exc:
                logger.warning(
                    f"[Module {module_id}] 第{attempt + 1}次验证异常: {exc.errors()[:1]}"
                )
            except Exception as exc:
                logger.warning(f"[Module {module_id}] 第{attempt + 1}次异常: {exc}")
        return last_result

    def _extract_module_node_by_node(
        self,
        nodes: list[dict],
        module_id: str,
        field_codes: list[str],
    ) -> RequirementExtractionResult | None:
        """逐节点抽取降级：单节点独立调用，按 title 去重。"""
        logger.warning(
            f"[Module {module_id}] 批量失败({len(nodes)}节点)，降级逐节点抽取"
        )
        all_reqs: list[RequirementCandidate] = []
        all_fields: list[ProjectFieldCandidate] = []
        seen_titles: set[str] = set()

        for node in nodes:
            try:
                node_result = (
                    self._llm.extract_requirements_for_fields([node], field_codes)
                    if field_codes
                    else self._llm.extract_requirements([node])
                )
            except Exception:
                continue
            if not node_result:
                continue
            for req in node_result.requirements or []:
                if req.title not in seen_titles:
                    seen_titles.add(req.title)
                    all_reqs.append(req)
            all_fields.extend(node_result.project_fields or [])

        logger.warning(
            f"[Module {module_id}] 逐节点降级完成: "
            f"fields={len(all_fields)} reqs={len(all_reqs)}"
        )
        return RequirementExtractionResult(project_fields=all_fields, requirements=all_reqs)

    def _filter_result(
        self,
        result: RequirementExtractionResult,
        *,
        allowed_order_nos: set[int],
    ) -> RequirementExtractionResult:
        """Reject unsupported LLM output and merge duplicate, evidence-linked requirements."""
        valid_fields: list[ProjectFieldCandidate] = []
        seen_fields: set[str] = set()
        for field in result.project_fields:
            evidence_order_nos = self._resolve_order_nos(
                field.evidence_order_nos, allowed_order_nos
            )
            if not evidence_order_nos or field.field_code in seen_fields:
                continue
            seen_fields.add(field.field_code)
            field.evidence_order_nos = evidence_order_nos
            valid_fields.append(field)

        seen_titles: dict[tuple[str, str], RequirementCandidate] = {}
        valid_reqs: list[RequirementCandidate] = []
        for r in result.requirements:
            evidence_order_nos = self._resolve_order_nos(
                r.evidence_order_nos, allowed_order_nos
            )
            if not evidence_order_nos:
                continue
            r.evidence_order_nos = evidence_order_nos
            category = _normalize_category(r.category)
            r.category = category
            key = (category, self._normalized_requirement_key(r))
            existing = seen_titles.get(key)
            if existing is not None:
                existing.evidence_order_nos = list(dict.fromkeys(
                    (existing.evidence_order_nos or []) + (r.evidence_order_nos or [])
                ))
                if (r.confidence or Decimal("0")) > (existing.confidence or Decimal("0")):
                    existing.description = r.description
                    existing.confidence = r.confidence
                    existing.conditions = r.conditions
                existing.is_mandatory = existing.is_mandatory or r.is_mandatory
                continue
            seen_titles[key] = r
            valid_reqs.append(r)

        dropped_fields = len(result.project_fields) - len(valid_fields)
        dropped_reqs = len(result.requirements) - len(valid_reqs)
        if dropped_fields or dropped_reqs:
            logger.warning(f"过滤候选: fields={dropped_fields}, reqs={dropped_reqs}")
        return RequirementExtractionResult(project_fields=valid_fields, requirements=valid_reqs)

    # ==================================================================
    # 任务状态
    # ==================================================================

    def _fail(self, task_id: UUID, version_id: UUID, code: str, message: str) -> None:
        self._session.rollback()
        self._tasks.fail_extraction(task_id, version_id, code, message)

    def _retry_or_fail(self, task_id: UUID, version_id: UUID, code: str, message: str) -> None:
        self._session.rollback()
        if self._tasks.retry_extraction_or_fail(task_id, version_id, code, message):
            raise RetryableDocumentTaskError(code, message)

    # ==================================================================
    # 低置信度处理
    # ==================================================================

    def _apply_low_confidence_penalty(self, project_id: UUID) -> None:
        """对低置信度的 PENDING 字段做降权处理，便于后续人工复核优先。"""
        low_fields = (
            self._session.query(ProjectField)
            .filter(
                ProjectField.project_id == project_id,
                ProjectField.review_status == "PENDING",
                ProjectField.extraction_source == "llm",
                ProjectField.confidence < self._CONFIDENCE_THRESHOLD,
            )
            .all()
        )
        if not low_fields:
            logger.info("[Requirement] 无低置信度字段，跳过")
            return

        low_codes = [f.field_code for f in low_fields]
        logger.warning(
            f"[Requirement] 检测到 {len(low_fields)} 个低置信度字段: {low_codes}，降权处理"
        )
        for field in low_fields:
            field.confidence = field.confidence * self._LOW_CONFIDENCE_PENALTY
            field.review_note = (field.review_note or "") + "[低置信度降权]"
        self._session.commit()
        logger.info("[Requirement] 低置信度字段降权完成")
