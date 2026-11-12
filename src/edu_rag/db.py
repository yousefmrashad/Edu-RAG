"""Database engines and session factories.

Each process builds its own engine the first time it needs one, then reuses it
for the life of that process: the API imports the async pair, the Celery worker
imports the sync pair. Nothing is created at import time, so a pre-forked
worker never inherits a pool.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from edu_rag.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by the API and the worker."""


_async_engine: AsyncEngine | None = None
_sync_engine: Engine | None = None
_async_session: async_sessionmaker[AsyncSession] | None = None
_sync_session: sessionmaker[Session] | None = None


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _async_engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return _async_engine


def get_sync_engine() -> Engine:
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _sync_engine


def get_async_session() -> async_sessionmaker[AsyncSession]:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(get_async_engine(), expire_on_commit=False)
    return _async_session


def get_sync_session() -> sessionmaker[Session]:
    global _sync_session
    if _sync_session is None:
        _sync_session = sessionmaker(get_sync_engine(), expire_on_commit=False)
    return _sync_session
