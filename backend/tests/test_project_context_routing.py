from types import SimpleNamespace

from app.services.query_router_service import QueryRouterService, QuerySource, RouteContext


def test_generic_requirement_question_uses_selected_project_context() -> None:
    decision = QueryRouterService(settings=SimpleNamespace(ai_is_configured=False)).route(
        "有什么要求？", RouteContext(has_project_context=True)
    )

    assert decision.source == QuerySource.TENDER
