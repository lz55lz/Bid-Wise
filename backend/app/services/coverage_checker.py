"""CoverageChecker — 反向校验：关键词撒网命中 vs LLM 提取结果

借鉴：tender-review-kit/scripts/check_coverage.py

核心思想：
- 关键词撒网是确定性的（这次和下次一模一样）
- LLM 提取可能漏判或误判
- 反向校验 = "程序兜底 + LLM 判断"两层分离

用法：
    checker = CoverageChecker(session)
    result = checker.check(document_version_id, extracted_requirements)
    if result.uncovered:
        for item in result.uncovered:
            print(f"[{item.severity}] line={item.line} word={item.word}")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


# ============================================================================
# 强判决词：未覆盖时判 high
# ============================================================================

STRONG_KEYWORDS = [
    "否决", "废标", "无效投标", "拒收", "予以否决", "无效",
    "取消资格", "取消中标", "取消投标", "不予受理", "不予接受",
    "不予退还", "不予认可", "不予计分", "不予扣除",
    "视为放弃", "视为撤回", "视为撤销", "视为无效",
    "作为废标", "按无效投标", "按废标",
]

# 投标阶段/评标阶段关键词（bid_phase / evaluation_phase）
# 这些命中如果没被 requirements 覆盖，说明 LLM 漏了
SCOPE_KEYWORDS = [
    # 一级判决词（bid_phase）
    "废标", "无效投标", "否决", "拒收", "不予受理", "不予接受",
    "取消资格", "取消中标", "取消投标", "不予退还保证金",
    "视为放弃", "视为撤回", "视为撤销", "视为无效",
    "作为废标", "按无效投标", "按废标", "实质性偏离",
    # 二级判决词（bid_phase + evaluation_phase）
    "必须", "应当", "不得", "不准", "严禁",
    "不接受", "不允许", "不符合", "不满足",
    # 格式性要求
    "签字", "盖章", "签章", "密封", "正本", "副本",
    "原件", "扫描件", "份数", "装订",
]

# 行号提取正则：只认带"行"字的锚点
# 避免把金额/数量等数字误判成行号
LINE_PATTERNS = [
    re.compile(r"行\s*(\d+)\s*(?:[–\-~至]\s*(\d+))?"),       # 行103 / 行100-105
    re.compile(r"第\s*(\d+)\s*(?:[–\-~至]\s*(\d+))?\s*行"),   # 第103行 / 第100-105行
]


# ============================================================================
# 数据类
# ============================================================================


class Severity(str):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class HitItem:
    """关键词命中项"""
    line: int       # 行号（文档中的位置）
    word: str       # 命中的关键词
    text: str       # 命中行的原文（截断）
    scope: list[str] = field(default_factory=list)  # bid_phase / evaluation_phase
    severity: Severity = Severity.MEDIUM


@dataclass
class UncoveredItem:
    """未覆盖的命中项"""
    line: int
    word: str
    text: str
    severity: Severity
    reason: str = ""


@dataclass
class CoverageResult:
    """反向校验结果"""
    total_hits: int = 0          # 总命中数
    covered: int = 0             # 被 requirements 覆盖的命中数
    uncovered: list[UncoveredItem] = field(default_factory=list)  # 未覆盖的命中
    coverage_ratio: float = 0.0  # 覆盖率 0.0~1.0

    # 统计
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0

    # 告警信息
    warnings: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        """覆盖率 >= 90% 且无 high 级未覆盖 = 健康"""
        return self.coverage_ratio >= 0.9 and self.high_count == 0


# ============================================================================
# CoverageChecker
# ============================================================================


class CoverageChecker:
    """反向校验器

    工作流程：
    1. 用关键词撒网扫描所有候选节点 → hits
    2. 读取 LLM 提取的 requirements，提取每个 requirement 引用的行号
    3. 检查 hits 中的每个命中是否被某个 requirement 覆盖
    4. 未覆盖的命中按 severity 分级
    """

    def __init__(self, session) -> None:
        self._session = session

    def scan_hits(self, nodes: list) -> list[HitItem]:
        """关键词撒网扫描候选节点，返回命中列表

        Args:
            nodes: DocumentNode 列表

        Returns:
            HitItem 列表，按 line 排序
        """
        hits: list[HitItem] = []
        seen: set[tuple[int, str]] = set()

        for node in nodes:
            content = node.cleaned_content or ""
            if not content:
                continue

            for keyword in SCOPE_KEYWORDS:
                # 不区分大小写搜索
                for match in re.finditer(re.escape(keyword), content, re.IGNORECASE):
                    # 估算行号（用 order_no 代替真实行号）
                    line = getattr(node, "order_no", 0) or 0

                    key = (line, keyword)
                    if key in seen:
                        continue
                    seen.add(key)

                    # 提取上下文（命中词周围 50 字）
                    start = max(0, match.start() - 25)
                    end = min(len(content), match.end() + 25)
                    text = content[start:end]

                    # 判断 scope
                    scope = self._get_scope(keyword)

                    # 判断 severity
                    severity = self._get_severity(keyword, scope)

                    hits.append(HitItem(
                        line=line,
                        word=keyword,
                        text=text,
                        scope=scope,
                        severity=severity,
                    ))

        hits.sort(key=lambda h: h.line)
        return hits

    def check(
        self,
        hits: list[HitItem],
        requirement_evidence_lines: dict[int, list[str]],
    ) -> CoverageResult:
        """反向校验：检查 hits 是否被 requirements 覆盖

        Args:
            hits: 关键词命中列表
            requirement_evidence_lines: {line: [requirement_title, ...]}
                从 requirements 中提取每个 evidence node 对应的 line

        Returns:
            CoverageResult
        """
        if not hits:
            return CoverageResult(total_hits=0, covered=0, coverage_ratio=1.0)

        uncovered: list[UncoveredItem] = []
        covered_count = 0

        for hit in hits:
            # 检查该行是否被某个 requirement 覆盖
            covered_by = requirement_evidence_lines.get(hit.line, [])

            if covered_by:
                covered_count += 1
            else:
                uncovered.append(UncoveredItem(
                    line=hit.line,
                    word=hit.word,
                    text=hit.text,
                    severity=hit.severity,
                    reason=f"关键词 '{hit.word}' 命中的行未被任何 requirement 引用",
                ))

        total = len(hits)
        coverage_ratio = covered_count / total if total > 0 else 0.0

        # 统计 severity
        high_count = sum(1 for u in uncovered if u.severity == Severity.HIGH)
        medium_count = sum(1 for u in uncovered if u.severity == Severity.MEDIUM)
        low_count = sum(1 for u in uncovered if u.severity == Severity.LOW)

        # 生成告警
        warnings = []
        if high_count > 0:
            warnings.append(f"⚠️ {high_count} 条 high 级关键词未覆盖（包括否决/废标类）")
        if coverage_ratio < 0.8:
            warnings.append(f"⚠️ 覆盖率仅 {coverage_ratio:.1%}，可能存在漏提")

        result = CoverageResult(
            total_hits=total,
            covered=covered_count,
            uncovered=uncovered,
            coverage_ratio=coverage_ratio,
            high_count=high_count,
            medium_count=medium_count,
            low_count=low_count,
            warnings=warnings,
        )

        logger.info(
            f"[Coverage] hits={total} covered={covered_count} "
            f"uncovered={len(uncovered)} (high={high_count}) ratio={coverage_ratio:.2%}"
        )

        return result

    def check_from_db(
        self,
        document_version_id: UUID,
        project_id: UUID,
    ) -> CoverageResult:
        """从数据库读取数据执行反向校验

        优先走 RequirementEvidence → Evidence → DocumentNode 链路（Evidence 存在时）；
        Evidence 缺失时 fallback 到 Requirement.conditions 中的 evidence_node_ids。

        Args:
            document_version_id: 文档版本 ID
            project_id: 项目 ID

        Returns:
            CoverageResult
        """
        from app.db import models as m

        # 1. 读取候选节点，撒网扫描
        nodes = self._session.query(m.DocumentNode).filter(
            m.DocumentNode.document_version_id == document_version_id,
            m.DocumentNode.tender_req_candidate == True,
        ).all()

        hits = self.scan_hits(nodes)

        # 2. 构建 line → requirement_title 映射
        evidence_lines = self._build_evidence_lines(
            document_version_id, project_id
        )

        # 3. 执行校验
        return self.check(hits, evidence_lines)

    def _build_evidence_lines(
        self,
        document_version_id: UUID,
        project_id: UUID,
    ) -> dict[int, list[str]]:
        """构建 {order_no: [requirement_title]} 映射

        直接从 Requirement.conditions.evidence_order_nos 读取 order_no，
        不再依赖 Evidence 表和 RequirementEvidence 链路。
        """
        from app.db import models as m

        evidence_lines: dict[int, list[str]] = {}

        requirements = self._session.query(m.Requirement).filter(
            m.Requirement.project_id == project_id,
            m.Requirement.deleted_at.is_(None),
        ).all()

        for req in requirements:
            order_nos = (req.conditions or {}).get("evidence_order_nos", [])
            if not isinstance(order_nos, list):
                continue
            for order_no in order_nos:
                if isinstance(order_no, int) and order_no > 0:
                    evidence_lines.setdefault(order_no, []).append(req.title)

        return evidence_lines

    # --------------------------------------------------------------------------
    # 内部方法
    # --------------------------------------------------------------------------

    def _get_scope(self, keyword: str) -> list[str]:
        """判断关键词属于哪个 scope"""
        # 一级判决词 → bid_phase + evaluation_phase
        primary_scope = ["废标", "无效投标", "否决", "拒收", "不予受理", "取消资格"]
        if keyword in primary_scope:
            return ["bid_phase", "evaluation_phase"]

        # 合同期词 → 跳过（不在校验范围）
        contract_scope = ["付款", "支付", "结算", "履约", "违约", "保修", "质保"]
        if keyword in contract_scope:
            return ["contract_phase"]

        # 默认 bid_phase
        return ["bid_phase"]

    def _get_severity(self, keyword: str, scope: list[str]) -> Severity:
        """判断关键词的严重程度

        Severity 分级：
        - HIGH：STRONG_KEYWORDS（否决/废标类）+ bid_phase/evaluation_phase
        - MEDIUM：义务词（应当/必须/不得）+ bid_phase（但非 STRONG_KEYWORDS）
        - LOW：格式性要求（签字/盖章/密封）+ bid_phase + 非义务词
        """
        if keyword in STRONG_KEYWORDS and "bid_phase" in scope:
            return Severity.HIGH
        if keyword in STRONG_KEYWORDS and "evaluation_phase" in scope:
            return Severity.HIGH
        if "evaluation_phase" in scope:
            return Severity.MEDIUM
        if "bid_phase" in scope:
            # 义务词 → MEDIUM，格式性要求 → LOW
            obligation_words = {"必须", "应当", "不得", "不准", "严禁",
                                "不接受", "不允许", "不符合", "不满足"}
            if keyword in obligation_words:
                return Severity.MEDIUM
            return Severity.LOW
        return Severity.LOW
