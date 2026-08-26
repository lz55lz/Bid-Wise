"""Add the deferred review state used by the prioritized HITL queue."""

from alembic import op

revision = "202609110000"
down_revision = "202609100000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter type app.review_status add value if not exists 'DEFERRED'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed without rebuilding dependent columns.
    pass
