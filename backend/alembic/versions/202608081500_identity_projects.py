"""Create the P0 identity, project, member and audit foundation."""

from alembic import op

revision = "202608081500"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create extension if not exists pgcrypto")
    op.execute("create schema if not exists app")
    op.execute("""
        create table app.roles (
          code varchar(40) primary key, name varchar(64) not null,
          description varchar(256) not null, is_system boolean not null default true,
          created_at timestamptz not null default now()
        );
        create table app.users (
          id uuid primary key default gen_random_uuid(), username varchar(64) not null unique,
          password_hash varchar(255) not null, display_name varchar(128) not null,
          status varchar(16) not null default 'ACTIVE', last_login_at timestamptz,
          created_at timestamptz not null, updated_at timestamptz not null,
          constraint ck_users_username check (username = lower(username))
        );
        create table app.user_roles (
          user_id uuid not null references app.users(id) on delete restrict,
          role_code varchar(40) not null references app.roles(code) on delete restrict,
          created_at timestamptz not null, primary key (user_id, role_code)
        );
        create table app.tender_projects (
          id uuid primary key default gen_random_uuid(), name varchar(256) not null,
          code varchar(128) not null unique, purchaser varchar(256) not null,
          project_type varchar(128) not null, region varchar(128) not null,
          budget numeric(18,2), max_price numeric(18,2), currency char(3) not null default 'CNY',
          bid_deadline timestamptz not null, status varchar(16) not null default 'DRAFT',
          owner_id uuid not null references app.users(id) on delete restrict, archived_at timestamptz,
          created_at timestamptz not null, updated_at timestamptz not null,
          constraint ck_tender_projects_money check ((budget is null or budget >= 0) and (max_price is null or max_price >= 0)),
          constraint ck_tender_projects_archived check ((status = 'ARCHIVED' and archived_at is not null) or status <> 'ARCHIVED')
        );
        create table app.project_members (
          project_id uuid not null references app.tender_projects(id) on delete restrict,
          user_id uuid not null references app.users(id) on delete restrict,
          role_code varchar(40) not null references app.roles(code) on delete restrict,
          created_at timestamptz not null, primary key (project_id, user_id, role_code)
        );
        create table app.audit_logs (
          id bigint generated always as identity primary key, actor_id uuid references app.users(id) on delete restrict,
          action varchar(80) not null, target_type varchar(64) not null, target_id uuid,
          project_id uuid references app.tender_projects(id) on delete restrict, request_id uuid,
          before_summary text, after_summary text, created_at timestamptz not null
        );
        create index ix_project_members_user on app.project_members (user_id, project_id);
        create index ix_audit_project_created on app.audit_logs (project_id, created_at desc);
    """)
    op.execute("""
      insert into app.roles (code, name, description) values
      ('SYSTEM_ADMIN','系统管理员','管理用户、角色和全局审计日志'),
      ('PROJECT_OWNER','项目负责人','管理项目成员、项目决策与报告'),
      ('BID_SPECIALIST','投标专员','维护投标项目资料并处理风险'),
      ('LEGAL_COMPLIANCE','法务/合规','复核风险并维护规则'),
      ('MATERIAL_ADMIN','企业材料管理员','维护企业材料与证明文件'),
      ('READ_ONLY','管理层/只读','查看被授权项目的已发布信息');
    """)


def downgrade() -> None:
    op.execute("drop table app.audit_logs")
    op.execute("drop table app.project_members")
    op.execute("drop table app.tender_projects")
    op.execute("drop table app.user_roles")
    op.execute("drop table app.users")
    op.execute("drop table app.roles")
    op.execute("drop schema app")
