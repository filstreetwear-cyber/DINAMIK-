"""
Database engine and session factory.

Async engine for the application (FastAPI, services), used with
SQLAlchemy 2.0's asyncio extension. Alembic uses its own synchronous
connection (see migrations/env.py) — that's standard practice and keeps
migrations simple, it does not imply two different databases.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, closes it after the request."""
    async with SessionLocal() as session:
        yield session
