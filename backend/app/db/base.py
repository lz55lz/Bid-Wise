"""所有 SQLAlchemy ORM 模型共用的 Declarative Base。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Alembic 从这里收集迁移元数据。"""
