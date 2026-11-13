"""Database engines and session factories.

Each process builds its own engine the first time it needs one, then reuses it
for the life of that process: the API imports the async pair, the Celery worker
imports the sync pair. Nothing is created at import time, so a pre-forked
worker never inherits a pool.
"""

from functools import lru_cache

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


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_async_session() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


@lru_cache(maxsize=1)
def get_sync_session() -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(), expire_on_commit=False)
