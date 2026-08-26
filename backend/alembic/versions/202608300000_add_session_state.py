"""Add active_project_id and pending_intent to im_channel_sessions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202608300000"
down_revision = "202608290000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "im_channel_sessions",
        sa.Column("active_project_id", sa.String(36), nullable=True),
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
        sa.Column(
            "pending_intent",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("im_channel_sessions", "pending_intent", schema="app")
    op.drop_index(
        "ix_app_im_channel_sessions_active_project_id",
        table_name="im_channel_sessions",
        schema="app",
    )
    op.drop_column("im_channel_sessions", "active_project_id", schema="app")
