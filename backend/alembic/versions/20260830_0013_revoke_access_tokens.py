"""支持访问令牌主动撤销与密码重置失效。

Revision ID: 20260830_0013
Revises: 20260830_0012
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0013"
down_revision: str | Sequence[str] | None = "20260830_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保存已登出的 jti，并为用户增加令牌全量失效水位线。"""
    op.add_column("users", sa.Column("access_token_invalid_after", sa.DateTime(timezone=True)))
    op.create_table(
        "revoked_access_tokens",
        sa.Column("jti", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index("ix_revoked_access_tokens_user_id", "revoked_access_tokens", ["user_id"])
    op.create_index("ix_revoked_access_tokens_expires_at", "revoked_access_tokens", ["expires_at"])


def downgrade() -> None:
    """删除令牌撤销事实表与用户失效水位线。"""
    op.drop_index("ix_revoked_access_tokens_expires_at", table_name="revoked_access_tokens")
    op.drop_index("ix_revoked_access_tokens_user_id", table_name="revoked_access_tokens")
    op.drop_table("revoked_access_tokens")
    op.drop_column("users", "access_token_invalid_after")
