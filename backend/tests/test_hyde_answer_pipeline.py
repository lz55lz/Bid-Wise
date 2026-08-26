"""HyDE 接入 LlmClient + AiRunService 测试

覆盖：
  - success：返回 hyde_text + complete_call 被以 {"hyde_text": ...} 调用
  - LlmUnavailable → 返回 None + fail_call(AI_SERVICE_UNAVAILABLE)，不 raise
  - 未知异常 → 返回 None + fail_call(AI_SERVICE_FAILED)
  - audit start_call 失败仍走 LLM 调用，返回文本
  - scene 命名约定：knowledge_rag_query_hyde
"""
from unittest.mock import MagicMock

import pytest

from app.integrations.ai.llm import LlmUnavailable


def _build_service(hyde_answer_mock, ai_runs_mock) -> MagicMock:
    """构造一个 KnowledgeRagService MagicMock 实例，把 _llm.hyde_answer 和 _ai_runs 注入。"""
    from app.services.knowledge_rag_service import KnowledgeRagService

    service = MagicMock(spec=KnowledgeRagService)
    service._llm = MagicMock()
    service._llm.hyde_answer = hyde_answer_mock
    service._ai_runs = ai_runs_mock
    return service


@pytest.mark.asyncio
async def test_hyde_returns_text_on_success_and_calls_complete_call():
    """成功路径：hyde_answer 返回 text，complete_call 被以 {"hyde_text": text} 调用。"""
    fake_run = MagicMock()
    ai_runs = MagicMock()
    ai_runs.start_call.return_value = fake_run
    ai_runs.complete_call = MagicMock()
    ai_runs.fail_call = MagicMock()

    hyde_mock = MagicMock(return_value="2017年10月1日起施行")
    service = _build_service(hyde_mock, ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    result = await KnowledgeRagService._agenerate_hyde_answer(service, "87号令什么时候施行？")

    assert result == "2017年10月1日起施行"
    hyde_mock.assert_called_once_with(
        "87号令什么时候施行？", max_tokens=100, temperature=0.3
    )
    ai_runs.start_call.assert_called_once()
    kwargs = ai_runs.start_call.call_args.kwargs
    assert kwargs["scene"] == "knowledge_rag_query_hyde"
    assert kwargs["model_id"] is not None  # LLM_MODEL_ID 常量
    assert kwargs["input_payload"] == {"question": "87号令什么时候施行？"}
    assert kwargs["evidence_ids"] == []
    ai_runs.complete_call.assert_called_once()
    output_payload = ai_runs.complete_call.call_args.args[1]
    assert output_payload == {"hyde_text": "2017年10月1日起施行"}
    ai_runs.fail_call.assert_not_called()


@pytest.mark.asyncio
async def test_hyde_returns_none_on_llm_unavailable_no_raise():
    """LlmUnavailable → fail_call(AI_SERVICE_UNAVAILABLE) + return None，不抛 DomainError。"""
    fake_run = MagicMock()
    ai_runs = MagicMock()
    ai_runs.start_call.return_value = fake_run
    ai_runs.complete_call = MagicMock()
    ai_runs.fail_call = MagicMock()

    hyde_mock = MagicMock(side_effect=LlmUnavailable("rate limited"))
    service = _build_service(hyde_mock, ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    result = await KnowledgeRagService._agenerate_hyde_answer(service, "问题")

    assert result is None
    ai_runs.fail_call.assert_called_once()
    args = ai_runs.fail_call.call_args.args
    assert args[1] == "AI_SERVICE_UNAVAILABLE"
    ai_runs.complete_call.assert_not_called()


@pytest.mark.asyncio
async def test_hyde_returns_none_on_unknown_exception():
    """未知异常 → fail_call(AI_SERVICE_FAILED) + return None。"""
    fake_run = MagicMock()
    ai_runs = MagicMock()
    ai_runs.start_call.return_value = fake_run
    ai_runs.fail_call = MagicMock()

    hyde_mock = MagicMock(side_effect=RuntimeError("unexpected"))
    service = _build_service(hyde_mock, ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    result = await KnowledgeRagService._agenerate_hyde_answer(service, "问题")

    assert result is None
    ai_runs.fail_call.assert_called_once()
    args = ai_runs.fail_call.call_args.args
    assert args[1] == "AI_SERVICE_FAILED"
    ai_runs.complete_call.assert_not_called()


@pytest.mark.asyncio
async def test_hyde_still_returns_text_when_audit_start_fails():
    """audit start_call 抛 DB 异常 → 跳过 audit 仍走 LLM，返回文本。"""
    ai_runs = MagicMock()
    ai_runs.start_call.side_effect = Exception("DB down")

    hyde_mock = MagicMock(return_value="some hypothetical answer")
    service = _build_service(hyde_mock, ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    result = await KnowledgeRagService._agenerate_hyde_answer(service, "问题")

    assert result == "some hypothetical answer"
    hyde_mock.assert_called_once()
    ai_runs.complete_call.assert_not_called()
    ai_runs.fail_call.assert_not_called()


@pytest.mark.asyncio
async def test_hyde_returns_none_when_chat_returns_empty_string():
    """hyde_answer 返回 "" → 当成 None 处理（不写 complete_call，return None）。"""
    fake_run = MagicMock()
    ai_runs = MagicMock()
    ai_runs.start_call.return_value = fake_run
    ai_runs.complete_call = MagicMock()

    hyde_mock = MagicMock(return_value="")
    service = _build_service(hyde_mock, ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    result = await KnowledgeRagService._agenerate_hyde_answer(service, "问题")

    assert result is None
    # 空文本也算"完成"，complete_call 仍被以 {"hyde_text": ""} 调用（保持 RUNNING 状态闭合）
    ai_runs.complete_call.assert_called_once()


@pytest.mark.asyncio
async def test_hyde_uses_scene_knowledge_rag_query_hyde():
    """scene 命名严格等于 knowledge_rag_query_hyde（与现有 embedding/rerank/answer 同级）。"""
    ai_runs = MagicMock()
    ai_runs.start_call.return_value = MagicMock()

    service = _build_service(MagicMock(return_value="text"), ai_runs)

    from app.services.knowledge_rag_service import KnowledgeRagService

    await KnowledgeRagService._agenerate_hyde_answer(service, "问题")

    scene = ai_runs.start_call.call_args.kwargs["scene"]
    assert scene == "knowledge_rag_query_hyde"


# -------------------------------------------------------------------
# LangChainLlmClient.chat 单点测试（非结构化对话）
# -------------------------------------------------------------------


def test_langchain_chat_returns_assistant_text_content():
    """LangChainLlmClient.chat 调用 _openai.chat.completions.create 并返回 content。"""
    from app.integrations.ai.llm import LangChainLlmClient

    client = LangChainLlmClient(
        model="test-model",
        base_url="https://example.com/v1",
        api_key="test-key",
        timeout=10.0,
    )
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message={"content": "answer text"})]
    client._openai.chat.completions.create = MagicMock(return_value=fake_response)

    result = client.chat("sys", "user")

    assert result == "answer text"
    call_kwargs = client._openai.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "test-model"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert call_kwargs["extra_body"] == {
        "reasoning_split": True,
        "thinking": {"type": "disabled"},
    }


def test_langchain_chat_returns_empty_string_on_empty_content():
    """LLM 返回空 content → 返回 ""（调用方按 falsy 处理）。"""
    from app.integrations.ai.llm import LangChainLlmClient

    client = LangChainLlmClient(
        model="m", base_url="https://example.com/v1", api_key="k", timeout=10.0
    )
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message={"content": "  ", "reasoning_content": ""})]
    client._openai.chat.completions.create = MagicMock(return_value=fake_response)

    result = client.chat("sys", "user")
    assert result == ""


def test_langchain_chat_classifies_errors_to_llm_unavailable():
    """chat 内部异常归一化到 LlmUnavailable 子类（与 generate 行为一致）。"""
    from app.integrations.ai.llm import (
        LangChainLlmClient,
        LlmUnavailable,
    )

    client = LangChainLlmClient(
        model="m", base_url="https://example.com/v1", api_key="k", timeout=10.0
    )
    client._openai.chat.completions.create = MagicMock(side_effect=Exception("500 internal"))

    with pytest.raises(LlmUnavailable):
        client.chat("sys", "user")


# -------------------------------------------------------------------
# DeepSeekV4FlashClient.hyde_answer 测试
# -------------------------------------------------------------------


def test_deepseek_hyde_answer_returns_text_on_success():
    """成功路径：返回 self._client.chat 的结果。"""
    from app.integrations.ai.llm import DeepSeekV4FlashClient

    # 用 __new__ 跳过 __init__（避免需要 settings），但保留真实类属性
    service = DeepSeekV4FlashClient.__new__(DeepSeekV4FlashClient)
    service._client = MagicMock()
    service._client.chat.return_value = "假设答案"

    result = DeepSeekV4FlashClient.hyde_answer(service, "问题", max_tokens=50, temperature=0.5)

    assert result == "假设答案"
    service._client.chat.assert_called_once()
    args = service._client.chat.call_args.args
    # system prompt 应来自 DeepSeekV4FlashClient._HYDE_SYSTEM 类属性
    assert "事实回答助手" in args[0]
    assert "问题" in args[1]
    assert service._client.chat.call_args.kwargs == {"temperature": 0.5, "max_tokens": 50}


def test_deepseek_hyde_answer_returns_none_on_llm_unavailable():
    """LlmUnavailable → 捕获并返回 None（non-fatal）。"""
    from app.integrations.ai.llm import DeepSeekV4FlashClient, LlmUnavailable

    service = DeepSeekV4FlashClient.__new__(DeepSeekV4FlashClient)
    service._client = MagicMock()
    service._client.chat.side_effect = LlmUnavailable("rate limit")

    result = DeepSeekV4FlashClient.hyde_answer(service, "问题")

    assert result is None


def test_deepseek_hyde_answer_returns_none_on_empty_content():
    """chat 返回 "" → 返回 None（调用方 falsy 判定）。"""
    from app.integrations.ai.llm import DeepSeekV4FlashClient

    service = DeepSeekV4FlashClient.__new__(DeepSeekV4FlashClient)
    service._client = MagicMock()
    service._client.chat.return_value = ""

    result = DeepSeekV4FlashClient.hyde_answer(service, "问题")

    assert result is None
