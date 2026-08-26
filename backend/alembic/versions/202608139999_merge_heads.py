"""Merge two heads: 202608110000 and 202608130000

Revision ID: 202608139999
Revises: 202608110000, 202608130000
Create Date: 2026-08-13

"""
from alembic import op


revision = "202608139999"
down_revision = ("202608110000", "202608130000")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
