# ruff: noqa: E501
"""建立材料匹配结果事实表。

Revision ID: 20260831_0019
Revises: 20260831_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0019"
down_revision: str | Sequence[str] | None = "20260831_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_match_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid()),
        sa.Column("rule_status", sa.String(length=16), nullable=False),
        sa.Column("final_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("missing_conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("matched_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overridden_at", sa.DateTime(timezone=True)),
        sa.Column("overridden_by", sa.Uuid()),
        sa.Column("override_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rule_status IN ('MATCHED', 'UNCERTAIN', 'MISSING')",
            name="material_match_results_rule_status_check",
        ),
        sa.CheckConstraint(
            "final_status IN ('MATCHED', 'UNCERTAIN', 'MISSING')",
            name="material_match_results_final_status_check",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["requirement_id"], ["tender_requirements.id"]),
        sa.ForeignKeyConstraint(["material_id"], ["enterprise_materials.id"]),
        sa.ForeignKeyConstraint(["overridden_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "requirement_id"),
    )
    op.create_index(
        "ix_material_match_results_project_id", "material_match_results", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("material_match_results")
