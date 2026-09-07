# ruff: noqa: E501
"""建立项目风险事实表。

Revision ID: 20260831_0020
Revises: 20260831_0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0020"
down_revision: str | Sequence[str] | None = "20260831_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_risks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("rule_code", sa.String(length=80), nullable=False),
        sa.Column("risk_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("trigger_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("resolution", sa.Text()),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "severity IN ('HIGH', 'MEDIUM', 'LOW')", name="project_risks_severity_check"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'ACCEPTED', 'RESOLVED', 'DISMISSED')",
            name="project_risks_status_check",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "rule_code", "subject"),
    )
    op.create_index("ix_project_risks_project_id", "project_risks", ["project_id"])


def downgrade() -> None:
    op.drop_table("project_risks")
