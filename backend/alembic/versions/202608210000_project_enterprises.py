"""Add project_enterprises join table for multi-enterprise (consortium) bidding.

- Create app.project_enterprises(project_id, enterprise_id, is_lead, ...)
- Backfill from tender_projects.enterprise_id (as lead enterprise)
- Drop tender_projects.enterprise_id (superseded by the join table)

Revision ID: 202608210000
Revises: 202608200000
Create Date: 2026-08-21
"""

import sqlalchemy as sa

from alembic import op

revision = "202608210000"
down_revision = "202608200000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_enterprises",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("enterprise_id", sa.UUID(), nullable=False),
        sa.Column("is_lead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["app.tender_projects.id"]),
        sa.ForeignKeyConstraint(["enterprise_id"], ["app.enterprises.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app.users.id"]),
        sa.PrimaryKeyConstraint("project_id", "enterprise_id"),
    )
    op.create_index(
        "ix_project_enterprises_enterprise_id", "project_enterprises", ["enterprise_id"]
    )

    # 回填:原单企业绑定迁移为联合体主投标人
    op.execute(
        "INSERT INTO app.project_enterprises "
        "(project_id, enterprise_id, is_lead, created_at, created_by) "
        "SELECT p.id, p.enterprise_id, true, p.created_at, p.owner_id "
        "FROM app.tender_projects p WHERE p.enterprise_id IS NOT NULL"
    )

    op.drop_index("ix_tender_projects_enterprise_id", "tender_projects")
    op.drop_column("tender_projects", "enterprise_id")


def downgrade() -> None:
    op.add_column(
        "tender_projects",
        sa.Column("enterprise_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_tender_projects_enterprise_id", "tender_projects", ["enterprise_id"]
    )
    op.execute(
        "UPDATE app.tender_projects p SET enterprise_id = pe.enterprise_id "
        "FROM app.project_enterprises pe "
        "WHERE pe.project_id = p.id AND pe.is_lead = true"
    )
    op.drop_index("ix_project_enterprises_enterprise_id", "project_enterprises")
    op.drop_table("project_enterprises")
