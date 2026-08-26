"""LangSmith 可观测性配置

参考 WeKnora internal/tracing/langfuse，但使用 LangSmith。
启用方式：设置环境变量 LANGCHAIN_API_KEY
"""

import os
from typing import Any

from app.core.config import Settings


def configure_langsmith(settings: Settings) -> dict[str, Any] | None:
    """配置 LangSmith 追踪，返回 langchain 兼容的 config dict。

    启用条件：LANGCHAIN_API_KEY 环境变量已设置。
    未配置时返回 None，LangGraph 自动降级到无追踪模式。
    """
    api_key = os.getenv("LANGCHAIN_API_KEY")
    if not api_key:
        return None

    project = os.getenv("LANGSMITH_PROJECT", "ai-bid-advisor")

    return {
        "configurable": {
            "tags": ["tender-analysis"],
            "metadata": {
                "project": project,
                "version": "langgraph-v1",
            },
        },
        "callbacks": [],  # LangSmith 通过环境变量自动启用
    }


def langsmith_env() -> dict[str, str]:
    """返回 LangSmith 所需环境变量（供启动时检查）。"""
    api_key = os.getenv("LANGCHAIN_API_KEY", "")
    project = os.getenv("LANGSMITH_PROJECT", "ai-bid-advisor")
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "true" if api_key else "false")

    return {
        "LANGCHAIN_API_KEY": api_key,
        "LANGSMITH_PROJECT": project,
        "LANGCHAIN_TRACING_V2": tracing,
    }
