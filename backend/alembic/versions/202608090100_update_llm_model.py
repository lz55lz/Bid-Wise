"""Update the fixed LLM identifier to deepseek-v4-flash."""

from alembic import op

revision = "202608090100"
down_revision = "202608090000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseek-v4-flash', 'bge-reranker-v2-m3', 'bge-m3'))
        """
    )


def downgrade() -> None:
    op.execute("alter table app.ai_runs drop constraint ck_ai_runs_model_id")
    op.execute(
        """
        alter table app.ai_runs add constraint ck_ai_runs_model_id
        check (model_id in ('deepseekv4', 'bge-reranker-v2-m3', 'bge-m3'))
        """
    )
