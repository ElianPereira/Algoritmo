from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.database import Base

logger = logging.getLogger(__name__)

# Normalise URL — Railway emits postgres:// or postgresql://; asyncpg needs postgresql+asyncpg://
_url = settings.database_url
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _url.startswith("sqlite:///"):
    _url = _url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

logger.info("Database engine: %s", _url.split("@")[-1] if "@" in _url else _url)

engine = create_async_engine(_url, echo=False, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables on startup."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables verified/created successfully")
    except Exception as exc:
        logger.error("Database init failed: %s", exc, exc_info=True)
        raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
