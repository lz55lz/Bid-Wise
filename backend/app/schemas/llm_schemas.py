"""LLM 调用相关的 Pydantic Schema 定义。

用于 with_structured_output 的结构化输出校验。
"""
from pydantic import Field

from app.schemas.base import ApiSchema


class MultiQueryResult(ApiSchema):
    """多视角检索 query 生成结果。

    用于 query_rewrite_service 的 _llm_generate_queries。
    """
    queries: list[str] = Field(
        min_length=1,
        max_length=5,
        description="3-5个不同角度的检索查询",
    )
