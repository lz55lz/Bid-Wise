"""KeywordScoringService — 关键词得分制段落筛选

参考：-bid-analysis/src/extractor/scoring.py

功能：
- 根据三级关键词（high=7, medium=4, low=2）计算节点得分
- 结合 section_keywords + text_keywords 双维度评分
- 将 score 写入 node.cleaning_metadata，供后续优先级处理

用法：
    scorer = KeywordScoringService()
    scorer.score_nodes(nodes)  # 就地修改 nodes 的 cleaning_metadata
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# 全局配置缓存
_config: dict | None = None


def _load_config() -> dict:
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "data" / "keyword_scoring.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                _config = yaml.safe_load(f)
        else:
            _config = {"tier_scores": {"high": 7, "medium": 4, "low": 2}, "global_threshold": 3, "dimensions": {}}
    return _config


class KeywordScoringService:
    """关键词得分计算器"""

    def __init__(self) -> None:
        self._config = _load_config()

    def score_node(self, content: str, section_path: str) -> tuple[int, list[str]]:
        """计算单个节点的关键词得分

        Args:
            content: 节点正文内容
            section_path: 章节路径

        Returns:
            (score, matched_keywords) — 得分和匹配到的关键词列表
        """
        tier_scores = self._config.get("tier_scores", {"high": 7, "medium": 4, "low": 2})
        dimensions = self._config.get("dimensions", {})

        score = 0
        matched: list[str] = []

        # 1. section_path 匹配
        sec_kws = dimensions.get("section", {})
        for tier_name, keywords in sec_kws.items():
            tier_score = tier_scores.get(tier_name, 0)
            for kw in keywords:
                if kw in (section_path or ""):
                    score += tier_score
                    matched.append(f"sec:{kw}({tier_name})")

        # 2. 正文内容匹配
        text_kws = dimensions.get("text", {})
        for tier_name, keywords in text_kws.items():
            tier_score = tier_scores.get(tier_name, 0)
            for kw in keywords:
                if kw in (content or ""):
                    score += tier_score
                    matched.append(f"txt:{kw}({tier_name})")

        return score, matched

    def score_nodes(self, nodes: list) -> None:
        """批量计算节点得分，就地写入 cleaning_metadata

        Args:
            nodes: DocumentNode 列表（带 cleaning_metadata 属性）
        """
        if not nodes:
            return

        threshold = self._config.get("global_threshold", 3)
        total = len(nodes)
        above = 0
        below = 0

        for node in nodes:
            content = getattr(node, "cleaned_content", None) or ""
            section = getattr(node, "section_path", None) or ""
            score, matched = self.score_node(content, section)

            # 写入 cleaning_metadata
            meta = dict(getattr(node, "cleaning_metadata", {}) or {})
            meta["keyword_score"] = score
            meta["keyword_matched"] = matched
            node.cleaning_metadata = meta

            if score >= threshold:
                above += 1
            else:
                below += 1

        logger.info(
            f"[KeywordScoring] scored {total} nodes: "
            f"{above} above threshold, {below} below threshold"
        )

    def get_score(self, node) -> int:
        """从 node 读取 keyword_score，没有则返回 0"""
        meta = getattr(node, "cleaning_metadata", None) or {}
        return meta.get("keyword_score", 0)
