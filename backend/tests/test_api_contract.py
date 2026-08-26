from fastapi.testclient import TestClient

import app.main as app_main
from app.main import create_app
from app.services.ai_health_service import AiHealthReport


def test_health_reports_ai_unavailable_without_deployment_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        app_main, "_check_ai_health", lambda: AiHealthReport(False, False, False, False)
    )
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ai_available"] is False


def test_model_override_is_rejected_before_business_execution() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "x", "model": "other"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "MODEL_OVERRIDE_FORBIDDEN"


def test_internal_ai_health_is_redacted_when_ai_is_not_available(monkeypatch) -> None:
    monkeypatch.setattr(
        app_main, "_check_ai_health", lambda: AiHealthReport(False, False, False, False)
    )
    client = TestClient(create_app())

    response = client.get("/internal/health/ai")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "ai_available": False,
        "checks": {
            "llm": False,
            "reranker": False,
            "embedding": False,
            "embedding_dimension": False,
        },
    }


def test_internal_ai_health_rechecks_a_degraded_startup_result(monkeypatch) -> None:
    reports = iter(
        (
            AiHealthReport(False, False, False, False),
            AiHealthReport(True, True, True, True),
        )
    )
    monkeypatch.setattr(app_main, "_check_ai_health", lambda: next(reports))
    app = create_app()
    client = TestClient(app)

    first = client.get("/internal/health/ai")
    second = client.get("/internal/health/ai")

    assert first.status_code == 503
    assert second.status_code == 200
    assert second.json()["checks"]["embedding_dimension"] is True


def test_internal_ai_health_caches_a_healthy_result(monkeypatch) -> None:
    calls = 0

    def check() -> AiHealthReport:
        nonlocal calls
        calls += 1
        return AiHealthReport(True, True, True, True)

    monkeypatch.setattr(app_main, "_check_ai_health", check)
    client = TestClient(create_app())

    assert client.get("/internal/health/ai").status_code == 200
    assert client.get("/internal/health/ai").status_code == 200
    assert calls == 1


def test_srs_project_scoped_write_paths_are_exposed() -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert "/api/v1/projects/{project_id}/requirements/{requirement_id}" in paths
    assert "/api/v1/projects/{project_id}/matches/{match_id}" in paths
    assert "/api/v1/projects/{project_id}/reports" in paths


def test_knowledge_and_unified_assistant_paths_are_exposed() -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    expected = {
        "/api/v1/knowledge-entries",
        "/api/v1/knowledge-entries/documents",
        "/api/v1/knowledge-entries/{entry_id}/documents",
        "/api/v1/chat/stream",
    }
    assert expected.issubset(paths)
