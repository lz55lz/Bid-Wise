"""检索配置，对应 WeKnora internal/types/retrieval_config.go"""

from dataclasses import dataclass


@dataclass
class RetrievalConfig:
    """可配置的检索参数，用于 RAG 混合搜索和 RRF 融合。

    参考：WeKnora/internal/types/retrieval_config.go
    """

    # 向量检索返回数量（粗筛）
    embedding_top_k: int = 50

    # 向量相似度阈值（cosine similarity，越高越好，1=完美匹配）
    # pgvector 返回的是 cosine similarity（1 - cosine_distance）
    # BGE-M3 归一化嵌入的 cosine similarity 范围 [0, 1]
    # 默认 0.5：低于 0.5 的结果语义相关性太低，直接过滤
    vector_threshold: float = 0.5

    # 关键词匹配阈值（ts_rank 分数，无标准范围）
    keyword_threshold: float = 0.0

    # RRF 融合候选数（送入重排的宽度；最终上下文由 RAG_CONTEXT_LIMIT 截断）
    # 10 太窄：正确条文会在融合阶段被高频词块挤出，重排器根本看不到
    rerank_top_k: int = 30

    # Rerank 分数阈值（bge-reranker-v2-m3 输出范围约 -10 到 10）
    rerank_threshold: float = 0.0

    # RRF 融合常数，越大越扁平，减少对 top-1 的偏好
    # 标准值 60，敏感检索可用 30，宽松检索可用 100
    rrf_k: int = 60

    # RRF 向量权重（默认 0.7，向量检索优先）
    rrf_vector_weight: float = 0.7

    # RRF 关键词权重（默认 0.3，BM25 辅助）
    rrf_keyword_weight: float = 0.3

    # 事实性问题启用关键词增强（lei 特有）
    keyword_boost_enabled: bool = True

    def validate_weights(self) -> None:
        """确保权重和为 1."""
        total = self.rrf_vector_weight + self.rrf_keyword_weight
        if abs(total - 1.0) > 0.001:
            # 归一化
            self.rrf_vector_weight /= total
            self.rrf_keyword_weight /= total
