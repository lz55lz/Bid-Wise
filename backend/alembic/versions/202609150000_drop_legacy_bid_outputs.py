"""drop legacy bid report and risk outputs.

Revision ID: 202609150000
Revises: 202609140000
"""

from alembic import op

revision = "202609150000"
down_revision = "202609140000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("bid_report")
    op.drop_table("bid_risk")


def downgrade() -> None:
    # These tables held a retired output format; restoring a schema without its
    # historical data would falsely imply recovery, so this migration is final.
    raise NotImplementedError("Legacy bid output tables cannot be restored")
