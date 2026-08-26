"""replace IM tenant fields with an explicit channel owner.

Revision ID: 202609130000
Revises: 202609120000
"""

import sqlalchemy as sa

from alembic import op

revision = "202609130000"
down_revision = "202609120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "im_channels",
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
        schema="app",
    )
    op.execute(
        "UPDATE app.im_channels "
        "SET owner_user_id = CASE "
        "WHEN length(tenant_id) = 32 THEN tenant_id::uuid::text "
        "ELSE tenant_id END"
    )
    op.alter_column("im_channels", "owner_user_id", nullable=False, schema="app")
    op.create_index(
        "ix_app_im_channels_owner_user_id",
        "im_channels",
        ["owner_user_id"],
        schema="app",
    )
    op.drop_index("ix_app_im_channels_tenant_id", table_name="im_channels", schema="app")
    op.drop_column("im_channels", "tenant_id", schema="app")

    op.drop_index(
        "ix_app_im_channel_sessions_tenant_id",
        table_name="im_channel_sessions",
        schema="app",
    )
    op.drop_column("im_channel_sessions", "tenant_id", schema="app")


def downgrade() -> None:
    op.add_column(
        "im_channel_sessions",
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        schema="app",
    )
    op.execute(
        "UPDATE app.im_channel_sessions s SET tenant_id = c.owner_user_id "
        "FROM app.im_channels c WHERE c.id = s.channel_id"
    )
    op.alter_column("im_channel_sessions", "tenant_id", nullable=False, schema="app")
    op.create_index(
        "ix_app_im_channel_sessions_tenant_id",
        "im_channel_sessions",
        ["tenant_id"],
        schema="app",
    )

    op.add_column(
        "im_channels",
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        schema="app",
    )
    op.execute("UPDATE app.im_channels SET tenant_id = owner_user_id")
    op.alter_column("im_channels", "tenant_id", nullable=False, schema="app")
    op.create_index(
        "ix_app_im_channels_tenant_id", "im_channels", ["tenant_id"], schema="app"
    )
    op.drop_index("ix_app_im_channels_owner_user_id", table_name="im_channels", schema="app")
    op.drop_column("im_channels", "owner_user_id", schema="app")
