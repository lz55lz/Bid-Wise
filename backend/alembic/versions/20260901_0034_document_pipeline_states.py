"""扩展项目文档处理状态机。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260901_0034"
down_revision: str | Sequence[str] | None = "20260901_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("document_versions_parse_status_check", "document_versions", type_="check")
    op.create_check_constraint(
        "document_versions_parse_status_check",
        "document_versions",
        "parse_status IN ('UPLOADED', 'QUEUED', 'PARSING', 'CLEANING', "
        "'BUILDING_EVIDENCE', 'INDEXING', 'READY', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint("document_versions_parse_status_check", "document_versions", type_="check")
    op.create_check_constraint(
        "document_versions_parse_status_check",
        "document_versions",
        "parse_status IN ('UPLOADED', 'QUEUED', 'PARSING', 'READY', 'FAILED')",
    )
