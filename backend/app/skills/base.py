"""Skill 抽象层 — 定义 Skill 接口和上下文"""

from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


@dataclass
class SkillContext:
    """Skill 执行上下文"""

    project_id: UUID | str
    db: Any = None  # Session | AsyncSession
    llm: Any = None  # RequirementLlm
    parameters: dict = field(default_factory=dict)
    knowledge_base: Any = None
    progress_callback: Callable | None = None

    # Pipeline 特有字段（可选）
    document_version_id: UUID | str | None = None
    thread_id: str | None = None


@dataclass
class SkillResult:
    """Skill 执行结果"""

    success: bool
    data: dict = field(default_factory=dict)
    tokens_consumed: int = 0
    sources: list[dict] = field(default_factory=list)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    stage_name: str | None = None  # 如 "clean", "extract"


class Skill(ABC):
    """Skill 抽象基类 — 所有业务能力单元的接口

    设计参考：
    - BidMaster-Pro core/skill_engine/base.py
    - tender-review-kit 判词库体系
    - -bid-analysis 关键词得分制

    使用方式：
        class MySkill(Skill):
            name = "my_skill"
            description = "我的自定义技能"
            category = "extract"
            version = "1.0.0"
            triggers = ["触发词"]

            async def execute(self, ctx: SkillContext) -> SkillResult:
                # 业务逻辑
                return SkillResult(success=True, data={})
    """

    name: str = ""
    description: str = ""
    category: str = ""  # parse / clean / extract / index / check / format
    version: str = "1.0.0"
    triggers: list[str] = field(default_factory=list)

    @abstractmethod
    async def execute(self, ctx: SkillContext) -> SkillResult:
        """执行 Skill 逻辑，子类必须实现"""
        ...

    async def safe_execute(self, ctx: SkillContext) -> SkillResult:
        """安全执行包装器，自动捕获异常并记录"""
        try:
            if ctx.progress_callback:
                await ctx.progress_callback(self.name, "started", {})
            result = await self.execute(ctx)
            if ctx.progress_callback:
                await ctx.progress_callback(self.name, "completed", result.data)
            return result
        except Exception as e:
            if ctx.progress_callback:
                await ctx.progress_callback(self.name, "failed", {"error": str(e)})
            return SkillResult(
                success=False,
                error=f"{str(e)}\n{traceback.format_exc()}",
            )

    def __repr__(self) -> str:
        return f"<Skill {self.name} v{self.version} [{self.category}]>"
