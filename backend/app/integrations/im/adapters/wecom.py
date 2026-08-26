"""企业微信（WeCom）webhook 适配器。"""

import base64
import hashlib
import hmac
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import Request

from app.core.config import get_settings
from app.integrations.im.adapters.base import IMAdapter
from app.integrations.im.schemas import (
    ChatType,
    IncomingMessage,
    MessageType,
    Platform,
    ReplyMessage,
)

# 模块级 Redis 客户端（lazy init）
_redis_client: Any | None = None


def _get_redis_client() -> Any | None:
    """获取模块级 Redis 客户端。"""
    global _redis_client
    if _redis_client is None:
        try:
            from redis import Redis
            from redis.exceptions import RedisError

            settings = get_settings()
            if settings.redis_url:
                _redis_client = Redis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    decode_responses=True,
                )
                _redis_client.ping()
        except (RedisError, Exception):
            _redis_client = False  # type: ignore[assignment]
    return _redis_client if _redis_client else None


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    """PKCS#7 填充。"""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes, block_size: int = 32) -> bytes:
    """PKCS#7 去除填充。"""
    if not data:
        raise ValueError("empty data")
    pad_len = data[-1]
    if pad_len > block_size or pad_len == 0:
        raise ValueError("invalid padding length")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("invalid padding bytes")
    return data[:-pad_len]


def _sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """计算企业微信回调签名。"""
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


class WeComAdapter(IMAdapter):
    """企业微信 webhook 适配器。"""

    platform = Platform.WECOM
    _default_api_base = "https://qyapi.weixin.qq.com"

    def __init__(self, credentials: dict[str, Any]) -> None:
        super().__init__(credentials)
        self.corp_id = credentials.get("corp_id", "")
        self.agent_secret = credentials.get("agent_secret", "")
        self.token = credentials.get("token", "")
        self.encoding_aes_key = credentials.get("encoding_aes_key", "")
        self.corp_agent_id = int(credentials.get("corp_agent_id") or 0)
        self.api_base = (credentials.get("api_base_url") or self._default_api_base).rstrip("/")

        # 解码 AES key：43 字符 base64 + "="补齐
        self.aes_key = base64.b64decode(self.encoding_aes_key + "=")
        if len(self.aes_key) != 32:
            raise ValueError("invalid encoding_aes_key length after decode")

        # access_token 缓存
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # 回调验证
    # ------------------------------------------------------------------
    async def verify_callback(self, request: Request) -> bool:
        """校验企业微信回调签名。"""
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        msg_signature = request.query_params.get("msg_signature", "")
        signature = request.query_params.get("signature", "") or msg_signature

        if request.method == "GET":
            encrypt = request.query_params.get("echostr", "")
        else:
            body = await request.body()
            encrypt = self._extract_encrypt(body)

        if not signature or not timestamp or not nonce:
            return False

        expected = _sha1_signature(self.token, timestamp, nonce, encrypt)
        return hmac.compare_digest(expected, signature)

    def verify_callback_sync(self, request: Request) -> bool:
        """同步版本，供测试使用。"""
        import asyncio
        return asyncio.run(self.verify_callback(request))

    # ------------------------------------------------------------------
    # URL 验证
    # ------------------------------------------------------------------
    async def handle_url_verification(self, request: Request) -> str:
        """处理企业微信 URL 验证，对齐 WeKnora：GET 直接解密 echostr，不验签名。"""
        if request.method != "GET":
            return ""

        echostr = request.query_params.get("echostr", "")
        if not echostr:
            return ""

        try:
            return self._decrypt(echostr)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("[WeCom] handle_url_verification decrypt failed: %s", exc)
            return ""

    def handle_url_verification_sync(self, request: Request) -> str:
        """同步版本，供测试使用。"""
        import asyncio
        return asyncio.run(self.handle_url_verification(request))

    # ------------------------------------------------------------------
    # 消息解析
    # ------------------------------------------------------------------
    async def parse_callback(self, request: Request) -> IncomingMessage | None:
        """解析企业微信回调消息。"""
        if not await self.verify_callback(request):
            return None

        body = await request.body()
        encrypt = self._extract_encrypt(body)
        if not encrypt:
            return None

        decrypted = self._decrypt(encrypt)
        return self._parse_decrypted_xml(decrypted)

    def parse_callback_sync(self, request: Request) -> IncomingMessage | None:
        """同步版本，供测试使用。"""
        import asyncio
        return asyncio.run(self.parse_callback(request))

    # ------------------------------------------------------------------
    # 发送回复
    # ------------------------------------------------------------------
    async def send_reply(self, incoming: IncomingMessage, reply: ReplyMessage) -> None:
        """发送回复消息。"""
        access_token = await self._get_access_token()
        payload = {
            "touser": incoming.user_id,
            "msgtype": "markdown",
            "agentid": self.corp_agent_id,
            "markdown": {"content": reply.content},
        }

        url = f"{self.api_base}/cgi-bin/message/send?access_token={access_token}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            if result.get("errcode", 0) != 0:
                errcode = result.get("errcode")
                errmsg = result.get("errmsg")
                raise RuntimeError(f"wecom api error: {errcode} {errmsg}")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _extract_encrypt(self, body: bytes) -> str:
        """从回调 XML 中提取 Encrypt 字段。"""
        if not body:
            return ""
        try:
            root = ET.fromstring(body)
            encrypt_node = root.find("Encrypt")
            return encrypt_node.text if encrypt_node is not None and encrypt_node.text else ""
        except ET.ParseError:
            return ""

    def _encrypt(self, plaintext: str) -> str:
        """加密明文（测试用）。"""
        random_bytes = uuid.uuid4().bytes
        msg_len = struct.pack(">I", len(plaintext.encode("utf-8")))
        msg_bytes = plaintext.encode("utf-8")
        corp_id_bytes = self.corp_id.encode("utf-8")
        data = random_bytes + msg_len + msg_bytes + corp_id_bytes
        padded = _pkcs7_pad(data, 32)

        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("utf-8")

    def _decrypt(self, encrypted: str) -> str:
        """解密企业微信密文。"""
        import logging
        _logger = logging.getLogger(__name__)

        ciphertext = base64.b64decode(encrypted)
        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        plaintext = _pkcs7_unpad(padded, 32)

        if len(plaintext) < 20:
            raise ValueError("plaintext too short")

        msg_len = struct.unpack(">I", plaintext[16:20])[0]
        msg_bytes = plaintext[20 : 20 + msg_len]
        corp_id_bytes = plaintext[20 + msg_len :]

        if corp_id_bytes.decode("utf-8") != self.corp_id:
            _logger.warning("[WeCom] callback corp_id mismatch")
            raise ValueError("corp_id mismatch")

        return msg_bytes.decode("utf-8")

    def _parse_decrypted_xml(self, xml_text: str) -> IncomingMessage | None:
        """解析解密后的 XML 消息。"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        def _text(tag: str) -> str:
            node = root.find(tag)
            return node.text or "" if node is not None else ""

        msg_type = _text("MsgType")
        from_user = _text("FromUserName")
        msg_id = _text("MsgId")
        chat_id = _text("ChatId")
        chat_type = ChatType.GROUP if chat_id else ChatType.DIRECT

        if msg_type == "text":
            content = _text("Content")
            if chat_type == ChatType.GROUP:
                content = self._strip_at_mention(content)
            return IncomingMessage(
                platform=Platform.WECOM,
                message_type=MessageType.TEXT,
                user_id=from_user,
                chat_id=chat_id,
                chat_type=chat_type,
                content=content.strip(),
                message_id=msg_id,
            )

        if msg_type == "image":
            pic_url = _text("PicUrl")
            media_id = _text("MediaId")
            return IncomingMessage(
                platform=Platform.WECOM,
                message_type=MessageType.IMAGE,
                user_id=from_user,
                chat_id=chat_id,
                chat_type=chat_type,
                content="",
                message_id=msg_id,
                file_key=pic_url or media_id,
                file_name=f"{msg_id or 'image'}.png",
            )

        # 暂不处理其他类型
        return None

    @staticmethod
    def _strip_at_mention(content: str) -> str:
        """剥除群聊中 @机器人 前缀。"""
        import re
        return re.sub(r"^\s*@[^\s]+\s+", "", content).strip()

    async def _get_access_token(self) -> str:
        """获取企业微信 access_token（优先 Redis 缓存，实例内存作回退）。"""
        redis = _get_redis_client()
        cache_key = f"im:wecom:token:{self.corp_id}:{self.corp_agent_id}"

        # 优先从 Redis 读取（多 worker 共享）
        if redis:
            cached = redis.get(cache_key)
            if cached:
                return cached

        # 实例缓存命中
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        url = (
            f"{self.api_base}/cgi-bin/gettoken"
            f"?corpid={self.corp_id}&corpsecret={self.agent_secret}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
            response.raise_for_status()
            result = response.json()
            if result.get("errcode", 0) != 0:
                errcode = result.get("errcode")
                errmsg = result.get("errmsg")
                raise RuntimeError(f"get token error: {errcode} {errmsg}")

            self._access_token = result["access_token"]
            self._token_expires_at = time.time() + result.get("expires_in", 7200)

            # 写入 Redis（比过期时间提前 5 分钟失效，避免临界穿越）
            if redis:
                redis.setex(cache_key, result.get("expires_in", 7200) - 300, self._access_token)

            return self._access_token

    async def get_userinfo_by_code(self, code: str) -> dict:
        """OAuth 回调用 code 换取用户身份。

        Returns:
            {"userid": "...", "openid": "...", "name": "..."} 或只有 openid（非企微成员）
        """
        access_token = await self._get_access_token()
        url = f"{self.api_base}/cgi-bin/auth/getuserinfo"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params={"access_token": access_token, "code": code})
            response.raise_for_status()
            result = response.json()
            if result.get("errcode", 0) != 0:
                raise RuntimeError(f"getuserinfo error: {result.get('errcode')} {result.get('errmsg')}")
            return {
                "userid": result.get("userid"),
                "openid": result.get("openid"),
            }
