# ruff: noqa: E501
"""建立项目字段和招标需求事实表。

Revision ID: 20260831_0018
Revises: 20260831_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0018"
down_revision: str | Sequence[str] | None = "20260831_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("field_code", sa.String(length=80), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("primary_evidence_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Uuid()),
        sa.Column("review_note", sa.Text()),
        sa.Column("extraction_source", sa.String(length=16), nullable=False, server_default="LLM"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "review_status IN ('PENDING', 'CONFIRMED', 'REJECTED')",
            name="project_fields_review_check",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["primary_evidence_id"], ["evidences.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_fields_project_id", "project_fields", ["project_id"])
    op.create_index("ix_project_fields_field_code", "project_fields", ["field_code"])
    op.create_table(
        "tender_requirements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Numeric(10, 2)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("primary_evidence_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Uuid()),
        sa.Column("review_note", sa.Text()),
        sa.Column("extraction_source", sa.String(length=16), nullable=False, server_default="LLM"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "category IN ('PROJECT', 'QUALIFICATION', 'BUSINESS', 'SCORING')",
            name="tender_requirements_category_check",
        ),
        sa.CheckConstraint(
            "review_status IN ('PENDING', 'CONFIRMED', 'REJECTED')",
            name="tender_requirements_review_check",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["primary_evidence_id"], ["evidences.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tender_requirements_project_id", "tender_requirements", ["project_id"])
    op.create_index("ix_tender_requirements_category", "tender_requirements", ["category"])
    op.create_index("ix_tender_requirements_deleted_at", "tender_requirements", ["deleted_at"])


def downgrade() -> None:
    op.drop_table("tender_requirements")
    op.drop_table("project_fields")
