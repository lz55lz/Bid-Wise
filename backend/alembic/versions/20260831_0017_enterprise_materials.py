# ruff: noqa: E501
"""建立企业、项目-企业绑定及演示材料库。

Revision ID: 20260831_0017
Revises: 20260830_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260831_0017"
down_revision: str | Sequence[str] | None = "20260830_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 企业是投标业务主体而非 tenant。项目可选择一或多家企业，支持联合体投标。
    op.create_table(
        "enterprises",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("credit_code", sa.String(length=18)),
        sa.Column("enterprise_type", sa.String(length=32)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="enterprises_status_check"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credit_code"),
    )
    op.create_index("ix_enterprises_deleted_at", "enterprises", ["deleted_at"])
    op.create_table(
        "project_enterprises",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("is_lead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("project_id", "enterprise_id"),
    )
    op.create_table(
        "enterprise_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("enterprise_id", sa.Uuid(), nullable=False),
        sa.Column("material_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("material_no", sa.String(length=128)),
        sa.Column("issuer", sa.String(length=256)),
        sa.Column("level", sa.String(length=128)),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(length=16)),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "material_type IN ('QUALIFICATION', 'CERTIFICATE', 'PERSONNEL', 'PROJECT_EXPERIENCE', 'FINANCE', 'OTHER')",
            name="enterprise_materials_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'CONFIRMED', 'ARCHIVED')", name="enterprise_materials_status_check"
        ),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_enterprise_materials_material_type", "enterprise_materials", ["material_type"]
    )
    op.create_index(
        "ix_enterprise_materials_enterprise_id", "enterprise_materials", ["enterprise_id"]
    )
    op.create_index("ix_enterprise_materials_material_no", "enterprise_materials", ["material_no"])
    op.create_index("ix_enterprise_materials_valid_to", "enterprise_materials", ["valid_to"])
    op.create_index("ix_enterprise_materials_deleted_at", "enterprise_materials", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_enterprise_materials_deleted_at", table_name="enterprise_materials")
    op.drop_index("ix_enterprise_materials_valid_to", table_name="enterprise_materials")
    op.drop_index("ix_enterprise_materials_material_no", table_name="enterprise_materials")
    op.drop_index("ix_enterprise_materials_material_type", table_name="enterprise_materials")
    op.drop_index("ix_enterprise_materials_enterprise_id", table_name="enterprise_materials")
    op.drop_table("enterprise_materials")
    op.drop_table("project_enterprises")
    op.drop_index("ix_enterprises_deleted_at", table_name="enterprises")
    op.drop_table("enterprises")
