"""项目问答运行时上下文。

这里保存的是应用服务可审计的本轮检索结果与治理信息，不是 LangGraph/Agent 状态。
"""

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """一次问答需要的确定性数据源计划。"""

    mode: str
    project: bool
    legal: bool = False
    report: bool = False
    enterprise_fit: bool = False
    memory: bool = False
    require_project: bool = False
    require_legal: bool = False
    require_report: bool = False
    require_enterprise_fit: bool = False

    @property
    def required_sources(self) -> frozenset[str]:
        sources: set[str] = set()
        if self.require_project:
            sources.add("PROJECT_EVIDENCE")
        if self.require_legal:
            sources.add("LEGAL_KNOWLEDGE")
        if self.require_report:
            sources.add("PROJECT_REPORT")
        if self.require_enterprise_fit:
            sources.add("ENTERPRISE_FIT")
        return frozenset(sources)


@dataclass(slots=True)
class AssistantTraceItem:
    """可安全返回给前端的问答执行摘要，不记录提示词或完整检索正文。"""

    operation: str
    status: str
    elapsed_ms: int
    detail: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "tool_name": self.operation,  # 保持现有 API trace 字段兼容。
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


@dataclass(slots=True)
class AssistantRunContext:
    """只由服务端创建的授权范围、检索计划和本轮引用白名单。"""

    actor_id: UUID
    role_codes: frozenset[str]
    project_id: UUID
    plan: RetrievalPlan
    project_access_verified: bool = False
    evidence_items: list[dict[str, object]] = field(default_factory=list)
    legal_items: list[dict[str, object]] = field(default_factory=list)
    report_items: list[dict[str, object]] = field(default_factory=list)
    enterprise_fit_items: list[dict[str, object]] = field(default_factory=list)
    memory_items: list[dict[str, object]] = field(default_factory=list)
    traces: list[AssistantTraceItem] = field(default_factory=list)
