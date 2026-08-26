from types import SimpleNamespace

from app.core.config import Settings
from app.integrations.ai.embedding import BgeM3Client
from app.integrations.ai.reranker import BgeRerankerV2M3Client


def test_embedding_client_supports_an_unauthenticated_local_service(monkeypatch) -> None:
    request: dict[str, object] = {}

    def fake_post(url: str, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"embedding": [0.25, 0.5]}]},
        )

    monkeypatch.setattr("app.integrations.ai.embedding.httpx.post", fake_post)

    vectors = BgeM3Client(
        Settings(embedding_base_url="http://embedding.example.invalid", embedding_api_key=None)
    ).embed(["test"])

    assert vectors == [[0.25, 0.5]]
    assert request["headers"] == {}
    assert request["json"] == {"model": "bge-m3", "input": ["test"]}


def test_reranker_client_supports_an_unauthenticated_local_service(monkeypatch) -> None:
    request: dict[str, object] = {}

    def fake_post(url: str, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "results": [
                    {"index": 1, "relevance_score": 0.4},
                    {"index": 0, "relevance_score": 0.8},
                ]
            },
        )

    monkeypatch.setattr("app.integrations.ai.reranker.httpx.post", fake_post)

    scores = BgeRerankerV2M3Client(
        Settings(reranker_base_url="http://reranker.example.invalid", reranker_api_key=None)
    ).rerank("question", ["first", "second"])

    assert scores == [0.8, 0.4]
    assert request["headers"] == {}
    assert request["json"] == {
        "model": "bge-reranker-v2-m3",
        "query": "question",
        "documents": ["first", "second"],
    }


def test_settings_reuses_existing_chat_llm_configuration() -> None:
    settings = Settings(
        llm_base_url=None,
        llm_api_key=None,
        chat_base_url="http://llm.example.invalid",
        chat_api_key="llm-key",
    )

    assert settings.llm_base_url == "http://llm.example.invalid"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "llm-key"
