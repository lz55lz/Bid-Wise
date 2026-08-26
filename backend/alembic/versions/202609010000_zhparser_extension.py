"""add_zhparser_extension: zhparser + 'zh' text search config (idempotent)

补齐 PG zhparser 扩展与 'zh' text search config，避免 BM25 中文分词依赖
环境偶然状态。已有 PG 实例上 CREATE EXTENSION IF NOT EXISTS 与 'zh' config
存在性检查都是 no-op；新实例可一次性建好。
"""
from alembic import op

revision = '202609010000'
down_revision = '202608310003'


def upgrade():
    # zhparser 是 PG contrib 扩展（apt install postgresql-18-zhparser 或 supabase 内置），
    # 需要 superuser 安装；本迁移仅兜底已安装实例的 'zh' text search config 存在性。
    op.execute("CREATE EXTENSION IF NOT EXISTS zhparser;")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'zh') THEN
                CREATE TEXT SEARCH CONFIGURATION zh (PARSER = zhparser);
                ALTER TEXT SEARCH CONFIGURATION zh
                  ADD MAPPING FOR n,v,a,i,e,l,t,s,m,h,k,c,p,np,ns,nz,vn,d
                  WITH simple;
            END IF;
        END$$;
    """
    )


def downgrade():
    # 仅删除由本次创建的 'zh' 配置（cfgparser=zhparser 且唯一）；
    # 不强制 DROP EXTENSION zhparser（生产实例可能多库共享）。
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_ts_config
                WHERE cfgname = 'zh' AND cfgparser = 'zhparser'::regproc
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_ts_config
                WHERE cfgname = 'zh' AND cfgparser <> 'zhparser'::regproc
            ) THEN
                DROP TEXT SEARCH CONFIGURATION IF EXISTS zh;
            END IF;
        END$$;
    """
    )
