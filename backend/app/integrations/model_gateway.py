"""统一 LLM 模型配置入口，避免 Agent/抽取/报告各自复制 Provider 参数。"""

from enum import StrEnum

from langchain.chat_models import init_chat_model

from app.core.config import Settings
from app.core.constants import LLM_MODEL_ID


class ModelProfile(StrEnum):
    ASSISTANT = "ASSISTANT"
    STRUCTURED = "STRUCTURED"
    EXTRACTION = "EXTRACTION"
    REPORT = "REPORT"


_PROFILE_CONFIG: dict[ModelProfile, dict[str, object]] = {
    # 项目问答默认应便于快速决策；用户明确要求展开时由提示词放宽篇幅。
    ModelProfile.ASSISTANT: {"timeout": 30, "max_retries": 2, "max_tokens": 900},
    ModelProfile.STRUCTURED: {
        "timeout": 90,
        "max_retries": 2,
        "model_kwargs": {"response_format": {"type": "json_object"}},
    },
    ModelProfile.EXTRACTION: {
        "timeout": 60,
        "max_retries": 2,
        "max_tokens": 4_096,
        "model_kwargs": {"response_format": {"type": "json_object"}},
    },
    ModelProfile.REPORT: {"timeout": 90, "max_retries": 2, "max_tokens": 4_096},
}


def create_chat_model(
    settings: Settings,
    profile: ModelProfile,
    *,
    max_tokens: int | None = None,
):
    """创建固定 Provider/模型的 LangChain ChatModel；业务层只选择用途 profile。"""
    if not settings.llm_base_url or not settings.llm_api_key:
        raise RuntimeError("LLM 服务未配置")
    config = dict(_PROFILE_CONFIG[profile])
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    return init_chat_model(
        model=LLM_MODEL_ID,
        model_provider="openai",
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        temperature=0,
        # MiniMax-M3 默认可能输出 reasoning；所有正式业务调用统一关闭。
        extra_body={"thinking": {"type": "disabled"}},
        **config,
    )
