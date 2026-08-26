"""Update the fixed reranker identifier to bge-reranker-v2-m3."""

from alembic import op

revision = "202608090000"
down_revision = "202608083200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseekv4', 'bge-reranker-v2-m3', 'bge-m3'))
        """
    )


def downgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseekv4', 'rankv2', 'bge-m3'))
        """
    )
