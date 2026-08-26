import logging
import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.core.constants import EMBEDDING_MODEL_ID

logger = logging.getLogger(__name__)


class EmbeddingUnavailable(Exception):
    """The fixed bge-m3 endpoint cannot be used safely."""


class EmbeddingClient(Protocol):
    def embed(self, contents: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class AsyncEmbeddingClient(Protocol):
    async def embed(self, contents: Sequence[str]) -> list[list[float]]: ...


def _is_retryable_embedding_error(exc: Exception) -> bool:
    """Check if an exception should trigger a retry."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


# Retry config for embedding
_EMBEDDING_RETRY = AsyncRetrying(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable_embedding_error),
    before_sleep=before_sleep_log(logger, logging.WARNING, exc_info=True),
)


# 单批 embedding 条数上限（WeKnora 分批策略），避免大文档单次请求超时
_EMBED_BATCH_SIZE = 32


class BgeM3Client:
    """OpenAI-compatible embedding adapter with a fixed server-side model ID."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.embedding_base_url
        self._api_key = settings.embedding_api_key

    def embed(self, contents: Sequence[str]) -> list[list[float]]:
        if not self._base_url or not contents:
            raise EmbeddingUnavailable("embedding service is not configured")
        vectors: list[list[float]] = []
        for i in range(0, len(contents), _EMBED_BATCH_SIZE):
            vectors.extend(self._embed_batch(list(contents[i : i + _EMBED_BATCH_SIZE])))
        return vectors

    def _embed_batch(self, contents: list[str]) -> list[list[float]]:
        try:
            api_key = self._api_key.get_secret_value() if self._api_key else None
            response = httpx.post(
                f"{self._base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={"model": EMBEDDING_MODEL_ID, "input": contents},
                timeout=30.0,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != len(contents):
                raise ValueError("unexpected embedding count")
            for idx, row in enumerate(rows):
                returned_index = row.get("index")
                # 部分本地服务不返回 index，仅在返回时校验顺序
                if returned_index is not None and returned_index != idx:
                    raise ValueError(f"embedding index mismatch at {idx}: got {returned_index}")
            vectors = [self._validate_vector(row.get("embedding")) for row in rows]
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            raise EmbeddingUnavailable("embedding service response is unavailable") from exc
        if len({len(vector) for vector in vectors}) != 1:
            raise EmbeddingUnavailable("embedding dimensions are inconsistent")
        return vectors

    @staticmethod
    def _validate_vector(value: object) -> list[float]:
        if not isinstance(value, list) or not value:
            raise ValueError("embedding vector is invalid")
        vector = [float(component) for component in value]
        if not all(math.isfinite(component) for component in vector):
            raise ValueError("embedding vector contains a non-finite value")
        return vector


class AsyncBgeM3Client:
    """Async OpenAI-compatible embedding adapter."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.embedding_base_url
        self._api_key = settings.embedding_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def embed(self, contents: Sequence[str]) -> list[list[float]]:
        if not self._base_url or not contents:
            raise EmbeddingUnavailable("embedding service is not configured")
        vectors: list[list[float]] = []
        for i in range(0, len(contents), _EMBED_BATCH_SIZE):
            vectors.extend(await self._embed_batch(list(contents[i : i + _EMBED_BATCH_SIZE])))
        return vectors

    async def _embed_batch(self, contents: list[str]) -> list[list[float]]:
        async def _do_embed() -> list[list[float]]:
            api_key = self._api_key.get_secret_value() if self._api_key else None
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={"model": EMBEDDING_MODEL_ID, "input": contents},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != len(contents):
                raise ValueError("unexpected embedding count")
            for idx, row in enumerate(rows):
                returned_index = row.get("index")
                # 部分本地服务不返回 index，仅在返回时校验顺序
                if returned_index is not None and returned_index != idx:
                    raise ValueError(f"embedding index mismatch at {idx}: got {returned_index}")
            vectors = [self._validate_vector(row.get("embedding")) for row in rows]
            if len({len(vector) for vector in vectors}) != 1:
                raise EmbeddingUnavailable("embedding dimensions are inconsistent")
            return vectors

        try:
            async for attempt in _EMBEDDING_RETRY:
                with attempt:
                    return await _do_embed()
        except Exception as exc:
            raise EmbeddingUnavailable(f"embedding failed after 3 attempts: {exc}") from exc

    @staticmethod
    def _validate_vector(value: object) -> list[float]:
        if not isinstance(value, list) or not value:
            raise ValueError("embedding vector is invalid")
        vector = [float(component) for component in value]
        if not all(math.isfinite(component) for component in vector):
            raise ValueError("embedding vector contains a non-finite value")
        return vector
