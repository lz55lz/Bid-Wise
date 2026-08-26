"""CompletenessChecker — 完整性护栏（第二道墙）

参考：tender-review-kit/scripts/check_completeness.py

设计原则：
- 通用"明显偏少"判定，不依赖具体类型
- 纯程序规则，不调用 LLM
- 可作为独立服务使用

用法：
    checker = CompletenessChecker(session)
    result = checker.check(project_id)
    if result.warnings:
        for w in result.warnings:
            print(f"⚠️ {w}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)


# 通用"明显偏少"阈值
MIN_DISQ = 8       # 废标条数下限
MIN_SCORING = 5    # 评分条数下限
MIN_CERT = 5       # 证明材料条数下限
EMPHASIS_RATIO = 0.8  # ▲ 覆盖率要求


@dataclass
class CompletenessResult:
    """完整性校验结果"""
    disq_count: int = 0       # 废标条数
    scoring_count: int = 0    # 评分条数
    cert_count: int = 0       # 证明材料条数
    emphasis_count: int = 0   # ▲/★ 标识条数
    emphasis_hits: int = 0    # 撒网识别的强调标识数

    warnings: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return len(self.warnings) == 0


class CompletenessChecker:
    """完整性护栏检查器

    检查维度：
    1. 废标条数 < 8 → 警告
    2. 评分条数 < 5 → 警告
    3. 证明材料 < 5 → 警告
    4. 评分项不含"分"字 → 警告（可能只填了大类没展开梯度）
    5. ▲/★ 覆盖率 < 80% → 警告（可能压缩了标识项）
    """

    def __init__(self, session) -> None:
        self._session = session

    def check(
        self,
        project_id: UUID,
        emphasis_hits: int = 0,
    ) -> CompletenessResult:
        """执行完整性校验

        Args:
            project_id: 项目 ID
            emphasis_hits: 撒网扫描到的强调标识总数（来自 CoverageChecker）

        Returns:
            CompletenessResult
        """
        from app.db import models as m

        requirements = self._session.query(m.Requirement).filter(
            m.Requirement.project_id == project_id,
            m.Requirement.deleted_at.is_(None),
        ).all()

        warnings: list[str] = []

        # 按 category 分类计数
        disq_count = 0    # 废标类（QUALIFICATION + BUSINESS 中含否决词的）
        scoring_count = 0
        cert_count = 0    # 证明材料类
        emphasis_count = 0  # 含 ▲/★ 的
        scoring_without_fen = []  # 不含"分"的评分项

        for req in requirements:
            title = req.title or ""
            desc = req.description or ""
            combined = title + desc

            # 评分类
            if req.category == "SCORING":
                scoring_count += 1
                if "分" not in combined:
                    scoring_without_fen.append(title[:30])

            # 证明材料类（含证书/认证/社保等关键词）
            if any(kw in combined for kw in ["证书", "认证", "检测", "社保", "报告", "资质"]):
                cert_count += 1

            # 含 ▲/★ 标识（从 title 或 conditions 推断）
            if any(mark in combined for mark in ["▲", "★", "◆", "●"]):
                emphasis_count += 1

            # 废标类（BUSINESS/QUALIFICATION 中含否决关键词）
            if req.category in ("BUSINESS", "QUALIFICATION"):
                if any(kw in combined for kw in ["否决", "废标", "无效", "拒收", "不予", "取消资格", "视为"]):
                    disq_count += 1

        # 逐项校验
        if disq_count < MIN_DISQ:
            warnings.append(
                f"废标条数 {disq_count} < 通用基线 {MIN_DISQ}（明显偏少，可能漏识别或专项未跑）"
            )
        if scoring_count < MIN_SCORING:
            warnings.append(
                f"评分条数 {scoring_count} < 通用基线 {MIN_SCORING}（明显偏少，评分细则可能未拆全）"
            )
        if cert_count > 0 and cert_count < MIN_CERT:
            warnings.append(
                f"证明材料 {cert_count} < 通用基线 {MIN_CERT}（可能聚合不全，检测/认证要求都进了吗）"
            )
        if scoring_without_fen:
            warnings.append(
                f"{len(scoring_without_fen)} 条评分项不含「分」字（可能只填了大类摘要，没展开逐档梯度）"
            )
        if emphasis_hits > 0 and emphasis_count < emphasis_hits * EMPHASIS_RATIO:
            warnings.append(
                f"▲/★ 清单 {emphasis_count} 条 < 撒网 {emphasis_hits} × 80% = {int(emphasis_hits * EMPHASIS_RATIO)}（可能压缩了标识项）"
            )

        result = CompletenessResult(
            disq_count=disq_count,
            scoring_count=scoring_count,
            cert_count=cert_count,
            emphasis_count=emphasis_count,
            emphasis_hits=emphasis_hits,
            warnings=warnings,
        )

        logger.info(
            f"[Completeness] disq={disq_count} scoring={scoring_count} "
            f"cert={cert_count} emphasis={emphasis_count}/{emphasis_hits} "
            f"warnings={len(warnings)}"
        )
        return result

    def check_with_hits(
        self,
        project_id: UUID,
        hits: list,  # CoverageChecker HitItem list
    ) -> CompletenessResult:
        """结合 CoverageChecker 的 hits 结果执行完整性校验

        Args:
            project_id: 项目 ID
            hits: CoverageChecker.scan_hits() 返回的命中列表
        """
        # 统计 emphasis_marks 类型的 hits
        emphasis_hits = sum(
            1 for h in hits
            if getattr(h, "word", "") in ("▲", "★", "◆", "●", "※", "■", "◇", "☆")
        )
        return self.check(project_id, emphasis_hits=emphasis_hits)
