"""Persist the immutable analysis input manifest."""

from alembic import op

revision = "202609060000"
down_revision = "202609050000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table app.analysis_snapshots add column input_manifest jsonb not null default '{}'::jsonb")


def downgrade() -> None:
    op.execute("alter table app.analysis_snapshots drop column input_manifest")
