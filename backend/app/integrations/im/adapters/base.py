"""IM 适配器抽象基类。"""

from abc import ABC, abstractmethod
from typing import Any

from fastapi import Request

from app.integrations.im.schemas import IncomingMessage, ReplyMessage


class IMAdapter(ABC):
    """IM 平台适配器抽象基类。

    参考 WeKnora internal/im/adapter.go 设计，把平台差异收敛到四个方法。
    """

    def __init__(self, credentials: dict[str, Any]) -> None:
        self.credentials = credentials

    @property
    @abstractmethod
    def platform(self) -> str:
        """返回平台标识，如 telegram/wecom。"""
        ...

    @abstractmethod
    async def verify_callback(self, request: Request) -> bool:
        """校验回调请求的签名/Token。"""
        ...

    @abstractmethod
    async def parse_callback(self, request: Request) -> IncomingMessage | None:
        """把平台原始回调解析为统一的 IncomingMessage（非消息事件返回 None）。"""
        ...

    @abstractmethod
    async def send_reply(self, incoming: IncomingMessage, reply: ReplyMessage) -> None:
        """把回复发回 IM 平台。"""
        ...

    async def handle_url_verification(self, request: Request) -> str:
        """处理平台的 URL 验证挑战；返回解密后的挑战字串，失败返回空字符串。"""
        return ""
