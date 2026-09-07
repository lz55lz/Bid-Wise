"""新增项目发现项和证据关联。

Revision ID: 20260830_0008
Revises: 20260830_0007
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0008"
down_revision: str | Sequence[str] | None = "20260830_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """AI 或人工提出的候选均需人工复核，并以 Evidence 关联保存依据。"""
    op.create_table(
        "project_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('REQUIREMENT', 'RISK', 'RECOMMENDATION')"),
        sa.CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')"),
        sa.CheckConstraint("status IN ('PENDING_REVIEW', 'CONFIRMED', 'DISMISSED', 'STALE')"),
        sa.CheckConstraint("source IN ('MANUAL', 'AI')"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_findings_project_id", "project_findings", ["project_id"])
    op.create_table(
        "finding_evidences",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["project_findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("finding_id", "evidence_id"),
    )


def downgrade() -> None:
    """先删除关联表，再删除发现项事实表。"""
    op.drop_table("finding_evidences")
    op.drop_index("ix_project_findings_project_id", table_name="project_findings")
    op.drop_table("project_findings")
