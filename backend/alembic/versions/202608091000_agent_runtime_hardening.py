"""Harden agent persistence, adoption records, and LangGraph checkpoint ownership."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202608091000"
down_revision = "202608090500"
branch_labels = None
depends_on = None

_SCHEMA = "app"


def upgrade() -> None:
    op.add_column("agent_run_steps", sa.Column("model_id", sa.String(32)), schema=_SCHEMA)
    op.add_column("agent_run_steps", sa.Column("input_hash", sa.String(64)), schema=_SCHEMA)
    op.add_column("agent_run_steps", sa.Column("output_hash", sa.String(64)), schema=_SCHEMA)
    op.add_column("agent_run_steps", sa.Column("latency_ms", sa.Integer()), schema=_SCHEMA)

    op.create_table(
        "agent_recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app.agent_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app.tender_projects.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_agent", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("risk_type", sa.String(32)),
        sa.Column("severity", sa.String(16)),
        sa.Column("priority", sa.String(8)),
        sa.Column("owner_role", sa.String(80)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("adopted_target_type", sa.String(64)),
        sa.Column("adopted_target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("review_note", sa.Text()),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app.users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("kind in ('RISK','WORK_ITEM')", name="ck_agent_recommendation_kind"),
        sa.CheckConstraint(
            "status in ('PROPOSED','ADOPTED','DISMISSED','SUPERSEDED')",
            name="ck_agent_recommendation_status",
        ),
        sa.CheckConstraint(
            "(status = 'ADOPTED' and adopted_target_type is not null "
            "and adopted_target_id is not null) or (status <> 'ADOPTED')",
            name="ck_agent_recommendation_adoption",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "agent_recommendation_evidences",
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app.agent_recommendations.id"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app.evidences.id"),
            primary_key=True,
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_agent_recommendations_run",
        "agent_recommendations",
        ["agent_run_id", "status", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_agent_recommendations_project",
        "agent_recommendations",
        ["project_id", "status", "created_at"],
        schema=_SCHEMA,
    )

    # langgraph-checkpoint-postgres 3.1.2 schema.  Tables are modelled here so
    # the runtime database account can remain DML-only.
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), primary_key=True),
        schema=_SCHEMA,
    )
    op.create_table(
        "checkpoints",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default=sa.text("''")),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("parent_checkpoint_id", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "checkpoint_blobs",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default=sa.text("''")),
        sa.Column("channel", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary()),
        schema=_SCHEMA,
    )
    op.create_table(
        "checkpoint_writes",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_ns", sa.Text(), primary_key=True, server_default=sa.text("''")),
        sa.Column("checkpoint_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("idx", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text()),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("task_path", sa.Text(), nullable=False, server_default=sa.text("''")),
        schema=_SCHEMA,
    )
    op.create_index("checkpoints_thread_id_idx", "checkpoints", ["thread_id"], schema=_SCHEMA)
    op.create_index(
        "checkpoint_blobs_thread_id_idx", "checkpoint_blobs", ["thread_id"], schema=_SCHEMA
    )
    op.create_index(
        "checkpoint_writes_thread_id_idx", "checkpoint_writes", ["thread_id"], schema=_SCHEMA
    )
    checkpoint_migrations = sa.table(
        "checkpoint_migrations",
        sa.column("v", sa.Integer()),
        schema=_SCHEMA,
    )
    op.bulk_insert(checkpoint_migrations, [{"v": version} for version in range(10)])


def downgrade() -> None:
    op.drop_table("checkpoint_writes", schema=_SCHEMA)
    op.drop_table("checkpoint_blobs", schema=_SCHEMA)
    op.drop_table("checkpoints", schema=_SCHEMA)
    op.drop_table("checkpoint_migrations", schema=_SCHEMA)
    op.drop_table("agent_recommendation_evidences", schema=_SCHEMA)
    op.drop_table("agent_recommendations", schema=_SCHEMA)
    op.drop_column("agent_run_steps", "latency_ms", schema=_SCHEMA)
    op.drop_column("agent_run_steps", "output_hash", schema=_SCHEMA)
    op.drop_column("agent_run_steps", "input_hash", schema=_SCHEMA)
    op.drop_column("agent_run_steps", "model_id", schema=_SCHEMA)
