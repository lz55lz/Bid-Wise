"""IM 消息编排服务。"""

import asyncio
import json
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.db.models.documents import Document, DocumentVersion
from app.db.repositories.document_repository import DocumentRepository
from app.integrations.im.adapters.base import IMAdapter
from app.integrations.im.commands import CommandRegistry, create_default_registry
from app.integrations.im.schemas import IncomingMessage, MessageType, ReplyMessage
from app.services.conversation_stream_service import (
    ConversationStreamService,
    ConversationStreamTurn,
)

logger = logging.getLogger(__name__)


def _decode_sse(raw_event: bytes) -> dict[str, Any]:
    """Decode internal SSE frames without coupling the IM adapter to HTTP."""
    try:
        return json.loads(raw_event.decode("utf-8").removeprefix("data: ").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _build_user_key(channel_id: str, incoming: IncomingMessage) -> str:
    """构建用户限流/会话 key。"""
    parts = [incoming.platform.value, channel_id, incoming.user_id, incoming.chat_id]
    if incoming.thread_id:
        parts.append(incoming.thread_id)
    return ":".join(parts)


class IMService:
    """IM 消息处理中枢。"""

    def __init__(
        self,
        redis_client: Any | None = None,
        max_messages: int = 10,
        window_seconds: int = 60,
        command_registry: CommandRegistry | None = None,
    ) -> None:
        self._redis = redis_client
        self._max_messages = max_messages
        self._window_seconds = window_seconds
        self._command_registry = command_registry or create_default_registry()

        # 内存回退实现
        self._memory_dedup: dict[str, bool] = {}
        self._memory_rates: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # 会话解析
    # ------------------------------------------------------------------
    def _resolve_or_create_session(
        self,
        channel_id: str,
        incoming: IncomingMessage,
        channel_data: dict,
        db,
    ) -> tuple[str | None, str | None]:
        """解析或创建 IM → WeKnora 会话映射，返回 (channel_session_id, chat_session_id)。"""
        from datetime import UTC, datetime

        from sqlalchemy import select

        from app.db.models import IMChannelSession
        from app.db.models import Session as ChatSession

        # 查询现有映射
        stmt = select(IMChannelSession).where(
            IMChannelSession.channel_id == channel_id,
            IMChannelSession.platform_user_id == incoming.user_id,
            IMChannelSession.chat_id == incoming.chat_id,
            IMChannelSession.thread_id == incoming.thread_id,
        )
        mapping = db.execute(stmt).scalar_one_or_none()

        if mapping:
            # 验证关联的 ChatSession 仍有效
            chat_session = db.get(ChatSession, mapping.session_id)
            if chat_session and chat_session.deleted_at is None:
                # 早期版本把外部平台用户 ID 写入 user_id；迁移到渠道所有者，
                # 让统一会话核心能执行真实的项目成员校验。
                if chat_session.user_id != channel_data["owner_user_id"]:
                    chat_session.user_id = channel_data["owner_user_id"]
                if channel_data.get("project_id") and not chat_session.active_project_id:
                    chat_session.project_id = channel_data["project_id"]
                    chat_session.active_project_id = channel_data["project_id"]
                db.flush()
                return mapping.id, mapping.session_id
            # 软删除映射，重新创建
            mapping.deleted_at = datetime.now(UTC)
            db.flush()

        # 创建新 ChatSession
        chat_session = ChatSession(
            project_id=channel_data.get("project_id"),
            # 外部 IM 用户不是本系统账号；会话归渠道所有者所有，映射表保留外部身份。
            user_id=channel_data["owner_user_id"],
            title=f"IM会话 {incoming.chat_id[:8]}",
            active_project_id=channel_data.get("project_id"),
        )
        db.add(chat_session)
        db.flush()

        # 创建 IMChannelSession 映射
        new_mapping = IMChannelSession(
            channel_id=channel_id,
            platform_user_id=incoming.user_id,
            chat_id=incoming.chat_id,
            thread_id=incoming.thread_id,
            session_id=chat_session.id,
        )
        db.add(new_mapping)
        db.flush()
        return new_mapping.id, chat_session.id

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------
    def is_message_processed(self, message_id: str) -> bool:
        """检查消息是否已处理。"""
        if self._redis:
            return bool(self._redis.exists(f"im:dedup:{message_id}"))
        return self._memory_dedup.get(message_id, False)

    def mark_message_processed(self, message_id: str, ttl_seconds: int = 300) -> None:
        """标记消息已处理。"""
        if self._redis:
            self._redis.setex(f"im:dedup:{message_id}", ttl_seconds, "1")
        else:
            self._memory_dedup[message_id] = True

    # ------------------------------------------------------------------
    # 限流
    # ------------------------------------------------------------------
    def check_rate_limit(self, user_key: str) -> bool:
        """检查是否超过限流阈值。"""
        now = time.time()
        if self._redis:
            return self._check_rate_limit_redis(user_key, now)
        return self._check_rate_limit_memory(user_key, now)

    def record_request(self, user_key: str) -> None:
        """记录一次请求。"""
        now = time.time()
        if self._redis:
            self._record_request_redis(user_key, now)
        else:
            self._record_request_memory(user_key, now)

    def _check_rate_limit_memory(self, user_key: str, now: float) -> bool:
        window = self._memory_rates.get(user_key, deque())
        cutoff = now - self._window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        return len(window) < self._max_messages

    def _record_request_memory(self, user_key: str, now: float) -> None:
        window = self._memory_rates.setdefault(user_key, deque())
        window.append(now)

    def _check_rate_limit_redis(self, user_key: str, now: float) -> bool:
        """Redis 滑动窗口限流（使用 ZSET）。"""
        if not self._redis:
            return True
        key = f"im:ratelimit:{user_key}"
        cutoff = now - self._window_seconds
        # 清理过期记录
        self._redis.zremrangebyscore(key, 0, cutoff)
        count = self._redis.zcard(key)
        return count < self._max_messages

    def _record_request_redis(self, user_key: str, now: float) -> None:
        if not self._redis:
            return
        key = f"im:ratelimit:{user_key}"
        self._redis.zadd(key, {str(now): now})
        self._redis.expire(key, self._window_seconds)

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------
    async def handle_message(
        self,
        channel_id: str,
        incoming: IncomingMessage,
        adapter: IMAdapter,
        channel_data: dict | None = None,
        db_session=None,
    ) -> None:
        """处理一条 IM 消息。"""
        # 去重（仅记录，不预标记；若限流失败可重放）
        if self.is_message_processed(incoming.message_id):
            logger.info("[IM] duplicate message skipped: %s", incoming.message_id)
            return

        user_key = _build_user_key(channel_id, incoming)

        # 限流（非命令也走限流，命令校验失败也回复）
        is_command = incoming.content.strip().startswith("/")
        if not is_command:
            if not self.check_rate_limit(user_key):
                await adapter.send_reply(
                    incoming,
                    ReplyMessage(content="请求过于频繁，请稍后再试。"),
                )
                return
            self.record_request(user_key)

        # 统一解析会话（命令和 QA 共享）
        channel_session_id: str | None = None
        chat_session_id: str | None = None
        if channel_data and db_session:
            channel_session_id, chat_session_id = self._resolve_or_create_session(
                channel_data.get("id", ""), incoming, channel_data, db_session
            )

        # 命令分发（捕获异常防止整条链路崩溃）
        try:
            command_result = await self._command_registry.execute(
                incoming,
                incoming.content,
                channel_data=channel_data,
                session_id=chat_session_id,
                db_session=db_session,
            )
        except Exception as exc:
            logger.exception("[IM] command execution failed: %s", exc)
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="命令执行失败，请稍后再试。"),
            )
            return

        # 文件/图片消息：下载并入库到知识库
        if incoming.message_type in (MessageType.FILE, MessageType.IMAGE):
            await self._handle_file_message(incoming, adapter, channel_data, db_session)
            return

        if command_result and command_result.action == "query":
            # 快捷指令只翻译用户意图，仍走 PC/IM 共用的权限、项目选择、
            # 检索和证据链路。
            incoming = incoming.model_copy(update={"content": command_result.content})
        elif command_result:
            await self._handle_command_result(incoming, adapter, command_result, chat_session_id)
            return

        # 限流通过后再标记已处理，避免限流时永久丢弃
        self.mark_message_processed(incoming.message_id)

        # 普通消息：走 QA 流水线
        await self._handle_qa_pipeline(
            incoming, adapter, channel_data, db_session, channel_session_id, chat_session_id
        )

    async def _handle_qa_pipeline(
        self,
        incoming: IncomingMessage,
        adapter: IMAdapter,
        channel_data: dict | None,
        db,
        channel_session_id: str | None,
        chat_session_id: str | None,
    ) -> None:
        """普通消息走与 PC 相同的会话核心；IM 只发送最终回复。"""
        if channel_data is None or db is None:
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="系统暂不可用，请稍后再试。"),
            )
            return

        if not channel_session_id or not chat_session_id:
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="当前渠道未关联会话，无法回答问题。"),
            )
            return

        try:
            settings = get_settings()
            from app.db.repositories.identity_repository import IdentityRepository

            owner_id = UUID(channel_data["owner_user_id"])
            role_codes = IdentityRepository(db).list_role_codes(owner_id)
            answer: str | None = None
            async for raw_event in ConversationStreamService(db, settings).stream(
                ConversationStreamTurn(
                    question=incoming.content,
                    session_id=chat_session_id,
                    project_id=(
                        UUID(channel_data["project_id"]) if channel_data.get("project_id") else None
                    ),
                    actor_id=owner_id,
                    role_codes=role_codes,
                )
            ):
                event = _decode_sse(raw_event)
                if event.get("type") == "done":
                    answer = str(event.get("answer") or "")

            await adapter.send_reply(
                incoming,
                ReplyMessage(content=answer or "抱歉，本次回答未能完成，请稍后重试。"),
            )
        except Exception as exc:
            logger.exception("[IM] QA pipeline error: %s", exc)
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="问答服务暂时不可用，请稍后再试。"),
            )

    # ------------------------------------------------------------------
    # 文件入库
    # ------------------------------------------------------------------

    async def _handle_file_message(
        self,
        incoming: IncomingMessage,
        adapter: IMAdapter,
        channel_data: dict | None,
        db,
    ) -> None:
        """下载 IM 平台文件/图片，上传到项目知识库。"""
        kb_id = channel_data.get("knowledge_base_id") if channel_data else None
        if not kb_id:
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="当前渠道未绑定知识库，无法存储文件。"),
            )
            return

        if not hasattr(adapter, "download_file"):
            await adapter.send_reply(
                incoming,
                ReplyMessage(content="当前平台不支持文件入库。"),
            )
            return

        try:
            # 1. 下载文件（同步方法，放线程池执行）
            from concurrent.futures import ThreadPoolExecutor

            def _download():
                return adapter.download_file(incoming)

            loop = asyncio.get_event_loop()
            resp, filename = await loop.run_in_executor(
                ThreadPoolExecutor(max_workers=4), _download
            )
            file_bytes = resp.content
            file_size = len(file_bytes)

            if file_size == 0:
                await adapter.send_reply(incoming, ReplyMessage(content="文件为空，无法入库。"))
                return

            # 2. 写入临时文件后上传到对象存储
            import tempfile
            from pathlib import Path

            from app.core.config import get_settings
            from app.integrations.object_storage import get_object_storage

            settings = get_settings()
            obj_store = get_object_storage(settings)
            project_id = channel_data.get("project_id", "") or "unknown"
            content_type = resp.headers.get("content-type", "application/octet-stream")

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = Path(tmp.name)

            try:
                import hashlib

                sha256 = hashlib.sha256(file_bytes).hexdigest()
                actor_id = UUID(incoming.user_id) if incoming.user_id else UUID(bytes([0] * 16))
                doc = Document(
                    id=uuid4(),
                    project_id=UUID(project_id) if project_id else None,
                    document_type="IM_FILE",
                    logical_name=filename,
                    created_at=datetime.now(UTC),
                    created_by=actor_id,
                )
                doc_repo = DocumentRepository(db)
                doc_repo.add_document(doc)
                db.flush()

                version = DocumentVersion(
                    id=uuid4(),
                    document_id=doc.id,
                    version_no=1,
                    file_name=filename,
                    file_size=file_size,
                    mime_type=content_type,
                    object_key=f"im_files/{project_id}/{kb_id}/{filename}",
                    sha256=sha256,
                    parse_status="PARSED",
                    created_at=datetime.now(UTC),
                    created_by=actor_id,
                )
                doc_repo.add_version(version)
                db.flush()
                doc.current_version_id = version.id

                object_key = version.object_key
                db.commit()

                # 同步上传放线程池
                def _upload():
                    obj_store.put_file(object_key, tmp_path, content_type)

                await loop.run_in_executor(ThreadPoolExecutor(max_workers=4), _upload)

                logger.info(
                    "[IM] file ingested: name=%s size=%d kb=%s doc=%s",
                    filename,
                    file_size,
                    kb_id,
                    doc.id,
                )

                file_type = "图片" if incoming.message_type == MessageType.IMAGE else "文件"
                await adapter.send_reply(
                    incoming,
                    ReplyMessage(content=f"{file_type}「{filename}」已入库。"),
                )
            finally:
                tmp_path.unlink(missing_ok=True)

        except Exception as exc:
            logger.exception("[IM] file ingestion failed: %s", exc)
            await adapter.send_reply(
                incoming,
                ReplyMessage(content=f"文件入库失败：{exc}，请稍后再试。"),
            )

    async def _handle_command_result(
        self,
        incoming: IncomingMessage,
        adapter: IMAdapter,
        result: Any,
        chat_session_id: str | None = None,
    ) -> None:
        """处理命令结果，同时写入 ChatSession 消息历史。"""
        from app.integrations.im.schemas import CommandResult

        if not isinstance(result, CommandResult):
            return

        if result.action == "reply":
            # 写入用户命令和助手回答到消息历史
            if chat_session_id:
                self._append_messages(chat_session_id, incoming.content, result.content)
            await adapter.send_reply(incoming, ReplyMessage(content=result.content))
        elif result.action == "clear":
            await adapter.send_reply(incoming, ReplyMessage(content=result.content))
        elif result.action == "stop":
            await adapter.send_reply(incoming, ReplyMessage(content=result.content))
        else:
            logger.warning("[IM] unknown command action: %s", result.action)

    def _append_messages(self, session_id: str, user_content: str, assistant_content: str) -> None:
        """追加用户消息和助手回答到 ChatSession。"""
        if not session_id:
            return
        # 懒获取 db_session（来自 handle_message 的上下文）
        # 注意：这里无法直接访问 self._db，需要通过调用链传递
        # 在 handle_message 中已确保 db 可用，此处安全
        from app.db.repositories.session_repository import MessageRepository
        from app.db.session import get_session_factory

        factory = get_session_factory()
        db = factory()
        try:
            repo = MessageRepository(db)
            repo.create_message(session_id=session_id, role="user", content=user_content)
            repo.create_message(session_id=session_id, role="assistant", content=assistant_content)
            db.commit()
        except Exception as exc:
            logger.warning("[IM] failed to append messages: %s", exc)
        finally:
            db.close()
