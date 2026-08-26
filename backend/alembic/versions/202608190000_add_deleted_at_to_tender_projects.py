"""add deleted_at to tender_projects

Revision ID: 202608190000
Revises: 202608180000_enterprise_tables
Create Date: 2026-08-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "202608190000"
down_revision = "202608180000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tender_projects",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tender_projects", "deleted_at")
