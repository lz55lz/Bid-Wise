"""Add enterprises and enterprise_members tables with foreign keys.

This enables multi-enterprise support where:
- EnterpriseMaterial belongs to an Enterprise (not just a User)
- TenderProject can be bound to an Enterprise
- Users can belong to multiple Enterprises via EnterpriseMember

Revision ID: 202608180000
Revises: 202608170000
Create Date: 2026-08-18
"""

import sqlalchemy as sa

from alembic import op

revision = "202608180000"
down_revision = "202608170000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enterprises table
    op.create_table(
        "enterprises",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("credit_code", sa.String(length=18), nullable=True),
        sa.Column("enterprise_type", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["app.users.id"],),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enterprises_created_at", "enterprises", ["created_at"])
    op.create_index("ix_enterprises_deleted_at", "enterprises", ["deleted_at"])

    # Create enterprise_members table
    op.create_table(
        "enterprise_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("enterprise_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["enterprise_id"], ["app.enterprises.id"],),
        sa.ForeignKeyConstraint(["user_id"], ["app.users.id"],),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enterprise_members_enterprise_id", "enterprise_members", ["enterprise_id"])
    op.create_index("ix_enterprise_members_user_id", "enterprise_members", ["user_id"])

    # Add enterprise_id to enterprise_materials table
    op.add_column(
        "enterprise_materials",
        sa.Column("enterprise_id", sa.UUID(), nullable=True),
    )
    op.create_index("ix_enterprise_materials_enterprise_id", "enterprise_materials", ["enterprise_id"])
    op.execute(
        "UPDATE app.enterprise_materials SET enterprise_id = "
        "(SELECT e.id FROM app.enterprises e WHERE e.created_by = app.enterprise_materials.created_by LIMIT 1) "
        "WHERE enterprise_id IS NULL"
    )
    op.alter_column("enterprise_materials", "enterprise_id", nullable=True)

    # Add enterprise_id to tender_projects table
    op.add_column(
        "tender_projects",
        sa.Column("enterprise_id", sa.UUID(), nullable=True),
    )
    op.create_index("ix_tender_projects_enterprise_id", "tender_projects", ["enterprise_id"])


def downgrade() -> None:
    op.drop_index("ix_tender_projects_enterprise_id", "tender_projects")
    op.drop_column("tender_projects", "enterprise_id")

    op.drop_index("ix_enterprise_materials_enterprise_id", "enterprise_materials")
    op.drop_column("enterprise_materials", "enterprise_id")

    op.drop_table("enterprise_members")
    op.drop_index("ix_enterprises_created_at", "enterprises")
    op.drop_index("ix_enterprises_deleted_at", "enterprises")
    op.drop_table("enterprises")
