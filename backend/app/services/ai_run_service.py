import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.constants import EMBEDDING_MODEL_ID
from app.db.models import AiRun, AiRunEvidence
from app.db.repositories.search_repository import SearchRepository
from app.integrations.ai.embedding import EmbeddingClient, EmbeddingUnavailable


class AiRunService:
    """Durably records fixed-model calls without persisting inputs or secrets."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._search = SearchRepository(session)

    def embed(
        self,
        task_id: UUID,
        contents: list[str],
        evidence_ids: list[UUID],
        client: EmbeddingClient,
    ) -> list[list[float]]:
        run = self.start_call(
            task_id=task_id,
            scene="document_index",
            model_id=EMBEDDING_MODEL_ID,
            input_payload=contents,
            evidence_ids=evidence_ids,
        )

        started = perf_counter()
        try:
            vectors = client.embed(contents)
        except EmbeddingUnavailable:
            self._mark_failed(run, "AI_SERVICE_UNAVAILABLE", started)
            raise
        except Exception as exc:
            self._mark_failed(run, "AI_SERVICE_FAILED", started)
            raise EmbeddingUnavailable("embedding operation failed") from exc

        self.complete_call(run, vectors, started)
        return vectors

    def start_call(
        self,
        *,
        task_id: UUID | None,
        scene: str,
        model_id: str,
        input_payload: object,
        evidence_ids: list[UUID],
    ) -> AiRun:
        run = AiRun(
            id=uuid4(),
            task_id=task_id,
            scene=scene,
            model_id=model_id,
            input_hash=self._hash_payload(input_payload),
            status="RUNNING",
            created_at=datetime.now(UTC),
        )
        self._search.add_run(run)
        self._session.add_all(
            AiRunEvidence(ai_run_id=run.id, evidence_id=evidence_id)
            for evidence_id in sorted(set(evidence_ids))
        )
        self._session.commit()
        return run

    def complete_call(self, run: AiRun, output_payload: object, started: float) -> None:
        run.status = "SUCCEEDED"
        run.output_hash = self._hash_payload(output_payload)
        run.latency_ms = int((perf_counter() - started) * 1000)
        run.completed_at = datetime.now(UTC)
        self._session.commit()

    def fail_call(self, run: AiRun, error_code: str, started: float) -> None:
        self._mark_failed(run, error_code, started)

    def invalidate_call(self, run: AiRun, error_code: str, started: float) -> None:
        self._session.rollback()
        persisted = self._session.get(AiRun, run.id, with_for_update=True)
        if persisted is None:
            return
        persisted.status = "VALIDATION_FAILED"
        persisted.error_code = error_code
        persisted.latency_ms = int((perf_counter() - started) * 1000)
        persisted.completed_at = datetime.now(UTC)
        self._session.commit()

    def _mark_failed(self, run: AiRun, error_code: str, started: float) -> None:
        self._session.rollback()
        persisted = self._session.get(AiRun, run.id, with_for_update=True)
        if persisted is None:
            return
        persisted.status = "FAILED"
        persisted.error_code = error_code
        persisted.latency_ms = int((perf_counter() - started) * 1000)
        persisted.completed_at = datetime.now(UTC)
        self._session.commit()

    @staticmethod
    def _hash_payload(value: object) -> str:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
