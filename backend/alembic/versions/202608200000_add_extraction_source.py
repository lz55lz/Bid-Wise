"""add extraction_source to project_fields and requirements

Revision ID: 202608200000
Revises: 202608190000
Create Date: 2026-08-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "202608200000"
down_revision = "202608190000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_fields",
        sa.Column("extraction_source", sa.String(16), nullable=True, server_default="llm"),
    )
    op.add_column(
        "requirements",
        sa.Column("extraction_source", sa.String(16), nullable=True, server_default="llm"),
    )


def downgrade() -> None:
    op.drop_column("requirements", "extraction_source")
    op.drop_column("project_fields", "extraction_source")
