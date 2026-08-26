"""Add sessions and messages tables for multi-turn chat history.

Port from WeKnora internal/types/session.go + message.go.

Revision ID: 202608150000
Revises: 202608140000
Create Date: 2026-08-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "202608150000"
down_revision = "202608140000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sessions 表
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False, server_default="新对话"),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("user_id", sa.String(512), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # messages 表
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("knowledge_references", JSONB(), nullable=True),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_session_id", "messages")
    op.drop_table("messages")
    op.drop_index("ix_sessions_user_id", "sessions")
    op.drop_index("ix_sessions_project_id", "sessions")
    op.drop_table("sessions")
