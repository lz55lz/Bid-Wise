"""validate_node + route_after_validate

必填校验 + 置信度校验 → 决定是否需要人工复核
"""
from typing import Any, Literal

from sqlalchemy import text

from app.services.bid_pipeline.state import BidState
from app.services.observability import stage_task


@stage_task("validate")
async def validate_node(state: BidState) -> dict[str, Any]:
    """校验节点：检查必填缺失 + 低置信度"""
    from app.db.session import get_async_session_factory

    extracted = state.get("extracted_tags", {})
    validated = state.get("validated_tags", {})

    factory = get_async_session_factory()
    async with factory() as session:
        result = await session.execute(
            text("SELECT tag_code FROM bid_tag_dict WHERE level_code = 'P0' AND is_active = true")
        )
        p0_codes = {row[0] for row in result.fetchall()}
        extracted_codes = set(extracted.keys())

        missing_p0 = p0_codes - extracted_codes
        low_conf = {tc: v for tc, v in extracted.items() if v.get("confidence", 1.0) < 0.7}

        issues = [f"MISSING_REQUIRED:{code}" for code in missing_p0]
        issues += [f"LOW_CONFIDENCE:{tc}" for tc in low_conf]

        needs_review = len(issues) > 5 or len(low_conf) > 10 or bool(missing_p0)

        return {
            "validation_issues": issues,
            "needs_human_review": needs_review,
            "current_stage": "validate",
            "stage_status": {"validate": "done"},
        }


def route_after_validate(state: BidState) -> Literal["human_review", "fan_out"]:
    """条件路由：需要复核且 HITL 开启 → human_review；否则 → fan_out。

    HITL_ENABLED 关闭时直接旁路：interrupt() 依赖 checkpointer，
    未接入前进入 human_review 会因无法持久化中断而报错。
    """
    from app.core.constants import HITL_ENABLED

    if (
        HITL_ENABLED
        and state.get("needs_human_review")
        and state.get("review_round", 0) < 2
    ):
        return "human_review"
    return "fan_out"
