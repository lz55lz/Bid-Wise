"""收紧 bid-pipeline 恢复状态并记录项目事实来源血缘。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0041"
down_revision: str | Sequence[str] | None = "20260902_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "tender_pipeline_runs_status_check", "tender_pipeline_runs", type_="check"
    )
    op.create_check_constraint(
        "tender_pipeline_runs_status_check",
        "tender_pipeline_runs",
        "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'RESUME_QUEUED', "
        "'SUCCEEDED', 'FAILED', 'CANCELLED')",
    )
    op.add_column(
        "tender_pipeline_runs",
        sa.Column("downstream_analysis_run_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tender_pipeline_runs_downstream_analysis_run_id",
        "tender_pipeline_runs",
        "project_analysis_runs",
        ["downstream_analysis_run_id"],
        ["id"],
    )
    op.create_index(
        "ix_tender_pipeline_runs_downstream_analysis_run_id",
        "tender_pipeline_runs",
        ["downstream_analysis_run_id"],
    )
    # 旧版本没有“同一文档版本只能有一个活动 Pipeline”的数据库约束。历史环境若
    # 已经出现并发重复运行，直接创建唯一索引会使迁移失败。保留最新一条活动运行，
    # 其余明确终止为 FAILED，既让迁移可重复执行，也保留审计记录。
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY document_version_id
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM tender_pipeline_runs
                WHERE status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW')
            )
            UPDATE tender_pipeline_runs AS runs
            SET status = 'FAILED',
                error_code = COALESCE(runs.error_code, 'MIGRATION_DUPLICATE_ACTIVE_RUN'),
                error_message = COALESCE(
                    runs.error_message,
                    '历史重复活动运行已在唯一约束迁移时终止'
                ),
                completed_at = COALESCE(runs.completed_at, now())
            FROM ranked
            WHERE runs.id = ranked.id AND ranked.rn > 1
            """
        )
    )
    op.create_index(
        "ux_tender_pipeline_runs_active_document_version",
        "tender_pipeline_runs",
        ["document_version_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'RESUME_QUEUED')"
        ),
    )
    # 新版本把人工确认后的 Matching/Risk/Report 作为独立 ARQ 下游阶段展示。
    # 历史 PipelineRun 也必须补一条 stage，否则升级旧数据库后 reconciler 的 INNER
    # JOIN 永远看不到这些运行。使用 NOT EXISTS 保证迁移在修复/重跑场景下仍幂等。
    op.execute(
        sa.text(
            """
            INSERT INTO tender_pipeline_stages (
                id, pipeline_run_id, stage_name, status, input_summary, output_summary,
                error_message, started_at, completed_at
            )
            SELECT gen_random_uuid(), runs.id, 'downstream_analysis',
                   CASE
                     WHEN runs.status = 'CANCELLED' THEN 'SKIPPED'
                     ELSE 'PENDING'
                   END,
                   NULL,
                   CASE
                     WHEN runs.status = 'CANCELLED' THEN '{"reason":"review_rejected"}'::jsonb
                     ELSE NULL
                   END,
                   NULL, NULL,
                   CASE WHEN runs.status = 'CANCELLED' THEN runs.completed_at ELSE NULL END
            FROM tender_pipeline_runs AS runs
            WHERE NOT EXISTS (
                SELECT 1
                FROM tender_pipeline_stages AS stages
                WHERE stages.pipeline_run_id = runs.id
                  AND stages.stage_name = 'downstream_analysis'
            )
            """
        )
    )

    for table in ("project_fields", "tender_requirements"):
        op.add_column(table, sa.Column("source_document_version_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("source_pipeline_run_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_source_document_version_id",
            table,
            "document_versions",
            ["source_document_version_id"],
            ["id"],
        )
        op.create_foreign_key(
            f"fk_{table}_source_pipeline_run_id",
            table,
            "tender_pipeline_runs",
            ["source_pipeline_run_id"],
            ["id"],
        )
        op.create_index(
            f"ix_{table}_source_document_version_id", table, ["source_document_version_id"]
        )
        op.create_index(
            f"ix_{table}_source_pipeline_run_id", table, ["source_pipeline_run_id"]
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM tender_pipeline_stages WHERE stage_name = 'downstream_analysis'"
        )
    )
    for table in ("tender_requirements", "project_fields"):
        op.drop_index(f"ix_{table}_source_pipeline_run_id", table_name=table)
        op.drop_index(f"ix_{table}_source_document_version_id", table_name=table)
        op.drop_constraint(f"fk_{table}_source_pipeline_run_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_source_document_version_id", table, type_="foreignkey")
        op.drop_column(table, "source_pipeline_run_id")
        op.drop_column(table, "source_document_version_id")

    op.drop_index(
        "ux_tender_pipeline_runs_active_document_version",
        table_name="tender_pipeline_runs",
    )
    op.drop_index(
        "ix_tender_pipeline_runs_downstream_analysis_run_id",
        table_name="tender_pipeline_runs",
    )
    op.drop_constraint(
        "fk_tender_pipeline_runs_downstream_analysis_run_id",
        "tender_pipeline_runs",
        type_="foreignkey",
    )
    op.drop_column("tender_pipeline_runs", "downstream_analysis_run_id")

    op.drop_constraint(
        "tender_pipeline_runs_status_check", "tender_pipeline_runs", type_="check"
    )
    op.create_check_constraint(
        "tender_pipeline_runs_status_check",
        "tender_pipeline_runs",
        "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
    )
