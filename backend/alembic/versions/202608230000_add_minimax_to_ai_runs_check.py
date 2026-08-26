"""Add MiniMax-M3 to ai_runs model_id check constraint."""

from alembic import op

revision = "202608230000"
down_revision = "202608220100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseek-v4-flash', 'MiniMax-M3', 'bge-reranker-v2-m3', 'bge-m3'))
        """
    )


def downgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseek-v4-flash', 'bge-reranker-v2-m3', 'bge-m3'))
        """
    )
