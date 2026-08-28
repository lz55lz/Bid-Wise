"""Query Router - decide which knowledge source for user question.

路由目标（五分类，MULTI 已移除）：
- small_talk：问候、能力介绍、致谢等无需检索的对话
- tender：问"本项目招标文件里写了什么"（条款/要求/预算/评分办法/时间节点）
- legal：问法律法规、条例条文的普适性问题
- report：问本项目的分析结论（项目事实、风险、匹配与报告）
- unclear：歧义，需要追问

判据设计：关键词只保留"强信号"（双关词如"风险""资质"不再进关键词表，
交给 LLM 结合项目上下文判断）；LLM 路由感知项目数据存在性。
"""

import logging
import re
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings

logger = logging.getLogger(__name__)


class QuerySource(StrEnum):
    """Available knowledge sources for routing."""

    SMALL_TALK = "small_talk"
    REPORT = "report"
    LEGAL = "legal"
    TENDER = "tender"
    UNCLEAR = "unclear"
    EMPTY = "empty"


class RouteContext(BaseModel):
    """项目数据存在性上下文（由调用方查库填充），供路由做存在性感知。"""

    has_project_context: bool = False
    has_report: bool = False
    has_tender_docs: bool = False


class RouteDecision(BaseModel):
    """Structured routing decision returned by the router."""

    source: QuerySource
    report_target: str | None = Field(
        default=None,
        description="Sub-target for report source: decision | risk | match | summary",
    )
    question: str | None = Field(default=None, description="Refined or original question")
    followup: str | None = Field(
        default=None, description="Follow-up question for unclear queries (max 80 chars)"
    )

    @field_validator("followup")
    @classmethod
    def _truncate_followup(cls, v: str | None) -> str | None:
        if v and len(v) > 80:
            return v[:77] + "..."
        return v


# ---------------------------------------------------------------------------
# Keyword-based routing（仅强信号，双关词交给 LLM）
# ---------------------------------------------------------------------------

# Ordered list of (patterns, source, report_target); first match wins.
_KEYWORD_RULES: list[tuple[list[str], QuerySource, str | None]] = [
    # --- Report：问分析结论/评分/建议（明确的结论类措辞） ---
    (
        [
            r"该不该投",
            r"建议投",
            r"要不要投",
            r"能不能投",
            r"投标建议",
            r"研判",
            r"分析报告",
            r"综合得分",
            r"综合评分",
            r"分析结论",
            r"下一步",
            r"行动计划",
            r"如何处理",
            r"准备什么",
        ],
        QuerySource.REPORT,
        "decision",
    ),
    # --- Report：风险检测结论（"风险"单独出现是双关词，不进关键词表） ---
    (
        [r"风险报告", r"风险检测", r"风险评估结果", r"风险等级", r"检测到.*风险"],
        QuerySource.REPORT,
        "risk",
    ),
    # --- Report：资质匹配度结论 ---
    (
        [r"匹配度", r"资质匹配", r"企业匹配", r"我们.*满足", r"本公司.*满足"],
        QuerySource.REPORT,
        "match",
    ),
    # --- Legal：明确的法条措辞 ---
    (
        [
            r"法律",
            r"法规",
            r"条例",
            r"司法解释",
            r"招标投标法",
            r"政府采购法",
            r"民法典",
            r"联合体.*协议",
        ],
        QuerySource.LEGAL,
        None,
    ),
]

_COMPILED_RULES: list[tuple[list[re.Pattern[str]], QuerySource, str | None]] = [
    ([re.compile(p) for p in patterns], source, target)
    for patterns, source, target in _KEYWORD_RULES
]

_SMALL_TALK_RE = re.compile(
    r"^\s*(?:你[好在吗]|嗨|哈喽|hello|hi|早上好|下午好|晚上好|谢谢|感谢|"
    r"你是谁|你能做什么|你会什么|帮助|帮我)(?:[！!。？，,～~ ]*)$",
    re.IGNORECASE,
)

# Short follow-ups such as “要求呢？” depend on the selected project and are
# common in multi-turn use.  Do not spend a model call asking the user to
# restate a project that is already in the trusted route context.
_PROJECT_TENDER_FOLLOWUP_RE = re.compile(
    r"^(?:那|这(?:个)?|本|该|上述)?(?:项目)?(?:有(?:哪些|什么)|哪些|什么)?(?:投标)?(?:要求|资格|资质|材料|条款|评分(?:办法)?|保函|保证金|截止时间)(?:呢|吗|怎么样|有哪些|是什么)?[？?！!。]*$"
)
_IMPLICIT_FOLLOWUP_RE = re.compile(
    r"^(?:那|这|这个|本|上述)?(?:个|项|条|部分|材料)?(?:呢|吗|么|呀|啊|为什么|为何|怎么|如何|具体|详细|第一项|第二项|第三项)[？?！!。]*$"
)


def _keyword_route(question: str) -> tuple[QuerySource, str | None]:
    """Fast keyword-based routing without LLM invocation."""
    for patterns, source, target in _COMPILED_RULES:
        if any(p.search(question) for p in patterns):
            return source, target
    return QuerySource.EMPTY, None


# ---------------------------------------------------------------------------
# LLM-based routing
# ---------------------------------------------------------------------------

_ROUTE_SYSTEM_PROMPT = """\
你是"投标参谋"系统的问答路由器。将用户问题分类到唯一知识源：

- tender：询问本项目招标文件的内容（条款、要求、预算、评分办法、时间节点等）
- legal：询问法律法规、条例条文的普适性问题（与本项目文件内容无关）
- report：询问本项目的分析结论、评分、风险检测结果、资质匹配度
- unclear：问题歧义，无法 confidently 分类

项目上下文：
- 当前会话已选定项目：{has_project_context}
- 该项目已生成投标分析报告：{has_report}
- 该项目已导入招标文件：{has_tender_docs}

分类规则：
- 问"某事怎么规定/是否合法/依据什么法"→ legal；问"本项目/这个项目的文件里写了什么"→ tender
- 当前会话已选定项目时，未指明主语的短问句（如"有什么要求？"）默认指该项目，
  不得追问是哪个项目
- report 仅用于分析结论类问题；若项目没有分析报告，不要路由到 report（改选 tender 或 unclear）
- 路由为 report 时设置 report_target：
  - "decision"：该不该投、结论、评分、建议
  - "risk"：风险检测结果
  - "match"：资质/企业匹配度
  - "summary"：报告整体概要

对 unclear 提供简短 followup，给出具体可选项（如"您想问招标文件内容还是法律条文？"）。

Respond strictly as JSON:
{{
  "source":"tender|legal|report|unclear",
  "report_target":"decision|risk|match|summary|null",
  "question":"refined question",
  "followup":"follow-up question or null"
}}\
"""


def _build_route_system_prompt(context: RouteContext | None) -> str:
    ctx = context or RouteContext()
    return _ROUTE_SYSTEM_PROMPT.format(
        has_project_context="是" if ctx.has_project_context else "否",
        has_report="是" if ctx.has_report else "否",
        has_tender_docs="是" if ctx.has_tender_docs else "否",
    )


class QueryRouterService:
    """Routes user questions to the appropriate knowledge source."""

    # Maximum question length to send to LLM (truncation guard)
    _MAX_QUESTION_LEN: ClassVar[int] = 2000

    def __init__(self, settings: Settings):
        self._settings = settings

    def route(
        self,
        question: str,
        context: RouteContext | None = None,
        conversation_context: str | None = None,
    ) -> RouteDecision:
        """Route a user question to a knowledge source.

        Tries fast keyword matching first; falls back to LLM-based routing
        (with project existence context) when keywords don't match.
        """
        question = question.strip()
        if not question:
            return RouteDecision(
                source=QuerySource.EMPTY,
                followup="请输入您的问题。",
            )

        # 闲聊不调用模型、不占用检索资源，也不会要求先选择项目。
        if _SMALL_TALK_RE.match(question):
            return RouteDecision(source=QuerySource.SMALL_TALK, question=question)

        if context and context.has_project_context and _PROJECT_TENDER_FOLLOWUP_RE.match(question):
            return RouteDecision(source=QuerySource.TENDER, question=question)

        # 1) Fast path: strong-signal keyword matching
        source, report_target = _keyword_route(question)
        if source != QuerySource.EMPTY:
            return RouteDecision(
                source=source,
                report_target=report_target,
                question=question,
            )

        # 2) Slow path: LLM-based routing with existence context
        return self._llm_route(conversation_context or question, context)

    def _llm_route(self, question: str, context: RouteContext | None = None) -> RouteDecision:
        """Use an LLM to classify the question when keywords are insufficient."""
        if not self._settings.ai_is_configured:
            logger.debug("AI not configured; returning EMPTY route for: %s", question[:100])
            return RouteDecision(
                source=QuerySource.UNCLEAR,
                question=question,
                followup="AI 未配置，无法智能路由。请尝试包含关键词的问题。",
            )

        # Lazy import to avoid circular dependencies at module load time
        from app.integrations.ai.llm import LangChainLlmClient

        truncated = question[: self._MAX_QUESTION_LEN]
        try:
            client = LangChainLlmClient.from_settings(self._settings)
            resp = client.generate(
                system=_build_route_system_prompt(context),
                user=f"Query: {truncated}",
                schema=RouteDecision,
            )

            if isinstance(resp, RouteDecision):
                # Ensure question is always populated
                if not resp.question:
                    resp.question = question
                return self._sanitize(resp)

            if isinstance(resp, dict):
                resp.setdefault("question", question)
                return self._sanitize(RouteDecision(**resp))

            logger.warning("Unexpected LLM response type: %s", type(resp).__name__)

        except Exception:
            logger.exception("LLM routing failed for question: %s", question[:100])

        # Final fallback
        return RouteDecision(
            source=QuerySource.UNCLEAR,
            question=question,
            followup="无法确定问题类型，请提供更具体的信息。",
        )

    def _sanitize(self, decision: RouteDecision) -> RouteDecision:
        """清洗 LLM 输出的越界枚举值，防止下游 KeyError。"""
        valid_targets = {"decision", "risk", "match", "summary"}
        if decision.source == QuerySource.REPORT:
            if decision.report_target not in valid_targets:
                decision.report_target = "decision"
        else:
            decision.report_target = None
        return decision
