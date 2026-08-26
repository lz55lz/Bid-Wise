"""Drop the retired bid-readiness multi-agent persistence.

Revision ID: 202609170000
Revises: 202609160000
"""

from alembic import op


revision = "202609170000"
down_revision = "202609160000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "agent_recommendation_evidences",
        "agent_recommendations",
        "agent_run_evidences",
        "agent_run_steps",
        "agent_runs",
    ):
        op.drop_table(table, schema="app")


def downgrade() -> None:
    raise NotImplementedError("Retired multi-agent data is intentionally not recoverable.")
