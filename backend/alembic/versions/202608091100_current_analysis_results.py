"""Retain superseded analysis records without letting them affect current decisions."""

import sqlalchemy as sa

from alembic import op

revision = "202608091100"
down_revision = "202608091000"
branch_labels = None
depends_on = None

_SCHEMA = "app"


def upgrade() -> None:
    op.add_column(
        "risks",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=_SCHEMA,
    )
    op.add_column(
        "match_results",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_risks_project_current", "risks", ["project_id", "is_current"], schema=_SCHEMA
    )
    op.create_index(
        "ix_match_results_project_current",
        "match_results",
        ["project_id", "is_current"],
        schema=_SCHEMA,
    )
    op.alter_column("risks", "is_current", server_default=None, schema=_SCHEMA)
    op.alter_column("match_results", "is_current", server_default=None, schema=_SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_match_results_project_current", table_name="match_results", schema=_SCHEMA)
    op.drop_index("ix_risks_project_current", table_name="risks", schema=_SCHEMA)
    op.drop_column("match_results", "is_current", schema=_SCHEMA)
    op.drop_column("risks", "is_current", schema=_SCHEMA)
