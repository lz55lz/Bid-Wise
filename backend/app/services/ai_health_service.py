"""Health verification for the three server-owned AI integrations.

The availability result is deliberately free of endpoint URLs and credentials so
it can be returned by an operator endpoint without exposing deployment secrets.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.constants import EMBEDDING_MODEL_ID, LLM_MODEL_ID, RERANKER_MODEL_ID
from app.integrations.ai.embedding import BgeM3Client, EmbeddingUnavailable
from app.integrations.vector_store import VectorStoreUnavailable


class EmbeddingDimensionValidator(Protocol):
    def validate_embedding_dimension(self, dimension: int) -> None: ...


@dataclass(frozen=True, slots=True)
class AiHealthReport:
    """A redacted, machine-readable availability snapshot."""

    llm: bool
    reranker: bool
    embedding: bool
    embedding_dimension: bool

    @property
    def available(self) -> bool:
        return self.llm and self.reranker and self.embedding and self.embedding_dimension

    @property
    def status(self) -> str:
        return "ok" if self.available else "degraded"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ai_available": self.available,
            "checks": {
                "llm": self.llm,
                "reranker": self.reranker,
                "embedding": self.embedding,
                "embedding_dimension": self.embedding_dimension,
            },
        }


class AiHealthService:
    """Validate fixed model identities and the pgvector embedding dimension."""

    def __init__(
        self,
        settings: Settings,
        vector_store: EmbeddingDimensionValidator,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._settings = settings
        self._vector_store = vector_store
        self._timeout_seconds = timeout_seconds

    def check(self) -> AiHealthReport:
        """Run all checks without leaking a transport or configuration exception."""
        if not self._settings.ai_is_configured:
            return AiHealthReport(False, False, False, False)

        llm = self._has_expected_model(
            self._settings.llm_base_url,
            self._settings.llm_api_key.get_secret_value() if self._settings.llm_api_key else None,
            LLM_MODEL_ID,
            requires_api_key=True,
        )
        reranker = self._has_expected_model(
            self._settings.reranker_base_url,
            (
                self._settings.reranker_api_key.get_secret_value()
                if self._settings.reranker_api_key
                else None
            ),
            RERANKER_MODEL_ID,
            requires_api_key=False,
        )
        embedding = self._has_expected_model(
            self._settings.embedding_base_url,
            (
                self._settings.embedding_api_key.get_secret_value()
                if self._settings.embedding_api_key
                else None
            ),
            EMBEDDING_MODEL_ID,
            requires_api_key=False,
        )
        dimension = self._validate_embedding_dimension() if embedding else False
        return AiHealthReport(llm, reranker, embedding, dimension)

    def _has_expected_model(
        self,
        base_url: str | None,
        api_key: str | None,
        expected_model_id: str,
        *,
        requires_api_key: bool,
    ) -> bool:
        if not base_url or (requires_api_key and not api_key):
            return False
        try:
            response = httpx.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            return expected_model_id in self._model_ids(response.json())
        except (httpx.HTTPError, TypeError, ValueError):
            return False

    def _validate_embedding_dimension(self) -> bool:
        try:
            vectors = BgeM3Client(self._settings).embed(["health check"])
            if len(vectors) != 1 or not vectors[0]:
                return False
            self._vector_store.validate_embedding_dimension(len(vectors[0]))
        except (EmbeddingUnavailable, VectorStoreUnavailable, ValueError, TypeError):
            return False
        return True

    @staticmethod
    def _model_ids(payload: object) -> set[str]:
        """Accept the standard OpenAI ``/models`` forms, but no fuzzy aliases."""
        if not isinstance(payload, dict):
            return set()
        ids: set[str] = set()
        direct = payload.get("id", payload.get("model"))
        if isinstance(direct, str):
            ids.add(direct)
        for key in ("data", "models"):
            rows = payload.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if isinstance(row, str):
                    ids.add(row)
                elif isinstance(row, dict):
                    value = row.get("id", row.get("model"))
                    if isinstance(value, str):
                        ids.add(value)
        return ids
