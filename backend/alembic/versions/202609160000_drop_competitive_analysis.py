"""Drop the retired competitive-analysis feature tables.

Revision ID: 202609160000
Revises: 202609150000
"""

from alembic import op


revision = "202609160000"
down_revision = "202609150000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Children first: this feature has no remaining public API or runtime consumer.
    for table in (
        "competitive_finding_knowledge",
        "competitive_finding_evidences",
        "competitive_analysis_evidences",
        "competitive_findings",
        "challenge_draft_evidences",
        "challenge_drafts",
        "competitive_analyses",
        "quote_scenarios",
        "project_comments",
        "work_items",
        "notifications",
        "market_checks",
        "graph_edges",
        "graph_nodes",
        "integration_runs",
        "integration_connectors",
    ):
        op.drop_table(table, schema="app")


def downgrade() -> None:
    raise NotImplementedError("Retired competitive-analysis data is intentionally not recoverable.")
