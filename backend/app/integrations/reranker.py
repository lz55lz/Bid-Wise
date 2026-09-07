"""固定 rankv2 模型的重排服务客户端。"""

import math
from collections.abc import Sequence

import httpx

from app.integrations.http_client import get_internal_http_client

from app.core.config import Settings
from app.core.constants import RERANKER_MODEL_ID


class RerankerUnavailable(Exception):
    """重排服务未配置、超时或返回无效结果。"""


class RankV2Reranker:
    """调用固定 rankv2 端点；调用方只能提供问题和候选文本。"""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.reranker_base_url
        self._api_key = settings.reranker_api_key

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        """按服务返回的 index 恢复候选顺序，并拒绝不完整或非有限分数。"""
        if not self._base_url:
            raise RerankerUnavailable("重排服务未配置")
        if not documents:
            return []
        try:
            # 同 embedding：本地 rankv2 需直连，避免代理把 /v1/rerank 变成 404。
            client = await get_internal_http_client()
            api_key = self._api_key.get_secret_value() if self._api_key else None
            response = await client.post(
                f"{self._base_url.rstrip('/')}/rerank",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": RERANKER_MODEL_ID,
                    "query": query,
                    "documents": [document[:2_000] for document in documents],
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RerankerUnavailable("重排服务请求失败") from exc
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise RerankerUnavailable("重排服务返回数量异常")
        scores: dict[int, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RerankerUnavailable("重排服务返回格式异常")
            index = row.get("index")
            score = row.get("relevance_score", row.get("score"))
            if not isinstance(index, int) or not isinstance(score, int | float):
                raise RerankerUnavailable("重排服务返回格式异常")
            if index in scores or not math.isfinite(float(score)):
                raise RerankerUnavailable("重排服务返回分数异常")
            scores[index] = float(score)
        if set(scores) != set(range(len(documents))):
            raise RerankerUnavailable("重排服务返回索引异常")
        return [scores[index] for index in range(len(documents))]
