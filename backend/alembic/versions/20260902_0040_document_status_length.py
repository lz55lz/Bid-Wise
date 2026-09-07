"""容纳 BUILDING_EVIDENCE 文档解析状态。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0040"
down_revision: str | Sequence[str] | None = "20260901_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("document_versions", "parse_status", type_=sa.String(length=32))


def downgrade() -> None:
    op.alter_column("document_versions", "parse_status", type_=sa.String(length=16))
