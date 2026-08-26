"""Add active_project_id and pending_intent to sessions (unified session state)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202608310000"
down_revision = "202608300000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("active_project_id", sa.String(36), nullable=True),
        schema="app",
    )
    op.create_index(
        "ix_app_sessions_active_project_id",
        "sessions",
        ["active_project_id"],
        schema="app",
    )
    op.add_column(
        "sessions",
        sa.Column(
            "pending_intent",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("sessions", "pending_intent", schema="app")
    op.drop_index(
        "ix_app_sessions_active_project_id",
        table_name="sessions",
        schema="app",
    )
    op.drop_column("sessions", "active_project_id", schema="app")
