"""The single channel-neutral streaming conversation service.

The service owns one chat turn end-to-end: session resolution, route decision,
retrieval, native LLM token streaming and one-time message persistence.  HTTP
and IM adapters only translate their transport into this contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.db.models import Session as ChatSession
from app.db.repositories.evidence_repository import EvidenceRepository
from app.db.repositories.session_repository import MessageRepository
from app.integrations.ai.embedding import AsyncBgeM3Client
from app.integrations.ai.llm import DeepSeekV4FlashClient
from app.integrations.ai.reranker import AsyncBgeRerankerV2M3Client
from app.integrations.vector_store import PgVectorStore
from app.services.knowledge_rag_service import KnowledgeRagService
from app.services.project_service import ProjectService
from app.services.query_router_service import QueryRouterService, QuerySource, RouteContext
from app.services.rag_service import RagService
from app.services.rag_stream import sse_event, stream_rag_answer
from app.services.report_field_query import ReportFieldQueryService

logger = logging.getLogger(__name__)

_RETRIEVAL_TIMEOUT_SECONDS = 45
_ANSWER_TIMEOUT_SECONDS = 90
_SMALL_TALK_REPLY = (
    "你好，我是投标参谋助手。\n\n"
    "我可以帮你查询项目招标文件、解读法律知识、查看风险与企业匹配结论，"
    "并给出可追溯的原文依据和下一步行动。"
)
_NO_PROJECT_REPLY = "请先选择要查询的项目；也可以直接在对话请求中传入 project_id。"
_TIMEOUT_REPLY = "本次检索耗时较长，暂未完成。请稍后重试或将问题描述得更具体一些。"
_FAILURE_REPLY = "抱歉，本次回答未能完成。请稍后重试；如果问题涉及项目，请确认已导入对应文件。"
_DEFAULT_SESSION_TITLES = {"新对话", "新会话", "New conversation"}
_SESSION_TITLE_MAX_LENGTH = 48


@dataclass(frozen=True, slots=True)
class ConversationStreamTurn:
    question: str
    actor_id: UUID
    role_codes: set[str]
    session_id: str | None = None
    project_id: UUID | None = None


class ConversationStreamService:
    """Runs the only supported PC/mobile streaming conversation flow."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._projects = ProjectService(session)
        self._messages = MessageRepository(session)

    async def stream(self, turn: ConversationStreamTurn) -> AsyncIterator[bytes]:
        chat_session = self._get_or_create_session(turn)
        session_id = str(chat_session.id)
        question, project_id, waiting_for_project = self._resolve_turn_context(turn, chat_session)
        if project_id is not None:
            # 会话里保存的历史项目也必须逐轮回查成员资格，不能把 session 当授权凭据。
            self._projects.get_visible(project_id, turn.actor_id, turn.role_codes)

        yield sse_event(
            {
                "type": "status",
                "stage": "routing",
                "message": "正在识别问题类型…",
                "session_id": session_id,
            }
        )

        # “我有哪些项目”是会话控制意图，不应交给 RAG/LLM 猜测。
        # 若前一轮正在等待项目选择，保留原问题，用户可直接回复列表中的项目名。
        if self._is_project_list_request(turn.question):
            async for event in self._finish_direct(
                chat_session,
                turn.question,
                self._project_selection_prompt(
                    turn.actor_id, turn.role_codes, waiting_for_project=waiting_for_project
                ),
                [],
                is_fallback=False,
            ):
                yield event
            return

        decision = QueryRouterService(self._settings).route(
            question,
            self._route_context(project_id),
        )

        if waiting_for_project:
            async for event in self._finish_direct(
                chat_session,
                turn.question,
                self._project_selection_prompt(turn.actor_id, turn.role_codes, True),
                [],
                is_fallback=False,
            ):
                yield event
            return

        if decision.source == QuerySource.SMALL_TALK:
            async for event in self._finish_direct(
                chat_session, turn.question, _SMALL_TALK_REPLY, [], is_fallback=False
            ):
                yield event
            return

        if decision.source in {QuerySource.EMPTY, QuerySource.UNCLEAR}:
            answer = decision.followup or "请说明您想查询项目文件、报告结论，还是法律知识。"
            async for event in self._finish_direct(
                chat_session, turn.question, answer, [], is_fallback=False
            ):
                yield event
            return

        if decision.source in {QuerySource.TENDER, QuerySource.REPORT} and project_id is None:
            chat_session.pending_intent = {
                "type": "await_project_selection",
                "original_question": question,
            }
            async for event in self._finish_direct(
                chat_session,
                turn.question,
                self._project_selection_prompt(turn.actor_id, turn.role_codes, True),
                [],
            ):
                yield event
            return

        if decision.source == QuerySource.REPORT:
            yield sse_event(
                {
                    "type": "status",
                    "stage": "retrieval",
                    "message": "正在读取已确认的项目事实与分析结论…",
                }
            )
            answer, citations = self._report_answer(project_id, decision.report_target)
            async for event in self._finish_direct(
                chat_session, turn.question, answer, citations, is_fallback=not citations
            ):
                yield event
            return

        yield sse_event(
            {"type": "status", "stage": "retrieval", "message": "正在检索可引用的原文证据…"}
        )
        async for event in self._stream_rag(
            chat_session=chat_session,
            original_question=turn.question,
            effective_question=decision.question or question,
            source=decision.source,
            project_id=project_id,
            actor_id=turn.actor_id,
            role_codes=turn.role_codes,
        ):
            yield event

    @staticmethod
    def _is_project_list_request(question: str) -> bool:
        normalized = question.replace(" ", "").strip()
        return any(
            phrase in normalized
            for phrase in ("哪些项目", "我的项目", "项目列表", "项目有哪些", "有项目吗")
        )

    def _project_selection_prompt(
        self, actor_id: UUID, role_codes: set[str], *, waiting_for_project: bool
    ) -> str:
        projects = self._projects.list_visible(actor_id, role_codes)
        if not projects:
            return "当前账号还没有可访问的项目。请先在 PC 端创建项目并导入招标文件。"
        lines = ["请直接回复项目名称以继续：" if waiting_for_project else "你当前可访问的项目："]
        lines.extend(
            f"{index}. {project.name}（{project.code}）"
            for index, project in enumerate(projects[:10], start=1)
        )
        if len(projects) > 10:
            lines.append(f"- 其余 {len(projects) - 10} 个项目请在 PC 端查看")
        if not waiting_for_project:
            lines.append("\n可回复项目名称；查询招标要求、风险或报告时，我也会引导你选择项目。")
        return "\n".join(lines)

    def _get_or_create_session(self, turn: ConversationStreamTurn) -> ChatSession:
        if turn.session_id:
            chat_session = self._session.get(ChatSession, turn.session_id)
            if (
                chat_session is None
                or chat_session.deleted_at is not None
                or chat_session.user_id != str(turn.actor_id)
            ):
                raise DomainError("NOT_FOUND", "会话不存在", 404)
            if (
                turn.project_id
                and chat_session.project_id
                and chat_session.project_id != str(turn.project_id)
            ):
                raise DomainError("SESSION_PROJECT_MISMATCH", "会话与指定项目不一致", 400)
        else:
            chat_session = ChatSession(user_id=str(turn.actor_id), title="新对话")
            self._session.add(chat_session)
            self._session.flush()

        if turn.project_id is not None:
            self._projects.get_visible(turn.project_id, turn.actor_id, turn.role_codes)
            chat_session.project_id = str(turn.project_id)
            chat_session.active_project_id = str(turn.project_id)
            chat_session.pending_intent = None
        return chat_session

    def _resolve_turn_context(
        self, turn: ConversationStreamTurn, chat_session: ChatSession
    ) -> tuple[str, UUID | None, bool]:
        project_id = (
            UUID(chat_session.active_project_id) if chat_session.active_project_id else None
        )
        pending = chat_session.pending_intent or {}
        if pending.get("type") != "await_project_selection":
            return turn.question, project_id, False

        matched = self._match_visible_project(turn.question, turn.actor_id, turn.role_codes)
        if matched is None:
            return turn.question, None, True
        chat_session.active_project_id = str(matched)
        chat_session.project_id = str(matched)
        chat_session.pending_intent = None
        return str(pending.get("original_question") or turn.question), matched, False

    def _match_visible_project(
        self, user_text: str, actor_id: UUID, role_codes: set[str]
    ) -> UUID | None:
        needle = user_text.strip()
        try:
            candidate = UUID(needle)
        except ValueError:
            candidate = None
        visible = self._projects.list_visible(actor_id, role_codes)
        if needle.isdecimal():
            position = int(needle)
            if 1 <= position <= len(visible):
                return visible[position - 1].id
        if candidate and any(item.id == candidate for item in visible):
            return candidate
        exact = [item for item in visible if needle in {item.code, item.name}]
        if len(exact) == 1:
            return exact[0].id
        contained = [item for item in visible if needle and needle in item.name]
        return contained[0].id if len(contained) == 1 else None

    def _route_context(self, project_id: UUID | None) -> RouteContext:
        if project_id is None:
            return RouteContext()
        from app.db.models import Document, Report

        has_tender_docs = (
            self._session.query(Document.id)
            .filter(
                Document.project_id == project_id,
                Document.document_type == "TENDER",
                Document.deleted_at.is_(None),
            )
            .first()
            is not None
        )
        has_report = (
            self._session.query(Report.id)
            .filter(
                Report.project_id == project_id,
                Report.status == "READY",
            )
            .first()
            is not None
        )
        return RouteContext(has_report=has_report, has_tender_docs=has_tender_docs)

    async def _stream_rag(
        self,
        *,
        chat_session: ChatSession,
        original_question: str,
        effective_question: str,
        source: QuerySource,
        project_id: UUID | None,
        actor_id: UUID,
        role_codes: set[str],
    ) -> AsyncIterator[bytes]:
        embedding = AsyncBgeM3Client(self._settings)
        reranker = AsyncBgeRerankerV2M3Client(self._settings)
        answer = ""
        citations: list[dict[str, Any]] = []
        is_fallback = False
        try:
            llm = DeepSeekV4FlashClient(self._settings)
            if source == QuerySource.LEGAL:
                service = KnowledgeRagService(
                    self._session,
                    self._settings,
                    embedding,
                    PgVectorStore(self._settings),
                    reranker,
                    llm,
                )
                async with asyncio.timeout(_RETRIEVAL_TIMEOUT_SECONDS):
                    contexts = await service._aprepare_retrieval(
                        actor_id, role_codes, effective_question, None
                    )
            else:
                if project_id is None:
                    raise DomainError("PROJECT_REQUIRED", _NO_PROJECT_REPLY, 400)
                service = RagService(
                    self._session,
                    self._settings,
                    embedding,
                    PgVectorStore(self._settings),
                    reranker,
                    llm,
                )
                async with asyncio.timeout(_RETRIEVAL_TIMEOUT_SECONDS):
                    contexts, _ = await service._aprepare_retrieval(
                        project_id, actor_id, role_codes, effective_question, str(chat_session.id)
                    )

            yield sse_event(
                {"type": "status", "stage": "answering", "message": "正在基于原文生成回答…"}
            )
            async with asyncio.timeout(_ANSWER_TIMEOUT_SECONDS):
                async for event in stream_rag_answer(llm, effective_question, contexts):
                    payload = _decode_sse(event)
                    if payload.get("type") == "delta":
                        answer += str(payload.get("content") or "")
                        yield event
                    elif payload.get("type") == "done":
                        answer = str(payload.get("answer") or answer)
                        citations = list(payload.get("citations") or [])
                        is_fallback = bool(payload.get("no_evidence"))
                    elif payload.get("type") == "error":
                        answer = _FAILURE_REPLY
                        is_fallback = True
                        yield event
                        continue
        except TimeoutError:
            logger.warning("conversation stream timed out source=%s", source)
            answer, citations, is_fallback = _TIMEOUT_REPLY, [], True
            yield sse_event({"type": "error", "code": "ANSWER_TIMEOUT", "message": _TIMEOUT_REPLY})
        except DomainError:
            raise
        except Exception:
            logger.exception("conversation stream failed source=%s", source)
            answer, citations, is_fallback = _FAILURE_REPLY, [], True
            yield sse_event({"type": "error", "code": "ANSWER_FAILED", "message": _FAILURE_REPLY})
        finally:
            await embedding.close()
            await reranker.close()

        if not answer:
            answer, is_fallback = _FAILURE_REPLY, True
        self._persist_turn(chat_session, original_question, answer, citations, is_fallback)
        yield sse_event(
            {
                "type": "done",
                "answer": answer,
                "citations": citations,
                "no_evidence": is_fallback,
                "session_id": str(chat_session.id),
            }
        )

    def _report_answer(
        self, project_id: UUID | None, target: str | None
    ) -> tuple[str, list[dict[str, Any]]]:
        if project_id is None:
            return _NO_PROJECT_REPLY, []
        context = ReportFieldQueryService(self._session).load(project_id)
        citation_ids: list[UUID] = []
        lines: list[str] = []
        if target == "match":
            gaps = context.enterprise_gaps()
            if not gaps:
                return "## 企业材料匹配\n\n当前没有待补齐或待确认的企业材料缺口。", []
            lines.append("## 企业材料匹配\n")
            for item in gaps[:5]:
                requirement = next(
                    (r for r in context.all_requirements if r.id == item.requirement_id), None
                )
                lines.append(
                    f"- **{requirement.title if requirement else '企业材料要求'}**：{item.reason}"
                )
                citation_ids.extend(eid for eid, _ in context.match_evidence.get(item.id, []))
                citation_ids.extend(context.requirement_evidence.get(item.requirement_id, []))
        else:
            risks = context.open_risks()
            if target == "risk":
                lines.append("## 当前风险\n")
                if not risks:
                    lines.append("当前没有待处理的风险。")
                for risk in risks[:5]:
                    lines.append(
                        f"- **[{_severity_label(risk.severity)}] {_risk_title(risk)}**："
                        f"{_risk_summary(risk)}"
                    )
                    citation_ids.extend(context.risk_evidence.get(risk.id, []))
            else:
                lines.append("## 项目分析摘要\n")
                lines.append(f"- 待处理风险：**{len(risks)}** 项")
                lines.append(f"- 企业材料缺口：**{len(context.enterprise_gaps())}** 项")
                actions = context.action_plan_items()
                if actions:
                    lines.append("\n### 建议优先行动\n")
                    for item in actions[:5]:
                        lines.append(f"- **{item.priority}**：{item.action}")
                        citation_ids.extend(item.evidence_ids)
        return "\n".join(lines), self._citations(citation_ids)

    async def _finish_direct(
        self,
        chat_session: ChatSession,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        *,
        is_fallback: bool,
    ) -> AsyncIterator[bytes]:
        self._persist_turn(chat_session, question, answer, citations, is_fallback)
        yield sse_event({"type": "delta", "content": answer})
        yield sse_event(
            {
                "type": "done",
                "answer": answer,
                "citations": citations,
                "no_evidence": is_fallback,
                "session_id": str(chat_session.id),
            }
        )

    def _persist_turn(
        self,
        chat_session: ChatSession,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        is_fallback: bool,
    ) -> None:
        if chat_session.title in _DEFAULT_SESSION_TITLES:
            chat_session.title = _session_title(question)
        self._messages.create_message(str(chat_session.id), "user", question)
        self._messages.create_message(
            str(chat_session.id),
            "assistant",
            answer,
            knowledge_references={"citations": citations} if citations else None,
            is_fallback=is_fallback,
        )
        self._session.commit()

    def _citations(self, evidence_ids: list[UUID]) -> list[dict[str, Any]]:
        evidence_map = EvidenceRepository(self._session).list_by_ids(
            list(dict.fromkeys(evidence_ids))
        )
        citations = []
        for evidence_id in dict.fromkeys(evidence_ids):
            evidence = evidence_map.get(evidence_id)
            if evidence is None:
                continue
            citations.append(
                {
                    "evidence_id": str(evidence.id),
                    "page_number": evidence.page_number,
                    "content": (evidence.quoted_text or "")[:300],
                    "source_reference": evidence.source_reference,
                }
            )
        return citations


def _session_title(question: str) -> str:
    """Use the first user turn as a compact, readable history title."""
    normalized = " ".join(question.split())
    if len(normalized) <= _SESSION_TITLE_MAX_LENGTH:
        return normalized or "新对话"
    return f"{normalized[:_SESSION_TITLE_MAX_LENGTH - 1]}…"


def _decode_sse(event: bytes) -> dict[str, Any]:
    try:
        return json.loads(event.decode("utf-8").removeprefix("data: ").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _severity_label(value: str) -> str:
    return {"CRITICAL": "严重", "HIGH": "高", "MEDIUM": "中", "LOW": "低", "INFO": "提示"}.get(
        value, value
    )


def _risk_summary(risk: Any) -> str:
    """Expose a business-readable risk explanation, never persistence internals."""
    requirement_title = (risk.trigger_data or {}).get("requirement_title")
    if isinstance(requirement_title, str) and requirement_title:
        return f"“{requirement_title}”尚未形成可确认的材料匹配结论。"
    description = str(risk.description or "需核对招标原文与企业材料。")
    return (
        description.replace("Requirement", "资格要求")
        .replace("MatchResult", "企业材料匹配结果")
        .replace("MATCHED/PARTIAL", "满足或部分满足")
    )


def _risk_title(risk: Any) -> str:
    """Keep generated rule IDs and English model names out of user-visible titles."""
    if "Requirement" in risk.title or "MatchResult" in risk.title:
        return "资格要求与企业材料待核验"
    return str(risk.title)
