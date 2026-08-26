"""Add sys_user_oauth and sys_login_log tables for WeCom OAuth."""

from alembic import op
import sqlalchemy as sa

revision = "202608290000"
down_revision = "202608280000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 创建 sys_user_oauth（用户 OAuth 绑定记录）
    op.create_table(
        "sys_user_oauth",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("corp_id", sa.String(64), nullable=False),
        sa.Column("wecom_user_id", sa.String(64), nullable=True),
        sa.Column("wecom_open_id", sa.String(64), nullable=True),
        sa.Column("union_id", sa.String(64), nullable=True),
        sa.Column("create_time", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("update_time", sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("corp_id", "wecom_user_id", name="uk_corp_wecom_userid"),
        sa.UniqueConstraint("corp_id", "wecom_open_id", name="uk_corp_wecom_openid"),
    )

    # 创建 sys_login_log（登录日志）
    op.create_table(
        "sys_login_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid, nullable=True, index=True),
        sa.Column("login_type", sa.String(32), nullable=False, server_default="wecom_qrcode"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("location", sa.String(128), nullable=True),
        sa.Column("browser", sa.String(128), nullable=True),
        sa.Column("os", sa.String(128), nullable=True),
        sa.Column("login_time", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.SmallInteger, nullable=False, server_default="1"),
        sa.Column("msg", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sys_login_log")
    op.drop_table("sys_user_oauth")
