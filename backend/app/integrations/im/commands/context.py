"""IM 斜杠命令上下文。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.integrations.im.schemas import IncomingMessage

if TYPE_CHECKING:
    from app.integrations.im.commands.registry import CommandRegistry


@dataclass
class CommandContext:
    """命令执行上下文。"""

    incoming: IncomingMessage
    registry: "CommandRegistry"
    channel: dict[str, Any] | None = None
    session_id: str | None = None
    db_session: Session | None = None
    services: dict[str, Any] = field(default_factory=dict)
