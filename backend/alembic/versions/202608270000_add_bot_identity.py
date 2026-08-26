"""add bot_identity to im_channels.

Revision ID: 202608270000
Revises: 202608260000_add_im_channels_and_sessions
Create Date: 2026-08-27
"""

from alembic import op

revision = "202608270000"
down_revision = "202608260000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE app.im_channels
        ADD COLUMN bot_identity VARCHAR(255) NOT NULL DEFAULT '';
    """)
    # 创建去重索引（bot_identity 为空字符串时不触发唯一约束）
    op.create_index(
        "ix_im_channels_bot_identity",
        "app.im_channels",
        ["bot_identity"],
        unique=False,
        postgresql_where=op.inline_literal("bot_identity != ''"),
    )


def downgrade() -> None:
    op.drop_index("ix_im_channels_bot_identity", table_name="app.im_channels")
    op.execute("ALTER TABLE app.im_channels DROP COLUMN bot_identity")
