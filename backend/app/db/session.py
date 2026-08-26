from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.errors import DomainError


# -------------------------------------------------------------------
# Sync engine + sessionmaker (keep existing for non-async code)
# -------------------------------------------------------------------
def get_session_factory() -> sessionmaker[Session]:
    settings = get_settings()
    if not settings.database_url:
        raise DomainError("SERVICE_UNAVAILABLE", "数据库未配置", 503)
    return sessionmaker(
        bind=create_engine(
            settings.database_url,
            pool_pre_ping=True,
            client_encoding="utf8",
        ),
        autoflush=False,
    )


# -------------------------------------------------------------------
# Async engine + sessionmaker (singleton per process)
# -------------------------------------------------------------------
_async_engine = None
_async_sessionmaker = None


def _get_async_engine():
    global _async_engine
    if _async_engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise DomainError("SERVICE_UNAVAILABLE", "数据库未配置", 503)
        async_url = settings.database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        _async_engine = create_async_engine(
            async_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _async_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_sessionmaker
    if _async_sessionmaker is None:
        _async_sessionmaker = async_sessionmaker(
            bind=_get_async_engine(),
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )
    return _async_sessionmaker


def get_db_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.close()
