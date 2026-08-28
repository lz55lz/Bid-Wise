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
from app.core.constants import RERANKER_MODEL_ID

logger = logging.getLogger(__name__)

# bge-reranker-v2-m3 context limit 512 token，每 doc 截断到 300 chars（约 75 tokens）
_RERANK_TRUNCATE_CHARS = 300


class RankerUnavailable(Exception):
    """The fixed bge-reranker-v2-m3 endpoint cannot be used safely."""


class RankerClient(Protocol):
    def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...


@runtime_checkable
class AsyncRankerClient(Protocol):
    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...


def _is_retryable_rerank_error(exc: Exception) -> bool:
    """Check if an exception should trigger a retry."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    # Some OpenAI-compatible rerankers intermittently return an incomplete
    # JSON body while their health endpoint remains green.  The same request
    # is safe to retry; validation still fails closed after the retry budget.
    if isinstance(exc, (TypeError, ValueError, KeyError)):
        return True
    return False


def _new_reranker_retry() -> AsyncRetrying:
    """Create a retry controller per request; AsyncRetrying is stateful."""
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_rerank_error),
        before_sleep=before_sleep_log(logger, logging.WARNING, exc_info=True),
    )


class BgeRerankerV2M3Client:
    """Fixed-model reranker accepting only the question and candidate text."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.reranker_base_url
        self._api_key = settings.reranker_api_key

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not self._base_url or not query or not documents:
            raise RankerUnavailable("reranker service is not configured")
        try:
            api_key = self._api_key.get_secret_value() if self._api_key else None
            url = f"{self._base_url.rstrip('/')}/rerank"
            truncated_docs = [doc[:_RERANK_TRUNCATE_CHARS] for doc in documents]
            payload = {
                "model": RERANKER_MODEL_ID,
                "query": query,
                "documents": truncated_docs,
            }
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            result_payload = response.json()
            rows = result_payload.get("results")
            if not isinstance(rows, list) or len(rows) != len(documents):
                raise ValueError("unexpected reranker result count")
            scores_by_index: dict[int, float] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("reranker result is invalid")
                index = row.get("index")
                score = row.get("relevance_score", row.get("score"))
                if not isinstance(index, int) or not isinstance(score, (int, float)):
                    raise ValueError("reranker result is invalid")
                if index in scores_by_index or not math.isfinite(float(score)):
                    raise ValueError("reranker result is invalid")
                scores_by_index[index] = float(score)
            if set(scores_by_index) != set(range(len(documents))):
                raise ValueError("reranker result indexes are invalid")
            scores = [scores_by_index[index] for index in range(len(documents))]
            return scores
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as exc:
            raise RankerUnavailable("reranker service response is unavailable") from exc


class AsyncBgeRerankerV2M3Client:
    """Async fixed-model reranker."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.reranker_base_url
        self._api_key = settings.reranker_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not self._base_url or not query or not documents:
            raise RankerUnavailable("reranker service is not configured")

        async def _do_rerank() -> list[float]:
            api_key = self._api_key.get_secret_value() if self._api_key else None
            url = f"{self._base_url.rstrip('/')}/rerank"
            truncated_docs = [doc[:_RERANK_TRUNCATE_CHARS] for doc in documents]
            payload = {
                "model": RERANKER_MODEL_ID,
                "query": query,
                "documents": truncated_docs,
            }
            client = await self._get_client()
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json=payload,
            )
            response.raise_for_status()
            result_payload = response.json()
            rows = result_payload.get("results")
            if not isinstance(rows, list) or len(rows) != len(documents):
                raise ValueError("unexpected reranker result count")
            scores_by_index: dict[int, float] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("reranker result is invalid")
                index = row.get("index")
                score = row.get("relevance_score", row.get("score"))
                if not isinstance(index, int) or not isinstance(score, (int, float)):
                    raise ValueError("reranker result is invalid")
                if index in scores_by_index or not math.isfinite(float(score)):
                    raise ValueError("reranker result is invalid")
                scores_by_index[index] = float(score)
            if set(scores_by_index) != set(range(len(documents))):
                raise ValueError("reranker result indexes are invalid")
            return [scores_by_index[index] for index in range(len(documents))]

        try:
            async for attempt in _new_reranker_retry():
                with attempt:
                    return await _do_rerank()
        except Exception as exc:
            raise RankerUnavailable(f"rerank failed after 3 attempts: {exc}") from exc
