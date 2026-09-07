"""收敛 Tender Pipeline 阶段名称并合并候选筛选实现细节。

Revision ID: 20260903_0043
Revises: 20260903_0042
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0043"
down_revision: str | Sequence[str] | None = "20260903_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 所有历史 run 都同时创建 annotate/tagging；保留更接近最终候选结果的 tagging 行，
    # 删除只记录中间分类实现细节的 annotate 行。
    op.execute("DELETE FROM tender_pipeline_stages WHERE stage_name = 'annotate'")
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'select_candidates' "
        "WHERE stage_name = 'tagging'"
    )
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'preflight' WHERE stage_name = 'prepare'"
    )
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'finalize' "
        "WHERE stage_name = 'persist_complete'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'prepare' WHERE stage_name = 'preflight'"
    )
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'tagging' "
        "WHERE stage_name = 'select_candidates'"
    )
    op.execute(
        "UPDATE tender_pipeline_stages SET stage_name = 'persist_complete' "
        "WHERE stage_name = 'finalize'"
    )
    # 旧代码只要求 annotate 行存在用于阶段展示；恢复成 PENDING 即可，不伪造历史摘要。
    op.execute(
        """
        INSERT INTO tender_pipeline_stages (
            id, pipeline_run_id, stage_name, status
        )
        SELECT gen_random_uuid(), runs.id, 'annotate', 'PENDING'
        FROM tender_pipeline_runs AS runs
        WHERE NOT EXISTS (
            SELECT 1
            FROM tender_pipeline_stages AS stages
            WHERE stages.pipeline_run_id = runs.id
              AND stages.stage_name = 'annotate'
        )
        """
    )
