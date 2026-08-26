import logging
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import DocumentNode
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.knowledge_repository import KnowledgeRepository
from app.integrations.task_publisher import TaskPublisher
from app.services.document_text_quality import (
    GARBLED_RATIO_THRESHOLD,
    assess_text_quality,
    garbled_character_count,
    indexability_gate,
)
from app.services.keyword_scoring_service import KeywordScoringService
from app.services.task_service import RetryableDocumentTaskError, TaskService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 文本规范化模式
# ---------------------------------------------------------------------------
_PAGE_ARTIFACT = re.compile(
    r"^(?:第\s*\d{1,4}\s*页(?:\s*/\s*共?\s*\d{1,4}\s*页)?|"
    r"page\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4})$",
    re.IGNORECASE,
)
_SPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


# ---------------------------------------------------------------------------
# 招标要求关键词
# ---------------------------------------------------------------------------
# section_path 与 content 共用的基础关键词
_TENDER_BASE_KEYWORDS: tuple[str, ...] = (
    "投标人资格",
    "投标保证金",
    "履约保证金",
    "资格审查",
    "评标办法",
    "评审办法",
    "评审标准",
    "实质性要求",
    "分包",
    "联合体",
    "投标文件",
    "投标有效期",
    "投标报价",
    "合同条款",
    "技术标准",
    "技术要求",
    "规格",
    "交货",
    "验收",
    "付款",
    "结算",
    "违约责任",
    "质量保修",
    "保修",
    "验收标准",
)
# 仅 content 匹配的扩展模式（条款编号格式）
_TENDER_CONTENT_EXTRA_PATTERNS: tuple[str, ...] = (
    r"^第[一二三四五六七八九十百千\d]+条",
    r"^\d+\.\d+[.\d]*[^.\s篇章节]",
)

_TENDER_REQUIREMENT_SECTION_RE = re.compile("|".join(_TENDER_BASE_KEYWORDS), re.IGNORECASE)
_TENDER_REQUIREMENT_CONTENT_RE = re.compile(
    "|".join(_TENDER_BASE_KEYWORDS + _TENDER_CONTENT_EXTRA_PATTERNS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 负面模式：即便命中关键词也排除
# ---------------------------------------------------------------------------
_TENDER_REQUIREMENT_NEGATIVE_PATTERNS: tuple[str, ...] = (
    r"^目?录$",
    r"^第[一二三四五六七八九十百千\d]+[章节条篇]$",
    r"^附录[一二三四五六七八九十\d]*$",
    r"^附件[一二三四五六七八九十\d]*$",
    r"^表格\d+$",
    r"^图\d+$",
    r"^注[：:]\s*\S",  # "注：xxx" 注释行
    # 绿化施工技术规范（合同附件中的施工质量标准，不是投标要求）
    r"覆盖率\s*\d+%",
    r"成活率\s*\d+%",
    r"病虫害",
    r"园林植物|绿化施工|园林绿化",
    r"株行距|乔木|灌木|地被植物",
    r"签证.*?监理|隐蔽工程|施工组织",
)
_TENDER_REQUIREMENT_NEGATIVE_RE = re.compile(
    "|".join(_TENDER_REQUIREMENT_NEGATIVE_PATTERNS), re.IGNORECASE
)


# ---------------------------------------------------------------------------
# 义务性表述：只有包含这些词的段落才是真正的投标要求
# ---------------------------------------------------------------------------
_TENDER_OBLIGATION_RE = re.compile(
    "|".join(("应当", "必须", "不得", "不准", "严禁", "需要", "要求", "须"))
)


# ---------------------------------------------------------------------------
# 程序性/流程性内容：操作步骤，非投标人资格要求
# 注意：投标截止/递交投标 过于宽泛（保证金截止日也是实质性要求），不纳入
# ---------------------------------------------------------------------------
_TENDER_PROCEDURAL_RE = re.compile(
    "|".join(
        (
            "开标",
            "评标委员会",
            "澄清",
            "修改招标文件",
            "踏勘",
            "预备会",
            "网上开标",
            "远程解密",
            "异议",
            "投诉",
            "质疑",
            "投标文件格式",
        )
    )
)


# 数字列表项/技术规格排除：短内容且以括号编号或数字编号开头（如"(1)xxx" "1.2.3xxx"）
_TENDER_SPEC_ITEM_RE = re.compile(
    r"^\s*[\(（][\d一二三四五六七八九十]+[\)）]\s*\S|^\s*\d+\.\d+[.\d]*[^\s]"
)


# ---------------------------------------------------------------------------
# 合同类章节关键词：其下正文天然是实质性要求（付款/履约/违约/结算等）
# 注意：仅用于合同章节 bypass（义务词可缺），不代表这些章节都是合同
# ---------------------------------------------------------------------------
_CONTRACT_SECTION_RE = re.compile(
    "|".join(
        (
            "合同条款",
            "合同条件",
            "付款",
            "支付",
            "结算",
            "履约",
            "违约",
            "质量保修",
            "质保",
            "保修",
            "竣工",
            "验收标准",
            "移交",
        )
    ),
    re.IGNORECASE,
)


# section_path 黑名单：这些章节的内容即使含义务词也是合同执行期规范，不算投标要求
_TENDER_REQUIREMENT_SECTION_BLACKLIST: tuple[str, ...] = (
    # 工程量清单/计价规范（合同执行期填表规则）
    "投标报价说明",
    "工程量清单",
    "计价规范",
    # 施工技术规范（合同附件的质量标准）
    "工程技术规范",
    "施工技术规范",
    "技术规范",
    # 廉政建设（合同甲乙方行为规范）
    "廉政协议",
    # 乙方职责/承包人义务（合同执行期约束）
    "乙方职责",
    "承包人义务",
    "承包人职责",
    # 监理规则（合同执行期程序）
    "监理规则",
    "监理制度",
    # 绿化施工规范（技术规格）
    "园林植物",
    "绿化施工",
    "地被植物",
    "乔木灌木",
    "株行距",
)
_TENDER_REQUIREMENT_SECTION_BLACKLIST_RE = re.compile(
    "|".join(_TENDER_REQUIREMENT_SECTION_BLACKLIST), re.IGNORECASE
)


# ---------------------------------------------------------------------------
# 清洗阈值常量
# ---------------------------------------------------------------------------
_DUPLICATE_SHORT_MAX_LEN = 120  # 进入"短重复"统计的最大长度
_MEANINGFUL_MIN_LEN = 8  # 有效字符最小数（小于即判 TOO_SHORT）
_GARBLED_RATIO_THRESHOLD = GARBLED_RATIO_THRESHOLD  # 解析文本乱码占比阈值
_PARAGRAPH_MIN_LEN = 30  # PARAGRAPH 候选最小长度
_PARAGRAPH_CONTRACT_MIN_LEN = 50  # 合同章节 bypass 时要求的最小长度
_SHORT_CONTENT_MAX_LEN = 40  # 列表项判定阈值
_SECTION_PATH_SLICE = 160  # section_path 截断长度
_SECTION_CANDIDATE_LIMIT = 20  # 每个 section 保留的候选段落数
_MIN_QUALITY_SCORE = 0.2  # 整体质量分下限

# 乱码检测：允许的标点集合（frozenset 提升查询性能）
_GARBLED_ALLOWED_PUNCTUATION = frozenset("，。；：、（）()【】[]《》〈〉''—-+/%.:;,_")


class CleaningQualityRejected(ValueError):
    """清洗后无可信文本可用。"""


@dataclass(frozen=True, slots=True)
class CleaningOutcome:
    summary: dict[str, object]
    indexable_nodes: list[DocumentNode]


class DocumentCleaningService:
    """保留 MinerU 输出，同时产出经质量门控的可搜索文本。"""

    # 公开阈值常量，便于单元测试或外部观察
    SECTION_CANDIDATE_LIMIT = _SECTION_CANDIDATE_LIMIT
    MIN_QUALITY_SCORE = _MIN_QUALITY_SCORE

    def __init__(self, session: Session, task_publisher: TaskPublisher | None = None) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._knowledge = KnowledgeRepository(session)
        self._tasks = TaskService(session)
        self._publisher = task_publisher

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def process(self, task_id: UUID, document_version_id: UUID) -> None:
        task = self._tasks.start_clean(task_id, document_version_id)
        if task is None:
            return
        try:
            summary = self.do_clean(document_version_id)
            version = self._documents.get_version(document_version_id)
            document = (
                None if version is None else self._documents.get_document(version.document_id)
            )
            requires_index = document is not None and document.document_type in {
                "TENDER",
                "ENTERPRISE",
                "LEGAL",
                "CASE",
            }
            self._tasks.complete_clean(
                task.id,
                document_version_id,
                summary,
                requires_followup_tasks=requires_index,
            )
            # 后续任务由 DocumentPipelineOrchestrator 统一管理，无需在此发布
        except CleaningQualityRejected:
            self._fail(
                task.id,
                document_version_id,
                "CLEANING_QUALITY_REJECTED",
                "清洗后没有足够的有效文本。",
            )
        except Exception:
            self._retry_or_fail(
                task.id,
                document_version_id,
                "CLEANING_FAILED",
                "文档清洗失败。",
            )

    def do_clean(self, document_version_id: UUID) -> dict[str, object]:
        """核心清洗逻辑，不含 TaskService 管理。返回清洗 summary。"""
        version = self._documents.get_version(document_version_id)
        document = None if version is None else self._documents.get_document(version.document_id)
        if version is None or document is None:
            raise ValueError("document version is not available")
        nodes = self._documents.list_nodes(document_version_id, 0, 1_000_000)
        outcome = self._clean(nodes)
        if document.document_type in {"LEGAL", "CASE"}:
            knowledge_version = self._knowledge.get_knowledge_version_by_source_document_version(
                document_version_id
            )
            if knowledge_version is None:
                raise ValueError("knowledge document has no linked knowledge version")
            knowledge_version.content = "\n\n".join(
                node.cleaned_content or "" for node in outcome.indexable_nodes
            ).strip()
            if not knowledge_version.content:
                raise CleaningQualityRejected("knowledge source has no usable text")
        self._session.commit()
        return outcome.summary

    # ------------------------------------------------------------------
    # 清洗主流程
    # ------------------------------------------------------------------
    def _clean(self, nodes: list[DocumentNode]) -> CleaningOutcome:
        if not nodes:
            raise CleaningQualityRejected("no parsed nodes")

        prepared = [(node, self._normalize(node.content)) for node in nodes]
        # 提前算一遍 duplicate_key，避免重复正则开销
        duplicate_keys = [self._duplicate_key(content) for _, content in prepared]
        short_counts = Counter(
            key for key in duplicate_keys if 0 < len(key) <= _DUPLICATE_SHORT_MAX_LEN
        )
        seen_duplicates: set[str] = set()

        raw_characters = 0
        cleaned_characters = 0
        garbled_characters = 0
        page_artifacts = 0
        duplicate_nodes = 0
        too_short_nodes = 0
        garbled_nodes = 0
        indexable_nodes: list[DocumentNode] = []

        for (node, cleaned), duplicate_key in zip(prepared, duplicate_keys, strict=True):
            visible = [c for c in cleaned if not c.isspace()]
            raw_node_characters = sum(1 for c in node.content if not c.isspace())
            raw_characters += raw_node_characters
            cleaned_characters += len(visible)

            garbled = self._garbled_character_count(visible)
            garbled_characters += garbled

            indexable, flags = self._classify_node(
                cleaned,
                visible,
                duplicate_key,
                short_counts,
                seen_duplicates,
            )
            if "PAGE_ARTIFACT" in flags:
                page_artifacts += 1
            if "DUPLICATE_FRAGMENT" in flags:
                duplicate_nodes += 1
            if "TOO_SHORT" in flags:
                too_short_nodes += 1
            if "GARBLED_TEXT" in flags:
                garbled_nodes += 1

            node.cleaned_content = cleaned or None
            # 招标要求预筛选标记（初筛，后续 section 限流可能降级 PARAGRAPH）
            node.tender_req_candidate = indexable and self._is_tender_requirement_candidate(
                cleaned, node.section_path or "", node.node_type
            )
            node.cleaning_metadata = {
                **(node.cleaning_metadata or {}),  # 保留已有字段
                "indexable": indexable,
                "flags": flags,
                "raw_characters": raw_node_characters,
                "cleaned_characters": len(visible),
                "garbled_ratio": round(garbled / len(visible), 4) if visible else 1.0,
            }
            if indexable:
                indexable_nodes.append(node)

        # ---- 关键词得分计算（优先级排序用）----
        KeywordScoringService().score_nodes(indexable_nodes)

        # ---- section 级候选上限：每个 section 最多保留 N 个候选段落 ----
        promoted, section_count = self._apply_section_candidate_limit(indexable_nodes)

        tender_candidates_count = sum(1 for n in indexable_nodes if n.tender_req_candidate)
        logger.info(
            "[Clean] section candidates: sections=%d promoted=%d limit=%d final_candidates=%d",
            section_count,
            promoted,
            _SECTION_CANDIDATE_LIMIT,
            tender_candidates_count,
        )
        logger.info(
            "[Clean] indexable=%d raw_chars=%d cleaned_chars=%d garbled_ratio=%.4f",
            len(indexable_nodes),
            raw_characters,
            cleaned_characters,
            round(garbled_characters / max(cleaned_characters, 1), 4),
        )

        quality_score = self._quality_score(raw_characters, garbled_characters, indexable_nodes)
        summary: dict[str, object] = {
            "raw_nodes": len(nodes),
            "indexable_nodes": len(indexable_nodes),
            "tender_candidates": tender_candidates_count,
            "raw_characters": raw_characters,
            "cleaned_characters": cleaned_characters,
            "page_artifacts": page_artifacts,
            "duplicate_nodes": duplicate_nodes,
            "too_short_nodes": too_short_nodes,
            "garbled_nodes": garbled_nodes,
            "garbled_ratio": round(garbled_characters / max(cleaned_characters, 1), 4),
            "quality_score": quality_score,
        }
        if not indexable_nodes or quality_score < _MIN_QUALITY_SCORE:
            raise CleaningQualityRejected("usable text quality is insufficient")
        return CleaningOutcome(summary=summary, indexable_nodes=indexable_nodes)

    def _classify_node(
        self,
        cleaned: str,
        visible: list[str],
        duplicate_key: str,
        short_counts: Counter,
        seen_duplicates: set[str],
    ) -> tuple[bool, list[str]]:
        """根据清洗后内容判定节点是否可索引，返回 (indexable, flags)。"""
        flags: list[str] = []
        indexable = True

        if not cleaned or _PAGE_ARTIFACT.fullmatch(cleaned):
            flags.append("PAGE_ARTIFACT")
            return False, flags

        # Shared gate for tender and legal knowledge: table-of-contents
        # fragments must never become chunks for RAG, extraction or indexing.
        quality_gate = indexability_gate(cleaned, assess_text_quality(cleaned))
        if quality_gate:
            flags.append(quality_gate)
            return False, flags

        if duplicate_key and short_counts[duplicate_key] >= 2:
            if duplicate_key in seen_duplicates:
                flags.append("DUPLICATE_FRAGMENT")
                return False, flags
            seen_duplicates.add(duplicate_key)

        if self._meaningful_character_count(visible) < _MEANINGFUL_MIN_LEN:
            flags.append("TOO_SHORT")
            indexable = False
        garbled_ratio = self._garbled_character_count(visible) / len(visible) if visible else 0.0
        if visible and garbled_ratio > _GARBLED_RATIO_THRESHOLD:
            flags.append("GARBLED_TEXT")
            indexable = False

        return indexable, flags

    def _apply_section_candidate_limit(
        self, indexable_nodes: list[DocumentNode]
    ) -> tuple[int, int]:
        """对每个 section 的 PARAGRAPH 候选做 top-N 限流。

        Returns:
            (promoted, section_count)
        """
        section_candidates: dict[str, list[DocumentNode]] = defaultdict(list)
        for node in indexable_nodes:
            # 非 PARAGRAPH 节点不做 section 级限制
            if node.node_type != "PARAGRAPH":
                continue
            # 只对初筛通过的节点做 section 级限流，减少 LLM 处理量
            if not node.tender_req_candidate:
                continue
            sec = (node.section_path or "")[:_SECTION_PATH_SLICE]
            section_candidates[sec].append(node)

        # 重置所有 PARAGRAPH 候选标记，再按 section 保留 top-N
        # 优先保留 keyword_score 高的节点（得分相同则按 order_no 倒序）
        for node in indexable_nodes:
            if node.node_type == "PARAGRAPH":
                node.tender_req_candidate = False

        promoted = 0
        for sec_nodes in section_candidates.values():
            for node in sorted(
                sec_nodes,
                key=lambda n: (n.cleaning_metadata.get("keyword_score", 0), n.order_no),
                reverse=True,
            )[:_SECTION_CANDIDATE_LIMIT]:
                node.tender_req_candidate = True
                promoted += 1

        return promoted, len(section_candidates)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize(content: str) -> str:
        normalized = unicodedata.normalize("NFKC", content)
        normalized = "".join(
            c for c in normalized if unicodedata.category(c) != "Cf" or c in {"\n", "\t"}
        )
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        lines = [_SPACE.sub(" ", line).strip() for line in normalized.split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()

    @staticmethod
    def _duplicate_key(content: str) -> str:
        # 去除 # 标题标记后去重，避免 "#标题" 与 "标题" 被当作不同内容
        return re.sub(r"\s+", "", re.sub(r"#+\s*", "", content)).casefold()

    @staticmethod
    def _meaningful_character_count(characters: list[str]) -> int:
        return sum(c.isalnum() or "一" <= c <= "鿿" for c in characters)

    @staticmethod
    def _garbled_character_count(characters: list[str]) -> int:
        # 不能用 ``str.isalnum`` 作为白名单：PDF 乱码后的希腊/西里尔字符
        # 同样会被它判为字母。实际 Worker 使用的清洗节点与这里共用质量判定。
        return garbled_character_count(characters)

    @staticmethod
    def _is_tender_requirement_candidate(content: str, section_path: str, node_type: str) -> bool:
        """判断节点是否可能是招标要求相关内容。

        过滤策略：
        - 噪音节点（目录、页码、注释等）直接排除
        - SECTION 节点靠 section_path bypass
        - PARAGRAPH 节点必须同时满足：义务词 + 关键词 + 最低字数
        """
        stripped = content.strip()
        # 排除目录、注释、附件等噪音节点（content 层面）
        if _TENDER_REQUIREMENT_NEGATIVE_RE.search(stripped):
            return False
        # 排除合同执行期章节（section_path 黑名单，优先于义务词检查）
        # "投标报价说明"、"工程技术规范"等内容虽然含义务词描述，
        # 但属于合同执行期的操作规范，不是投标递交时的要求
        if _TENDER_REQUIREMENT_SECTION_BLACKLIST_RE.search(section_path):
            return False
        # SECTION 节点：section_path 匹配明确关键词则通过（用于定位章节）
        if node_type == "SECTION" and _TENDER_REQUIREMENT_SECTION_RE.search(section_path):
            return True
        # 排除程序性/流程性内容（开标解密流程、澄清步骤等）
        if _TENDER_PROCEDURAL_RE.search(stripped):
            return False
        # 排除技术规格列表项（如"(1)裙板装饰"、"1.2.3扶梯配置要求"）
        # 必须同时满足：短内容(<40) + 以括号/数字编号开头
        if len(content) < _SHORT_CONTENT_MAX_LEN and _TENDER_SPEC_ITEM_RE.match(content):
            return False
        # PARAGRAPH 节点：必须同时满足 关键词匹配 + 义务词 + 最低字数
        # 原来 len >= 50 太宽；现在要求必须有义务词且 >= 30 字
        if len(content) >= _PARAGRAPH_MIN_LEN and _TENDER_REQUIREMENT_CONTENT_RE.search(content):
            if _TENDER_OBLIGATION_RE.search(content):
                return True
            # 合同章节下无义务词也可通过，但必须有更长的实质内容（>=50字）
            # 防止合同 bypass 放过大量描述性正文
            if (
                _CONTRACT_SECTION_RE.search(section_path)
                and len(content) >= _PARAGRAPH_CONTRACT_MIN_LEN
            ):
                return True
        return False

    @staticmethod
    def _quality_score(
        raw_characters: int,
        garbled_characters: int,
        indexable_nodes: list[DocumentNode],
    ) -> float:
        usable_characters = sum(len(node.cleaned_content or "") for node in indexable_nodes)
        usable_ratio = usable_characters / max(raw_characters, 1)
        garbled_ratio = garbled_characters / max(raw_characters, 1)
        return round(max(0.0, min(1.0, usable_ratio * (1 - garbled_ratio))), 4)

    def _fail(self, task_id: UUID, version_id: UUID, code: str, message: str) -> None:
        self._session.rollback()
        self._tasks.fail_clean(task_id, version_id, code, message)

    def _retry_or_fail(self, task_id: UUID, version_id: UUID, code: str, message: str) -> None:
        self._session.rollback()
        if self._tasks.retry_clean_or_fail(task_id, version_id, code, message):
            raise RetryableDocumentTaskError(code, message)
