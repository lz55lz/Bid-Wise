"""remove obsolete IM LangGraph checkpoint columns.

Revision ID: 202609140000
Revises: 202609130000
"""

import sqlalchemy as sa

from alembic import op

revision = "202609140000"
down_revision = "202609130000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_app_im_channel_sessions_active_project_id",
        table_name="im_channel_sessions",
        schema="app",
    )
    op.drop_column("im_channel_sessions", "pending_intent", schema="app")
    op.drop_column("im_channel_sessions", "active_project_id", schema="app")


def downgrade() -> None:
    op.add_column(
        "im_channel_sessions",
        sa.Column("active_project_id", sa.String(length=36), nullable=True),
        schema="app",
    )
    op.create_index(
        "ix_app_im_channel_sessions_active_project_id",
        "im_channel_sessions",
        ["active_project_id"],
        schema="app",
    )
    op.add_column(
        "im_channel_sessions",
        sa.Column("pending_intent", sa.JSON(), nullable=True),
        schema="app",
    )
