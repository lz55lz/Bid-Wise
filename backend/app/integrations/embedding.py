"""固定 bge-m3 模型的 OpenAI 兼容 Embedding 客户端。"""

import math
from collections.abc import Sequence

import httpx

from app.integrations.http_client import get_internal_http_client

from app.core.config import Settings
from app.core.constants import EMBEDDING_MODEL_ID
from app.modules.retrieval.models import EMBEDDING_DIMENSIONS


class EmbeddingUnavailable(Exception):
    """Embedding 服务未配置、超时或返回无效向量。"""


class BgeM3EmbeddingClient:
    """调用固定 bge-m3 端点；模型标识不接受调用方覆盖。"""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.embedding_base_url
        self._api_key = settings.embedding_api_key

    async def embed(self, contents: Sequence[str]) -> list[list[float]]:
        """按小批次生成向量并验证返回顺序、数值和维度。"""
        if not self._base_url:
            raise EmbeddingUnavailable("Embedding 服务未配置")
        vectors: list[list[float]] = []
        for start in range(0, len(contents), 32):
            vectors.extend(await self._embed_batch(list(contents[start : start + 32])))
        return vectors

    async def _embed_batch(self, contents: list[str]) -> list[list[float]]:
        try:
            # 向量服务通常部署在 localhost/内网；不能让桌面环境的 HTTP(S)_PROXY
            # 将其错误转发到网关。外部 LLM 与本地向量服务走不同客户端。
            client = await get_internal_http_client()
            api_key = self._api_key.get_secret_value() if self._api_key else None
            response = await client.post(
                f"{self._base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={"model": EMBEDDING_MODEL_ID, "input": contents},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingUnavailable("Embedding 服务请求失败") from exc
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(contents):
            raise EmbeddingUnavailable("Embedding 服务返回数量异常")
        vectors: list[list[float]] = []
        for expected_index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("index", expected_index) != expected_index:
                raise EmbeddingUnavailable("Embedding 服务返回顺序异常")
            raw_vector = row.get("embedding")
            if not isinstance(raw_vector, list):
                raise EmbeddingUnavailable("Embedding 服务返回向量异常")
            vector = [float(value) for value in raw_vector]
            if len(vector) != EMBEDDING_DIMENSIONS or not all(
                math.isfinite(value) for value in vector
            ):
                raise EmbeddingUnavailable("Embedding 向量维度或数值异常")
            vectors.append(vector)
        return vectors
