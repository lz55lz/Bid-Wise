"""飞书/ Lark 适配器，对齐 WeKnora internal/im/feishu/。

飞书和 Lark 是同一产品在不同云区（open.feishu.cn / open.larksuite.com），
共用同一套 API 实现。Region 决定接入哪个云。

支持两种模式:
  - webhook: HTTP 回调，verify + parse + send_reply
  - websocket: 长连接（通过 larksuite/oapi-sdk-go v3 官方 SDK）
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import Request

from app.core.errors import DomainError
from app.integrations.im.adapters.base import IMAdapter
from app.integrations.im.schemas import (
    ChatType,
    IncomingMessage,
    MessageType,
    Platform,
    ReplyMessage,
)

if TYPE_CHECKING:
    pass


# ── Region ────────────────────────────────────────────────────────────────────

class Region:
    """飞书/ Lark 云区。"""

    def __init__(
        self,
        platform: Platform,
        open_base_url: str,
        label: str,
        thinking_text: str,
        image_fallback_label: str,
    ) -> None:
        self.platform = platform
        self.open_base_url = open_base_url
        self.label = label
        self.thinking_text = thinking_text
        self.image_fallback_label = image_fallback_label


REGION_FEISHU = Region(
    platform=Platform.FEISHU,
    open_base_url="https://open.feishu.cn",
    label="Feishu",
    thinking_text="正在思考...",
    image_fallback_label="图片",
)

REGION_LARK = Region(
    platform=Platform.LARK,
    open_base_url="https://open.larksuite.com",
    label="Lark",
    thinking_text="Thinking...",
    image_fallback_label="Image",
)


# ── 常量 ──────────────────────────────────────────────────────────────────────

_AES_BLOCK_SIZE = 16
_STREAM_ELEMENT_ID = "streaming_content"
_MAX_IMAGE_BYTES = 10 << 20  # 10MB

_SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")

# 回复接口不支持的错误码 → 降级到普通发消息 API
_FALLBACK_ERROR_CODES = {230019, 230054, 230071}

# 流式卡片状态（内存缓存，生产环境建议 Redis）
_streams: dict[str, dict[str, Any]] = {}


# ── 适配器 ────────────────────────────────────────────────────────────────────

class FeishuAdapter(IMAdapter):
    """飞书/ Lark 适配器，支持 webhook 模式。"""

    # 实现 IMAdapter 的类属性
    platform: Platform = Platform.FEISHU

    def __init__(
        self,
        credentials: dict[str, Any],
        region: Region = REGION_FEISHU,
    ) -> None:
        super().__init__(credentials)
        self._region = region
        self._app_id = credentials.get("app_id", "")
        self._app_secret = credentials.get("app_secret", "")
        self._verification_token = credentials.get("verification_token", "")
        self._encrypt_key = credentials.get("encrypt_key", "")

        # access_token 缓存
        self._token: str = ""
        self._token_expires_at: float = 0

    # ── IMAdapter 实现 ───────────────────────────────────────────────────────

    @property
    def platform_value(self) -> str:
        return self._region.platform.value

    async def verify_callback(self, request: Request) -> bool:
        """校验飞书事件回调的 verification token。"""
        if not self._verification_token:
            return True

        try:
            body = await request.body()
        except Exception:
            return False

        raw = self._decrypt_body(body)
        try:
            parsed = json.loads(raw)
        except Exception:
            return False

        token = parsed.get("header", {}).get("token", "")
        return token == self._verification_token

    async def parse_callback(self, request: Request) -> IncomingMessage | None:
        """解析飞书事件回调为统一 IncomingMessage。"""
        try:
            body = await request.body()
        except Exception:
            return None

        raw = self._decrypt_body(body)
        try:
            event_body = json.loads(raw)
        except Exception:
            return None

        header = event_body.get("header", {})
        if header.get("event_type") != "im.message.receive_v1":
            return None

        event = event_body.get("event", {})
        msg = event.get("message", {})
        if not msg:
            return None

        message_id = msg.get("message_id", "")
        msg_type = msg.get("message_type", "")
        chat_type_str = msg.get("chat_type", "")
        chat_id = msg.get("chat_id", "")
        content_str = msg.get("content", "")

        chat_type = ChatType.GROUP if chat_type_str == "group" else ChatType.DIRECT

        # 发送者 open_id
        open_id = ""
        sender = event.get("sender", {})
        sender_id = sender.get("sender_id", {})
        if sender_id:
            open_id = sender_id.get("open_id", "")

        thread_id = msg.get("root_id", "") or message_id

        if msg_type == "text":
            text_content = self._parse_json(content_str, {"text": ""})
            content = text_content.get("text", "")
            if chat_type == ChatType.GROUP:
                content = self._strip_at_mention(content)
            return IncomingMessage(
                platform=self._region.platform,
                message_type=MessageType.TEXT,
                user_id=open_id,
                chat_id=chat_id,
                chat_type=chat_type,
                content=content.strip(),
                message_id=message_id,
                thread_id=thread_id,
                raw_payload=event_body,
            )

        if msg_type == "file":
            file_content = self._parse_json(content_str, {"file_key": "", "file_name": ""})
            file_key = file_content.get("file_key", "")
            if not file_key:
                return None
            return IncomingMessage(
                platform=self._region.platform,
                message_type=MessageType.FILE,
                user_id=open_id,
                chat_id=chat_id,
                chat_type=chat_type,
                message_id=message_id,
                thread_id=thread_id,
                file_key=file_key,
                file_name=file_content.get("file_name", ""),
                raw_payload=event_body,
            )

        if msg_type == "image":
            image_content = self._parse_json(content_str, {"image_key": ""})
            image_key = image_content.get("image_key", "")
            if not image_key:
                return None
            return IncomingMessage(
                platform=self._region.platform,
                message_type=MessageType.IMAGE,
                user_id=open_id,
                chat_id=chat_id,
                chat_type=chat_type,
                message_id=message_id,
                thread_id=thread_id,
                file_key=image_key,
                file_name=image_key + ".png",
                raw_payload=event_body,
            )

        if msg_type == "post":
            content = self._extract_post_text(content_str)
            if chat_type == ChatType.GROUP:
                content = self._strip_at_mention(content)
            content = content.strip()
            if not content:
                return None
            return IncomingMessage(
                platform=self._region.platform,
                message_type=MessageType.TEXT,
                user_id=open_id,
                chat_id=chat_id,
                chat_type=chat_type,
                content=content,
                message_id=message_id,
                thread_id=thread_id,
                raw_payload=event_body,
            )

        return None

    async def send_reply(self, incoming: IncomingMessage, reply: ReplyMessage) -> None:
        """通过飞书 Open Platform API 发送文本回复。"""
        token = self._get_tenant_access_token()
        if not token:
            raise DomainError("FEISHU_TOKEN_ERROR", "获取飞书 access_token 失败", 500)

        content = json.dumps({"text": reply.content}, ensure_ascii=False)

        # 优先：回复接口（在原消息/thread 下）
        if incoming.message_id and self._safe_path_param(incoming.message_id):
            ok, code = self._send_reply_message(token, incoming.message_id, content)
            if ok:
                return
            if code not in _FALLBACK_ERROR_CODES:
                raise DomainError("FEISHU_SEND_ERROR", f"飞书回复失败: code={code}", 500)

        # 降级：普通发消息 API
        receive_id_type, receive_id = self._resolve_receive_id(incoming)
        ok, code = self._send_message(token, receive_id_type, receive_id, content)
        if not ok:
            raise DomainError("FEISHU_SEND_ERROR", f"飞书发消息失败: code={code}", 500)

    async def handle_url_verification(self, request: Request) -> str:
        """处理 URL 验证 challenge。"""
        try:
            body = await request.body()
        except Exception:
            return ""

        raw = self._decrypt_body(body)
        try:
            payload = json.loads(raw)
        except Exception:
            try:
                payload = json.loads(body)
            except Exception:
                return ""

        challenge = payload.get("challenge", "")
        return challenge if isinstance(challenge, str) else ""

    # ── 流式卡片 ─────────────────────────────────────────────────────────────

    async def start_stream(self, incoming: IncomingMessage) -> str:
        """创建飞书互动卡片，返回 card_id。"""
        token = self._get_tenant_access_token()
        if not token:
            raise DomainError("FEISHU_TOKEN_ERROR", "获取飞书 access_token 失败", 500)

        card_json = self._build_streaming_card_json()
        card_id = self._cardkit_create(token, card_json)
        if not card_id:
            raise DomainError("FEISHU_CARD_ERROR", "创建飞书卡片失败", 500)

        self._send_card_message(token, incoming, card_id)

        _streams[card_id] = {
            "seq": 0,
            "created_at": time.time(),
            "first_chunk": False,
        }

        return card_id

    async def update_stream_content(
        self,
        incoming: IncomingMessage,
        stream_id: str,
        full_content: str,
    ) -> None:
        """更新卡片元素内容（传入完整内容）。"""
        if not full_content or stream_id not in _streams:
            return

        token = self._get_tenant_access_token()
        if not token:
            return

        state = _streams[stream_id]
        state["first_chunk"] = True
        state["seq"] += 1

        content = self._resolve_markdown_images(token, full_content)
        self._cardkit_update_element(token, stream_id, _STREAM_ELEMENT_ID, content, state["seq"])

    async def finalize_stream(
        self,
        incoming: IncomingMessage,
        stream_id: str,
        final_content: str,
    ) -> None:
        """用最终内容替换流式卡片。"""
        await self.update_stream_content(incoming, stream_id, final_content)

    async def end_stream(self, incoming: IncomingMessage, stream_id: str) -> None:
        """关闭流式模式，清理状态。"""
        if stream_id not in _streams:
            return

        token = self._get_tenant_access_token()
        if token:
            state = _streams[stream_id]
            self._cardkit_set_streaming(token, stream_id, False, state["seq"])

        del _streams[stream_id]

    # ── 文件下载 ─────────────────────────────────────────────────────────────

    def download_file(self, msg: IncomingMessage) -> tuple[httpx.Response, str]:
        """通过 GetMessageResource API 下载文件/图片。"""
        if not msg.file_key or not msg.message_id:
            raise DomainError("FEISHU_DOWNLOAD_ERROR", "file_key 和 message_id 必填", 400)

        if not self._safe_path_param(msg.message_id) or not self._safe_path_param(msg.file_key):
            raise DomainError("FEISHU_DOWNLOAD_ERROR", "无效的 message_id 或 file_key", 400)

        token = self._get_tenant_access_token()
        if not token:
            raise DomainError("FEISHU_TOKEN_ERROR", "获取飞书 access_token 失败", 500)

        resource_type = "image" if msg.message_type == MessageType.IMAGE else "file"
        url = (
            f"{self._region.open_base_url}/open-apis/im/v1/"
            f"messages/{msg.message_id}/resources/{msg.file_key}?type={resource_type}"
        )

        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if resp.status_code != 200:
            raise DomainError("FEISHU_DOWNLOAD_ERROR", f"下载失败: status={resp.status_code}", 500)

        filename = msg.file_name or msg.file_key
        return resp, filename

    # ── 内部工具 ─────────────────────────────────────────────────────────────

    def _decrypt_body(self, body: bytes) -> bytes:
        """AES-256-CBC 解密：key = SHA256(encrypt_key)，iv = 密文前16字节。"""
        if not self._encrypt_key:
            return body

        try:
            encrypted_body = json.loads(body)
        except Exception:
            return body

        encrypted = encrypted_body.get("encrypt", "")
        if not encrypted:
            return body

        try:
            ciphertext = base64.b64decode(encrypted)
        except Exception:
            return body

        if len(ciphertext) < _AES_BLOCK_SIZE:
            return body

        key = hashlib.sha256(self._encrypt_key.encode()).digest()
        iv = ciphertext[:_AES_BLOCK_SIZE]
        ct = ciphertext[_AES_BLOCK_SIZE:]

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ct) + decryptor.finalize()

        pad_len = plaintext[-1]
        if pad_len > _AES_BLOCK_SIZE or pad_len == 0 or pad_len > len(plaintext):
            return body
        plaintext = plaintext[:-pad_len]

        return plaintext

    def _get_tenant_access_token(self) -> str | None:
        """tenant_access_token 缓存（2小时，5分钟安全边际）。"""
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token

        url = f"{self._region.open_base_url}/open-apis/auth/v3/tenant_access_token/internal"
        resp = httpx.post(
            url,
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 0:
            return None

        self._token = data.get("tenant_access_token", "")
        expire = data.get("expire", 7200)
        self._token_expires_at = now + expire - 300
        return self._token

    def _send_reply_message(self, token: str, message_id: str, content: str) -> tuple[bool, int]:
        """POST /im/v1/messages/:message_id/reply。"""
        url = (
            f"{self._region.open_base_url}/open-apis/im/v1/"
            f"messages/{message_id}/reply"
        )
        payload = {"msg_type": "text", "content": content}
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()
        return data.get("code") == 0, data.get("code", -1)

    def _send_message(
        self,
        token: str,
        receive_id_type: str,
        receive_id: str,
        content: str,
    ) -> tuple[bool, int]:
        """POST /im/v1/messages。"""
        url = (
            f"{self._region.open_base_url}/open-apis/im/v1/messages"
            f"?receive_id_type={receive_id_type}"
        )
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": content,
        }
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = resp.json()
        return data.get("code") == 0, data.get("code", -1)

    def _resolve_receive_id(self, incoming: IncomingMessage) -> tuple[str, str]:
        """群聊用 chat_id，单聊用 open_id。"""
        if incoming.chat_type == ChatType.GROUP and incoming.chat_id:
            return "chat_id", incoming.chat_id
        return "open_id", incoming.user_id

    @staticmethod
    def _safe_path_param(s: str) -> bool:
        """防路径遍历：只允许字母数字和 - _。"""
        return bool(s) and bool(_SAFE_PATH_RE.match(s))

    @staticmethod
    def _parse_json(content: str, defaults: dict[str, Any]) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
            return defaults | parsed if isinstance(parsed, dict) else defaults
        except Exception:
            return defaults

    @staticmethod
    def _strip_at_mention(content: str) -> str:
        while content.startswith("@_user_"):
            idx = content.find(" ")
            if idx >= 0:
                content = content[idx + 1 :]
            else:
                break
        return content

    @staticmethod
    def _extract_post_text(content: str) -> str:
        try:
            post = json.loads(content)
        except Exception:
            return ""
        parts: list[str] = []
        if title := post.get("title", ""):
            parts.append(title)
        for line in post.get("content", []):
            for elem in line:
                tag = elem.get("tag", "")
                if tag in ("text", "a"):
                    parts.append(elem.get("text", ""))
        return "\n".join(parts)

    # ── CardKit ──────────────────────────────────────────────────────────────

    def _build_streaming_card_json(self) -> str:
        card = {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "summary": {"content": self._region.thinking_text},
            },
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "WeKnora"},
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "💭 " + self._region.thinking_text,
                        "text_size": "normal",
                        "element_id": _STREAM_ELEMENT_ID,
                    }
                ]
            },
        }
        return json.dumps(card, ensure_ascii=False)

    def _cardkit_create(self, token: str, card_json: str) -> str | None:
        url = f"{self._region.open_base_url}/open-apis/cardkit/v1/cards"
        payload = {"type": "card_json", "data": card_json}
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 0:
            return None
        card_id = data.get("data", {}).get("card_id", "")
        return card_id or None

    def _send_card_message(
        self,
        token: str,
        incoming: IncomingMessage,
        card_id: str,
    ) -> None:
        content = json.dumps(
            {"type": "card", "data": {"card_id": card_id}},
            ensure_ascii=False,
        )
        receive_id_type, receive_id = self._resolve_receive_id(incoming)

        if incoming.message_id and self._safe_path_param(incoming.message_id):
            ok, _ = self._send_reply_message(token, incoming.message_id, content)
            if ok:
                return

        self._send_message(token, receive_id_type, receive_id, content)

    def _cardkit_update_element(
        self,
        token: str,
        card_id: str,
        element_id: str,
        content: str,
        seq: int,
    ) -> None:
        url = (
            f"{self._region.open_base_url}/open-apis/cardkit/v1/cards/"
            f"{card_id}/elements/{element_id}/content"
        )
        payload = {"content": content, "sequence": seq}
        httpx.put(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

    def _cardkit_set_streaming(
        self,
        token: str,
        card_id: str,
        streaming: bool,
        seq: int,
    ) -> None:
        url = (
            f"{self._region.open_base_url}/open-apis/cardkit/v1/cards/"
            f"{card_id}/settings"
        )
        settings = json.dumps({"streaming_mode": streaming})
        payload = {"settings": settings, "sequence": seq}
        httpx.patch(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )

    def _resolve_markdown_images(self, token: str, content: str) -> str:
        """把 markdown 图片 URL 替换为飞书 image_key，失败降级为链接。"""
        if "![" not in content:
            return content

        def replacer(match: re.Match) -> str:
            alt = match.group(1)
            raw_url = match.group(2)
            image_key = self._upload_image_from_url(token, raw_url)
            if not image_key:
                label = alt or self._region.image_fallback_label
                return f"[{label}]({raw_url})"
            return f"![{alt}]({image_key})"

        return _IMAGE_LINK_RE.sub(replacer, content)

    def _upload_image_from_url(self, token: str, raw_url: str) -> str | None:
        """下载图片并上传到飞书，返回 image_key。"""
        try:
            img_resp = httpx.get(raw_url, timeout=15, follow_redirects=True)
            if img_resp.status_code != 200:
                return None
            img_data = img_resp.content
            if len(img_data) > _MAX_IMAGE_BYTES:
                return None
        except Exception:
            return None

        import multipart

        buf = io.BytesIO()
        with multipart.MultipartWriter("form-data") as writer:
            writer.field("image_type", "message")
            part = writer.file("image", "image", "image/png", img_data)
            buf = io.BytesIO()
            for chunk in part.streaming_content():
                buf.write(chunk)
        buf.seek(0)

        url = f"{self._region.open_base_url}/open-apis/im/v1/images"
        resp = httpx.post(
            url,
            content=buf.read(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": writer.content_type,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("code") != 0:
            return None
        return data.get("data", {}).get("image_key")
