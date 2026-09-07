"""为评测运行增加可恢复性时间戳。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0039"
down_revision: str | Sequence[str] | None = "20260901_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evaluation_runs", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("evaluation_runs", sa.Column("completed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("evaluation_runs", "completed_at")
    op.drop_column("evaluation_runs", "started_at")
