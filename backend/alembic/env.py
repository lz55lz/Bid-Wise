"""Alembic 迁移入口。

每个领域迁入 ORM 模型后，必须在 app/modules/<domain>/models.py 中声明并由
app.modules 的导入入口加载，确保 Base.metadata 能完整反映当前真实模型。
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.modules.analysis import models as analysis_models
from app.modules.conversations import models as conversation_models
from app.modules.decisions import models as decision_models
from app.modules.documents import models as document_models
from app.modules.evaluation import models as evaluation_models
from app.modules.evidence import models as evidence_models
from app.modules.findings import models as finding_models
from app.modules.identity import models as identity_models
from app.modules.indexing import models as indexing_models
from app.modules.knowledge import models as knowledge_models
from app.modules.matching import models as matching_models
from app.modules.materials import models as material_models
from app.modules.memories import models as memory_models
from app.modules.projects import models as project_models
from app.modules.reports import models as report_models
from app.modules.requirements import models as requirement_models
from app.modules.retrieval import models as retrieval_models
from app.modules.risks import models as risk_models
from app.modules.risks import rule_models as risk_rule_models  # noqa: F401
from app.modules.tender_analysis import models as tender_analysis_models

# 保留具名导入，确保每个已迁入领域的模型都会注册到 Base.metadata。
# 不能靠动态扫描：那会让迁移结果随导入顺序和运行环境改变。
_ = (
    analysis_models,
    conversation_models,
    decision_models,
    document_models,
    evidence_models,
    evaluation_models,
    finding_models,
    identity_models,
    indexing_models,
    knowledge_models,
    material_models,
    memory_models,
    matching_models,
    project_models,
    retrieval_models,
    report_models,
    requirement_models,
    risk_models,
    tender_analysis_models,
)
target_metadata = Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
    """生成 SQL 文本时不建立数据库连接。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "pyformat"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线执行迁移；迁移引擎独立于 API 的异步连接池。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
