"""BM25Scorer — BM25 文本相似度评分

参考：BidMaster-Pro/core/rag_engine/retriever.py BM25 部分

功能：
- 预建 requirements 的 BM25 索引
- 计算 material.text vs requirement.text 的 BM25 score
- 作为 keyword 匹配的补充（keyword 命中不足时救回语义相关材料）

用法：
    scorer = BM25Scorer(session)
    scorer.build_index(project_id)  # 项目初始化时调用
    score = scorer.score(requirement_text, material_name)  # 匹配时调用
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

# BM25 参数
_BM25_K1 = 1.5   # 词频饱和参数
_BM25_B = 0.75   # 文档长度归一化参数
_BM25_THRESHOLD = 0.1  # BM25 score 阈值，低于此值视为不相关


class BM25Scorer:
    """BM25 文本相似度评分器

    在 requirements 初始化时 build_index，
    匹配时对每个 (requirement, material) 计算 BM25 score。
    """

    def __init__(self, session) -> None:
        self._session = session
        self._index: dict[UUID, dict] = {}  # requirement_id -> {tokens, avgdl, doclen, df, idf}

    def build_index(self, project_id: UUID) -> None:
        """构建项目的 BM25 索引

        在 requirements 导入后调用一次。
        """
        from app.db import models as m

        requirements = self._session.query(m.Requirement).filter(
            m.Requirement.project_id == project_id,
            m.Requirement.deleted_at.is_(None),
        ).all()

        # 收集所有文档
        docs: dict[UUID, list[str]] = {}
        all_tokens: list[list[str]] = []
        for req in requirements:
            tokens = self._tokenize((req.title or "") + " " + (req.description or ""))
            docs[req.id] = tokens
            all_tokens.append(tokens)

        if not all_tokens:
            self._index = {}
            return

        # 计算全局统计
        avgdl = sum(len(t) for t in all_tokens) / len(all_tokens)
        n_docs = len(all_tokens)

        # 计算 DF 和 IDF
        vocab: dict[str, int] = {}
        df: dict[str, int] = Counter()
        for tokens in all_tokens:
            for t in set(tokens):
                df[t] += 1
                if t not in vocab:
                    vocab[t] = len(vocab)

        idf: dict[str, float] = {}
        for term, freq in df.items():
            idf[term] = np.log((n_docs - freq + 0.5) / (freq + 0.5) + 1)

        # 构建倒排索引
        doclens: dict[UUID, int] = {}
        for req_id, tokens in docs.items():
            doclens[req_id] = len(tokens)

        self._index = {
            "docs": docs,
            "vocab": vocab,
            "idf": idf,
            "doclens": doclens,
            "avgdl": avgdl,
            "n_docs": n_docs,
        }
        logger.info(f"[BM25] indexed {len(docs)} requirements, vocab={len(vocab)}")

    def score(self, requirement_text: str, material_name: str) -> float:
        """计算 material 相对于 requirement 的 BM25 score

        Args:
            requirement_text: requirement 的 title + description
            material_name: 企业材料的名称

        Returns:
            BM25 score，越高表示越相关
        """
        if not self._index or not material_name:
            return 0.0

        req_tokens = self._tokenize(requirement_text)
        mat_tokens = self._tokenize(material_name)
        if not req_tokens or not mat_tokens:
            return 0.0

        docs = self._index["docs"]
        avgdl = self._index["avgdl"]
        idf = self._index["idf"]
        doclens = self._index["doclens"]

        # 对每个 requirement token 计算 BM25
        scores: dict[UUID, float] = {}
        for req_id, req_doc_tokens in docs.items():
            score = 0.0
            doclen = doclens.get(req_id, 0)
            doc_tf = Counter(req_doc_tokens)

            for mat_t in mat_tokens:
                if mat_t not in idf:
                    continue
                tf = doc_tf.get(mat_t, 0)
                if tf == 0:
                    continue
                idf_val = idf[mat_t]
                numerator = tf * (_BM25_K1 + 1)
                denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doclen / avgdl)
                score += idf_val * numerator / denominator

            if score > 0:
                scores[req_id] = score

        if not scores:
            return 0.0

        # 返回所有 requirements 的最大 BM25 score
        # 即：这个 material 与该项目下某个 requirement 的最佳匹配度
        return max(scores.values()) if scores else 0.0

    def is_relevant(self, requirement_text: str, material_name: str) -> bool:
        """判断 material 是否与 requirement 语义相关（BM25 阈值判断）"""
        return self.score(requirement_text, material_name) >= _BM25_THRESHOLD

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中英文分词"""
        tokens: list[str] = []
        for char in text:
            if "一" <= char <= "鿿":
                tokens.append(char)  # 按字符分词（中文）
            else:
                for word in re.findall(r"[a-zA-Z0-9]+", char):
                    if word:
                        tokens.append(word.lower())
        return tokens
