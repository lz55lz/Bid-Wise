"""创建身份与项目核心表。

Revision ID: 20260830_0001
Revises:
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """先建立身份事实源，再建立依赖用户归属的项目与项目成员表。"""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "system_roles",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.bulk_insert(
        sa.table(
            "system_roles",
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("description", sa.String()),
        ),
        [
            {
                "code": "SYSTEM_ADMIN",
                "name": "系统管理员",
                "description": "管理用户、角色与全局审计",
            },
            {
                "code": "BID_SPECIALIST",
                "name": "投标专员",
                "description": "处理被授权项目的投标工作",
            },
            {
                "code": "LEGAL_COMPLIANCE",
                "name": "法务合规",
                "description": "复核合规和风险结论",
            },
            {
                "code": "MATERIAL_ADMIN",
                "name": "材料管理员",
                "description": "维护企业材料与证明文件",
            },
            {
                "code": "READ_ONLY",
                "name": "只读人员",
                "description": "查看被授权项目中的已发布内容",
            },
        ],
    )

    op.create_table(
        "user_system_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["role_code"], ["system_roles.code"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "role_code"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("project_id", sa.Uuid()),
        sa.Column("before_summary", sa.Text()),
        sa.Column("after_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"])

    op.create_table(
        "tender_projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("purchaser", sa.String(length=256), nullable=False),
        sa.Column("project_type", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="CNY"),
        sa.Column("bid_deadline", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_tender_projects_code", "tender_projects", ["code"])
    op.create_index("ix_tender_projects_deleted_at", "tender_projects", ["deleted_at"])
    op.create_index("ix_tender_projects_owner_id", "tender_projects", ["owner_id"])

    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('OWNER', 'EDITOR', 'VIEWER')"),
        sa.ForeignKeyConstraint(["project_id"], ["tender_projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
    )


def downgrade() -> None:
    """按外键依赖的反向顺序删除本次创建的表。"""
    op.drop_table("project_members")
    op.drop_index("ix_tender_projects_owner_id", table_name="tender_projects")
    op.drop_index("ix_tender_projects_deleted_at", table_name="tender_projects")
    op.drop_index("ix_tender_projects_code", table_name="tender_projects")
    op.drop_table("tender_projects")
    op.drop_index("ix_audit_logs_target_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_project_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("user_system_roles")
    op.drop_table("system_roles")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
