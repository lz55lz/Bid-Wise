"""Agent 抽象层 — LangGraph Agent 架构的核心组件

设计参考：
- BidMaster-Pro core/skill_engine/base.py — Skill 基类
- -bid-analysis haha-code Agent — 工具调用模式
- LangGraph BaseTool — 工具定义规范

每个 Agent 包含：
1. name/description/category — 元信息
2. tools — 工具列表（函数装饰器 @tool）
3. system_prompt — 角色定义 + 任务描述
4. execute(state) — 核心执行逻辑，返回 AgentResult
5. should_interrupt(state) — 判断是否需要人工确认
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    pass


# ============================================================================
# 数据类：AgentResult
# ============================================================================


class AgentStatus(str, Enum):
    """Agent 执行状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_FOR_REVIEW = "waiting_for_review"


@dataclass
class AgentResult:
    """Agent 执行结果 — 类似 LangGraph 的 Command

    每个 pipeline 阶段返回结构化结果，包含：
    - success: 是否成功
    - data: 输出数据（写入 state）
    - warnings: 告警列表（不影响执行，但需人工关注）
    - needs_review: 是否需要人工确认
    - sources: 来源追溯（每条结果的原始节点）
    """

    success: bool
    status: AgentStatus = AgentStatus.COMPLETED

    # 输出数据（会被写入 state）
    data: dict = field(default_factory=dict)

    # 统计数据
    tokens_consumed: int = 0
    tools_called: int = 0
    runtime_ms: int = 0

    # 来源追溯
    sources: list[dict] = field(default_factory=list)

    # 错误信息
    error: str | None = None
    error_code: str | None = None

    # 告警（不影响执行，但需要人工关注）
    warnings: list[str] = field(default_factory=list)

    # 人工审核相关
    needs_review: bool = False
    review_note: str | None = None

    def add_warning(self, msg: str) -> None:
        """添加告警"""
        self.warnings.append(msg)

    def add_source(self, node_id: str, content: str, reason: str) -> None:
        """添加来源追溯"""
        self.sources.append({
            "node_id": node_id,
            "content_preview": content[:100],
            "reason": reason,
        })

    def to_json(self) -> str:
        return json.dumps({
            "success": self.success,
            "status": self.status.value,
            "data": self.data,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
            "error": self.error,
        }, ensure_ascii=False, indent=2)


# ============================================================================
# Tool 装饰器
# ============================================================================


@dataclass
class Tool:
    """工具定义 — 类似 LangGraph 的 BaseTool"""

    name: str
    description: str
    fn: Callable[..., Any]

    def __call__(self, **kwargs) -> Any:
        return self.fn(**kwargs)

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"


def tool(
    name: str | None = None,
    description: str = "",
):
    """工具装饰器 — 将函数标记为 Agent 可用的工具

    用法：
        @tool(name="query_candidates", description="查询候选节点")
        def query_candidates(document_version_id: str) -> list[dict]:
            ...
    """
    def decorator(fn: Callable) -> Tool:
        tool_name = name or fn.__name__
        return Tool(
            name=tool_name,
            description=description or fn.__doc__ or "",
            fn=fn,
        )
    return decorator


# ============================================================================
# BaseAgent 抽象基类
# ============================================================================


class BaseAgent:
    """Agent 基类 — 所有业务 Agent 的父类

    设计原则：
    1. 每个 Agent 是独立的智能体，有自己的工具集和决策逻辑
    2. Agent 之间通过 context.data 共享数据（类似 LangGraph state）
    3. 人工确认通过 needs_review 触发，由 orchestrator 处理

    子类只需实现：
    - name, description, category, version
    - system_prompt 属性（角色定义）
    - execute(ctx) -> AgentResult

    可选重写：
    - should_interrupt(ctx) -> bool
    - validate_input(ctx) -> None
    """

    name: str = ""
    description: str = ""
    category: str = ""  # parse / clean / extract / index / check / match
    version: str = "1.0.0"
    triggers: list[str] = field(default_factory=list)

    # 子类定义
    system_prompt: str = ""

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{self.__class__.__name__}.name must be defined")
        if not self.system_prompt:
            raise ValueError(f"{self.__class__.__name__}.system_prompt must be defined")

        self.logger = logging.getLogger(f"agent.{self.name}")

    def __repr__(self) -> str:
        return f"<Agent {self.name} v{self.version} [{self.category}]>"

    # --------------------------------------------------------------------------
    # 公共 API
    # --------------------------------------------------------------------------

    def execute(self, ctx: AgentContext) -> AgentResult:
        """执行 Agent — 子类必须实现"""
        raise NotImplementedError

    def safe_execute(self, ctx: AgentContext) -> AgentResult:
        """安全执行包装器 — 自动捕获异常、记录日志、计时"""
        start_time = time.monotonic()

        try:
            if ctx.progress_callback:
                ctx.progress_callback(self.name, AgentStatus.RUNNING.value, {})

            result = self.execute(ctx)

            result.runtime_ms = int((time.monotonic() - start_time) * 1000)
            result.status = AgentStatus.COMPLETED if result.success else AgentStatus.FAILED

            if result.needs_review:
                result.status = AgentStatus.WAITING_FOR_REVIEW

            if ctx.progress_callback:
                ctx.progress_callback(self.name, result.status.value, result.data)

            self.logger.info(
                f"[{self.name}] completed in {result.runtime_ms}ms "
                f"success={result.success} tools_called={result.tools_called}"
            )

            return result

        except Exception as e:
            runtime_ms = int((time.monotonic() - start_time) * 1000)
            error_detail = f"{str(e)}\n{traceback.format_exc()}"

            self.logger.error(f"[{self.name}] failed after {runtime_ms}ms: {e}")

            if ctx.progress_callback:
                ctx.progress_callback(self.name, AgentStatus.FAILED.value, {"error": str(e)})

            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=error_detail,
                error_code=getattr(e, "code", None) or "AGENT_EXECUTION_ERROR",
                runtime_ms=runtime_ms,
            )

    def should_interrupt(self, ctx: AgentContext) -> bool:
        """判断是否需要人工确认 — 默认不需要，子类可重写"""
        return False

    def validate_input(self, ctx: AgentContext) -> None:
        """输入校验 — 子类可重写，抛出 DomainError 表示校验失败"""
        pass


# ============================================================================
# AgentContext（放在这里避免循环导入）
# ============================================================================


@dataclass
class AgentContext:
    """Agent 执行上下文 — 类似 LangGraph 的 State"""

    project_id: str
    document_version_id: str | None = None
    thread_id: str | None = None
    actor_id: str | None = None
    role_codes: set[str] = field(default_factory=set)

    # 可选数据传递
    data: dict = field(default_factory=dict)

    # 进度回调
    progress_callback: Callable | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


# ============================================================================
# 泛型
# ============================================================================

F = TypeVar("F", bound=Callable[..., Any])
