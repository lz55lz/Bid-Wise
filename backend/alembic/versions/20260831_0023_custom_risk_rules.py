# ruff: noqa: E501
"""添加版本化风险规则模板并关联项目风险。

Revision ID: 20260831_0023
Revises: 20260831_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0023"
down_revision: str | Sequence[str] | None = "20260831_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("project_risks_severity_check", "project_risks", type_="check")
    op.create_check_constraint(
        "project_risks_severity_check",
        "project_risks",
        "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')",
    )
    op.create_table(
        "risk_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("risk_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "risk_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["risk_rules.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "version_no"),
    )
    op.create_index("ix_risk_rule_versions_rule_id", "risk_rule_versions", ["rule_id"])
    op.add_column("project_risks", sa.Column("rule_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "project_risks_rule_version_id_fkey",
        "project_risks",
        "risk_rule_versions",
        ["rule_version_id"],
        ["id"],
    )
    op.create_index("ix_project_risks_rule_version_id", "project_risks", ["rule_version_id"])
    op.drop_constraint(
        "project_risks_project_id_rule_code_subject_key",
        "project_risks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_project_risks_rule_version_subject",
        "project_risks",
        ["project_id", "rule_version_id", "subject"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_project_risks_rule_version_subject", "project_risks", type_="unique")
    op.create_unique_constraint(
        "project_risks_project_id_rule_code_subject_key",
        "project_risks",
        ["project_id", "rule_code", "subject"],
    )
    op.drop_index("ix_project_risks_rule_version_id", table_name="project_risks")
    op.drop_constraint("project_risks_rule_version_id_fkey", "project_risks", type_="foreignkey")
    op.drop_column("project_risks", "rule_version_id")
    op.drop_index("ix_risk_rule_versions_rule_id", table_name="risk_rule_versions")
    op.drop_table("risk_rule_versions")
    op.drop_table("risk_rules")
    op.drop_constraint("project_risks_severity_check", "project_risks", type_="check")
    op.create_check_constraint(
        "project_risks_severity_check", "project_risks", "severity IN ('HIGH', 'MEDIUM', 'LOW')"
    )
