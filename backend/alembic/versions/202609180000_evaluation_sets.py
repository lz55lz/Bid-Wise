"""Add editable RAG evaluation sets."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "202609180000"
down_revision = "202609170000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("evaluation_sets", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("name", sa.String(128), nullable=False), sa.Column("description", sa.Text()), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app.users.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema="app")
    op.create_table("evaluation_cases", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("set_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app.evaluation_sets.id", ondelete="CASCADE"), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("scope", sa.String(32), nullable=False), sa.Column("expected_evidence", postgresql.JSONB(), nullable=False), sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"), schema="app")
    op.create_index("ix_app_evaluation_cases_set_id", "evaluation_cases", ["set_id"], schema="app")


def downgrade() -> None:
    op.drop_table("evaluation_cases", schema="app")
    op.drop_table("evaluation_sets", schema="app")
