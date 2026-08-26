"""Add wecom_user_id to users table."""

from alembic import op
import sqlalchemy as sa

revision = "202608280000"
down_revision = "202608270000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("wecom_user_id", sa.String(64), nullable=True, index=True))


def downgrade() -> None:
    op.drop_column("users", "wecom_user_id")
