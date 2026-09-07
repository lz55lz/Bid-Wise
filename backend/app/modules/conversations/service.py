"""项目对话用例编排：授权、持久化、受控检索问答和引用校验。"""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.modules.conversations.assistant_context import AssistantRunContext
from app.modules.conversations.assistant_workflow import ProjectAssistantWorkflow
from app.modules.conversations.models import Conversation, ConversationMessage
from app.modules.conversations.repository import ConversationRepository
from app.modules.conversations.retrieval_plan import (
    build_retrieval_query,
    plan_global_legal_retrieval,
    plan_retrieval,
)
from app.modules.conversations.schemas import (
    ConversationCitation,
    ConversationCreateRequest,
    ConversationMessagePage,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationUpdateRequest,
)
from app.modules.identity.service import AuthenticatedUser
from app.modules.projects.service import ProjectService

_HISTORY_LIMIT = 12
_EVIDENCE_CITATION_PATTERN = re.compile(r"【Evidence:\s*([0-9a-fA-F-]{36})】")
_LEGAL_CITATION_PATTERN = re.compile(r"【Legal:\s*([0-9a-fA-F-]{36})】")
_REPORT_CITATION_PATTERN = re.compile(r"【Report:\s*([0-9a-fA-F-]{36})】")
_ANY_CITATION_MARKER_PATTERN = re.compile(r"【(?:Evidence|Legal|Report):[^】]*】")
_GLOBAL_LEGAL_SCOPE_ID = UUID(int=0)


class ConversationService:
    """会话应用服务；授权、消息持久化与问答工作流边界保持明确。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repository = ConversationRepository(session)
        self._projects = ProjectService(session)

    async def create(
        self,
        project_id: UUID,
        actor: AuthenticatedUser,
        payload: ConversationCreateRequest,
    ) -> ConversationResponse:
        """成员可创建自己的私有会话。"""
        await self._projects.require_project_access(project_id, actor)
        now = datetime.now(UTC)
        conversation = Conversation(
            project_id=project_id,
            user_id=actor.id,
            title=payload.title,
            created_at=now,
            updated_at=now,
        )
        self._repository.add_conversation(conversation)
        await self._session.flush()
        return ConversationResponse.model_validate(conversation)

    async def create_global(
        self, actor: AuthenticatedUser, payload: ConversationCreateRequest
    ) -> ConversationResponse:
        """创建不读取任何项目资料的全局法律会话。"""
        now = datetime.now(UTC)
        conversation = Conversation(
            project_id=None,
            user_id=actor.id,
            title=payload.title,
            created_at=now,
            updated_at=now,
        )
        self._repository.add_conversation(conversation)
        await self._session.flush()
        return ConversationResponse.model_validate(conversation)

    async def list_owned(
        self, project_id: UUID, actor: AuthenticatedUser
    ) -> list[ConversationResponse]:
        """管理员也不越权读取其他成员的私有会话。"""
        await self._projects.require_project_access(project_id, actor)
        return [
            ConversationResponse.model_validate(item)
            for item in await self._repository.list_owned(project_id, actor.id)
        ]

    async def list_global_owned(self, actor: AuthenticatedUser) -> list[ConversationResponse]:
        return [
            ConversationResponse.model_validate(item)
            for item in await self._repository.list_global_owned(actor.id)
        ]

    async def list_messages(
        self, project_id: UUID, conversation_id: UUID, actor: AuthenticatedUser
    ) -> list[ConversationMessageResponse]:
        """读取会话消息前同时检查项目和所有权。"""
        await self._require_owned(project_id, conversation_id, actor)
        messages = await self._repository.list_messages(conversation_id, 100)
        return [self._message_response(item) for item in messages]

    async def list_message_page(
        self,
        project_id: UUID,
        conversation_id: UUID,
        actor: AuthenticatedUser,
        offset: int,
        limit: int,
    ) -> ConversationMessagePage:
        await self._require_owned(project_id, conversation_id, actor)
        messages, total = await self._repository.list_message_page(conversation_id, offset, limit)
        return ConversationMessagePage(
            items=[self._message_response(item) for item in messages],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def update(
        self,
        project_id: UUID,
        conversation_id: UUID,
        actor: AuthenticatedUser,
        payload: ConversationUpdateRequest,
    ) -> ConversationResponse:
        conversation = await self._require_owned(project_id, conversation_id, actor)
        conversation.title = payload.title
        conversation.updated_at = datetime.now(UTC)
        return ConversationResponse.model_validate(conversation)

    async def delete(
        self, project_id: UUID, conversation_id: UUID, actor: AuthenticatedUser
    ) -> None:
        conversation = await self._require_owned(project_id, conversation_id, actor)
        await self._repository.delete_conversation(conversation)

    async def ask(
        self,
        project_id: UUID,
        conversation_id: UUID,
        actor: AuthenticatedUser,
        question: str,
    ) -> ConversationMessageResponse:
        """先固化用户问题，再执行受控检索问答，最后留存可校验引用。"""
        conversation = await self._require_owned(project_id, conversation_id, actor)
        history = await self._repository.list_messages(conversation.id, _HISTORY_LIMIT)
        has_enterprise_fit_history = self._has_enterprise_fit_history(history)
        plan = (
            plan_global_legal_retrieval(question, has_history=bool(history))
            if project_id is None
            else plan_retrieval(question, has_enterprise_fit_history=has_enterprise_fit_history)
        )
        if project_id is None and (plan.require_project or plan.enterprise_fit):
            raise DomainError(
                "PROJECT_CONTEXT_REQUIRED", "该问题需要结合具体项目材料，请先选择项目", 409
            )
        if not self._settings.ai_is_configured:
            raise DomainError("AI_NOT_CONFIGURED", "AI 服务尚未完成部署配置", 503)
        now = datetime.now(UTC)
        self._repository.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                role="USER",
                content=question,
                citations=[],
                traces=[],
                created_at=now,
            )
        )
        self._set_initial_title(conversation, question)
        # 先提交用户输入；外部模型超时也不会令用户的问题丢失。
        conversation.updated_at = now
        await self._session.commit()

        history_pairs = [(item.role, item.content) for item in history]
        context = self._assistant_context(
            project_id or _GLOBAL_LEGAL_SCOPE_ID, actor, question, plan
        )
        retrieval_query = build_retrieval_query(question, history_pairs)
        try:
            answer = await ProjectAssistantWorkflow(self._settings, self._session).answer(
                history_pairs, question, retrieval_query, context
            )
        except Exception as exc:
            raise DomainError("ASSISTANT_UNAVAILABLE", "项目问答暂不可用", 503) from exc
        answer = self._strip_disallowed_citation_markers(answer, context)
        citations = self._verified_citations(answer, context)
        if not self._citations_cover_plan(citations, context):
            answer = "未找到足够的可验证证据，暂不能给出可靠结论。"
            citations = []
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="ASSISTANT",
            content=answer,
            citations=[citation.model_dump(mode="json") for citation in citations],
            traces=[trace.to_payload() for trace in context.traces],
            created_at=datetime.now(UTC),
        )
        conversation.updated_at = assistant_message.created_at
        self._repository.add_message(assistant_message)
        await self._session.flush()
        return self._message_response(assistant_message)

    async def ask_stream(
        self, project_id: UUID, conversation_id: UUID, actor: AuthenticatedUser, question: str
    ) -> AsyncIterator[dict[str, object]]:
        """SSE 期间先持久化用户问题，结束后一次性固化可审计助手回答。"""
        conversation = await self._require_owned(project_id, conversation_id, actor)
        history = await self._repository.list_messages(conversation.id, _HISTORY_LIMIT)
        has_enterprise_fit_history = self._has_enterprise_fit_history(history)
        plan = (
            plan_global_legal_retrieval(question, has_history=bool(history))
            if project_id is None
            else plan_retrieval(question, has_enterprise_fit_history=has_enterprise_fit_history)
        )
        if project_id is None and (plan.require_project or plan.enterprise_fit):
            raise DomainError(
                "PROJECT_CONTEXT_REQUIRED", "该问题需要结合具体项目材料，请先选择项目", 409
            )
        if not self._settings.ai_is_configured:
            raise DomainError("AI_NOT_CONFIGURED", "AI 服务尚未完成部署配置", 503)
        now = datetime.now(UTC)
        self._repository.add_message(
            ConversationMessage(
                conversation_id=conversation.id,
                role="USER",
                content=question,
                citations=[],
                traces=[],
                created_at=now,
            )
        )
        self._set_initial_title(conversation, question)
        conversation.updated_at = now
        await self._session.commit()
        history_pairs = [(item.role, item.content) for item in history]
        context = self._assistant_context(
            project_id or _GLOBAL_LEGAL_SCOPE_ID, actor, question, plan
        )
        retrieval_query = build_retrieval_query(question, history_pairs)
        answer_parts: list[str] = []
        try:
            async for token in ProjectAssistantWorkflow(self._settings, self._session).stream(
                history_pairs, question, retrieval_query, context
            ):
                answer_parts.append(token)
                yield {"type": "token", "content": token}
        except Exception:
            yield {
                "type": "error",
                "code": "ASSISTANT_UNAVAILABLE",
                "message": "项目问答暂不可用",
            }
            return
        streamed_answer = "".join(answer_parts).strip() or "未找到证据"
        answer = self._strip_disallowed_citation_markers(streamed_answer, context)
        if answer != streamed_answer:
            # 流式 token 已发出时，用 replacement 将不可信标记从最终消息和页面移除。
            yield {"type": "replace", "content": answer}
        citations = self._verified_citations(answer, context)
        if not self._citations_cover_plan(citations, context):
            answer = "未找到足够的可验证证据，暂不能给出可靠结论。"
            citations = []
            # 真流式无法撤回已经发出的 token；显式 replacement 事件让前端以最终
            # 审计结果覆盖暂存文本，done 中的持久化消息仍是唯一事实源。
            yield {"type": "replace", "content": answer}
        message = ConversationMessage(
            conversation_id=conversation.id,
            role="ASSISTANT",
            content=answer,
            citations=[item.model_dump(mode="json") for item in citations],
            traces=[item.to_payload() for item in context.traces],
            created_at=datetime.now(UTC),
        )
        conversation.updated_at = message.created_at
        self._repository.add_message(message)
        await self._session.commit()
        yield {"type": "done", "message": self._message_response(message).model_dump(mode="json")}

    async def _require_owned(
        self, project_id: UUID, conversation_id: UUID, actor: AuthenticatedUser
    ) -> Conversation:
        if project_id is None:
            conversation = await self._repository.get_global_owned(conversation_id, actor.id)
            if conversation is None:
                raise DomainError("CONVERSATION_NOT_FOUND", "会话不存在或无权访问", 404)
            return conversation
        await self._projects.require_project_access(project_id, actor)
        conversation = await self._repository.get_owned(conversation_id, project_id, actor.id)
        if conversation is None:
            raise DomainError("CONVERSATION_NOT_FOUND", "会话不存在或无权访问", 404)
        return conversation

    @staticmethod
    def _assistant_context(
        project_id: UUID, actor: AuthenticatedUser, question: str, plan=None
    ) -> AssistantRunContext:
        """授权完成后构造本轮受控问答上下文；请求和模型都不能覆盖项目范围。"""
        return AssistantRunContext(
            actor_id=actor.id,
            role_codes=actor.role_codes,
            project_id=project_id,
            plan=plan or plan_retrieval(question),
            project_access_verified=True,
        )

    @staticmethod
    def _set_initial_title(conversation: Conversation, question: str) -> None:
        """用首条提问命名默认会话，保留用户手工修改过的标题。"""
        if conversation.title not in {"项目问答", "新对话"}:
            return
        # 标题按数据库字段限制截断；换行和连续空白压成一个空格，方便在侧栏阅读。
        title = " ".join(question.split())[:256]
        if title:
            conversation.title = title

    @staticmethod
    def _has_enterprise_fit_history(history: list[ConversationMessage]) -> bool:
        """只根据服务端执行轨迹延续企业适配上下文，不能相信模型自称查过企业。"""
        return any(
            any(
                trace.get("tool_name") == "enterprise_fit_retrieval"
                and trace.get("status") == "completed"
                for trace in (message.traces or [])
                if isinstance(trace, dict)
            )
            for message in history
            if message.role == "ASSISTANT"
        )

    @staticmethod
    def _strip_disallowed_citation_markers(answer: str, context: AssistantRunContext) -> str:
        """企业匹配没有 Evidence UUID 时，移除模型臆造的引用标签。"""
        if (
            context.plan.enterprise_fit
            and not context.evidence_items
            and not context.legal_items
            and not context.report_items
        ):
            return _ANY_CITATION_MARKER_PATTERN.sub("", answer).strip()
        return answer

    @staticmethod
    def _verified_citations(
        answer: str, context: AssistantRunContext
    ) -> list[ConversationCitation]:
        """只接受本轮实际暴露给模型的来源 ID，拒绝模型编造引用。"""
        evidence_by_id = {str(item["evidence_id"]): item for item in context.evidence_items}
        legal_by_id = {str(item["citation_id"]): item for item in context.legal_items}
        citations = [
            ConversationCitation(
                source="PROJECT_EVIDENCE",
                evidence_id=evidence_id,
                quoted_text=str(evidence_by_id[evidence_id]["quoted_text"]),
                locator=dict(evidence_by_id[evidence_id]["locator"]),
            )
            for evidence_id in dict.fromkeys(_EVIDENCE_CITATION_PATTERN.findall(answer))
            if evidence_id in evidence_by_id
        ]
        citations.extend(
            ConversationCitation(
                source="LEGAL_KNOWLEDGE",
                knowledge_chunk_id=citation_id,
                quoted_text=str(legal_by_id[citation_id]["quoted_text"]),
                locator={
                    "title": str(legal_by_id[citation_id]["title"]),
                    "source_reference": str(legal_by_id[citation_id]["source_reference"]),
                    "section_path": legal_by_id[citation_id].get("section_path"),
                },
            )
            for citation_id in dict.fromkeys(_LEGAL_CITATION_PATTERN.findall(answer))
            if citation_id in legal_by_id
        )
        report_by_id = {str(item["report_id"]): item for item in context.report_items}
        citations.extend(
            ConversationCitation(
                source="PROJECT_REPORT",
                report_id=report_id,
                quoted_text=str(report_by_id[report_id]["content"])[:2_000],
                locator={
                    "completed_at": report_by_id[report_id]["completed_at"],
                    "underlying_citations": report_by_id[report_id]["citations"],
                },
            )
            for report_id in dict.fromkeys(_REPORT_CITATION_PATTERN.findall(answer))
            if report_id in report_by_id
        )
        return citations

    @staticmethod
    def _citations_cover_plan(
        citations: list[ConversationCitation], context: AssistantRunContext
    ) -> bool:
        """本轮要求的事实来源必须真正出现在最终引用里；寒暄无需引用。"""
        sources = {item.source for item in citations}
        # ENTERPRISE_FIT 是服务端按项目权限读取的实时业务摘要（绑定企业、确认材料和
        # 匹配结果），不是招标原文或知识库切片，不能伪造成 Evidence 引用。其是否可用
        # 已由工作流的 required_sources / enterprise_fit_items 覆盖校验保证。
        citation_required_sources = context.plan.required_sources - {"ENTERPRISE_FIT"}
        return citation_required_sources.issubset(sources)

    @staticmethod
    def _message_response(message: ConversationMessage) -> ConversationMessageResponse:
        return ConversationMessageResponse(
            id=message.id,
            role=message.role,
            content=message.content,
            citations=[ConversationCitation.model_validate(item) for item in message.citations],
            traces=message.traces,
            created_at=message.created_at,
        )
