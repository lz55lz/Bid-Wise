"""add im_channels and im_channel_sessions

IM 集成：企业微信/飞书等平台的机器人渠道配置与会话映射。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202608260000"
down_revision = "202608250000"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "im_channels",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="webhook"),
        sa.Column("credentials", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("agent_id", sa.String(36), nullable=True),
        sa.Column("knowledge_base_id", sa.String(36), nullable=True),
        sa.Column("output_mode", sa.String(32), nullable=False, server_default="full"),
        sa.Column("session_mode", sa.String(32), nullable=False, server_default="user"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="app",
    )
    op.create_index("ix_app_im_channels_platform", "im_channels", ["platform"], schema="app")
    op.create_index("ix_app_im_channels_tenant_id", "im_channels", ["tenant_id"], schema="app")
    op.create_index("ix_app_im_channels_project_id", "im_channels", ["project_id"], schema="app")

    op.create_table(
        "im_channel_sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("channel_id", sa.String(36), nullable=False),
        sa.Column("platform_user_id", sa.String(512), nullable=False),
        sa.Column("chat_id", sa.String(512), nullable=False),
        sa.Column("thread_id", sa.String(512), nullable=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["channel_id"], ["app.im_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["app.sessions.id"], ondelete="CASCADE"),
        schema="app",
    )
    op.create_index("ix_app_im_channel_sessions_channel_id", "im_channel_sessions", ["channel_id"], schema="app")
    op.create_index("ix_app_im_channel_sessions_platform_user_id", "im_channel_sessions", ["platform_user_id"], schema="app")
    op.create_index("ix_app_im_channel_sessions_chat_id", "im_channel_sessions", ["chat_id"], schema="app")
    op.create_index("ix_app_im_channel_sessions_tenant_id", "im_channel_sessions", ["tenant_id"], schema="app")


def downgrade() -> None:
    op.drop_table("im_channel_sessions", schema="app")
    op.drop_table("im_channels", schema="app")
