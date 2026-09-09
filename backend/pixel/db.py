"""Async database + Redis handles. DATABASE_URL / REDIS_URL from the environment (Compose sets them)."""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
import redis.asyncio as aioredis

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://pixel:pixel@localhost:5434/pixel")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_engine: AsyncEngine | None = None
_redis = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_pre_ping=True)
    return _engine


def redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis
