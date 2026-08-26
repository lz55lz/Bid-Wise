"""Add tender_req_candidate column to document_nodes for SQL-level filtering.

SQL-level pre-filtering for requirement extraction, reducing DB I/O and memory.

Revision ID: 202608160000
Revises: 202608150000
Create Date: 2026-08-16

"""
from alembic import op
import sqlalchemy as sa


revision = "202608160000"
down_revision = "202608150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_nodes",
        sa.Column("tender_req_candidate", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("document_nodes", "tender_req_candidate")
