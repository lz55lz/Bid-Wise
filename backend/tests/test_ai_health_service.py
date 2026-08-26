from types import SimpleNamespace

from app.core.config import Settings
from app.services.ai_health_service import AiHealthService


class RecordingVectorStore:
    def __init__(self) -> None:
        self.dimension: int | None = None

    def validate_embedding_dimension(self, dimension: int) -> None:
        self.dimension = dimension


def _configured_settings() -> Settings:
    return Settings(
        llm_base_url="http://llm.example.invalid",
        llm_api_key="llm-key",
        reranker_base_url="http://reranker.example.invalid",
        embedding_base_url="http://embedding.example.invalid",
    )


def test_ai_health_rejects_a_service_that_reports_the_wrong_fixed_model(monkeypatch) -> None:
    settings = _configured_settings()

    def fake_get(url: str, **kwargs):
        del kwargs
        model_id = {
            "http://llm.example.invalid/models": "MiniMax-M3",
            "http://reranker.example.invalid/models": "not-bge-reranker-v2-m3",
            "http://embedding.example.invalid/models": "bge-m3",
        }[url]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": model_id}]},
        )

    monkeypatch.setattr("app.services.ai_health_service.httpx.get", fake_get)
    monkeypatch.setattr(
        "app.services.ai_health_service.BgeM3Client.embed",
        lambda self, contents: [[0.25, 0.5, 0.75] for _ in contents],
    )
    report = AiHealthService(settings, RecordingVectorStore()).check()

    assert report.llm is True
    assert report.reranker is False
    assert report.embedding is True
    assert report.embedding_dimension is True
    assert report.available is False


def test_ai_health_validates_embedding_dimension(monkeypatch) -> None:
    settings = _configured_settings()
    store = RecordingVectorStore()

    def fake_get(url: str, **kwargs):
        del url, kwargs
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "data": [
                    {"id": "MiniMax-M3"},
                    {"id": "bge-reranker-v2-m3"},
                    {"id": "bge-m3"},
                ]
            },
        )

    def fake_embed(self, contents):
        del self
        return [[0.25, 0.5, 0.75] for _ in contents]

    monkeypatch.setattr("app.services.ai_health_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.ai_health_service.BgeM3Client.embed", fake_embed)

    report = AiHealthService(settings, store).check()

    assert report.available is True
    assert store.dimension == 3


def test_ai_health_is_degraded_when_ai_connection_information_is_missing() -> None:
    report = AiHealthService(
        Settings(
            llm_base_url=None,
            llm_api_key=None,
            chat_base_url=None,
            chat_api_key=None,
            reranker_base_url=None,
            reranker_api_key=None,
            embedding_base_url=None,
            embedding_api_key=None,
        ),
        RecordingVectorStore(),
    ).check()

    assert report.available is False
    assert report.as_dict()["checks"] == {
        "llm": False,
        "reranker": False,
        "embedding": False,
        "embedding_dimension": False,
    }
