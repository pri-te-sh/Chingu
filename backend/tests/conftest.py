"""Tests run against the Compose Postgres/Redis (DATABASE_URL / REDIS_URL) on a throwaway household."""
import os, asyncio, pytest, pytest_asyncio
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pixel:pixel@localhost:5434/pixel")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/9")
os.environ.setdefault("PIXEL_INFERENCE_URL", "http://localhost:8766")   # app startup must not load speech models in tests
os.environ.setdefault("SESSION_SECRET", "test-secret-not-for-prod")
import sqlalchemy as sa
from pixel import repo, models as m, db


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine():
    """asyncpg pools are bound to an event loop; pytest-asyncio gives each test its own -> dispose between tests."""
    yield
    if db._engine is not None:
        await db._engine.dispose(); db._engine = None
    if db._redis is not None:
        await db._redis.aclose(); db._redis = None


@pytest_asyncio.fixture
async def household():
    r = await repo.execute(sa.insert(m.households).values(name="test-household").returning(m.households.c.id))
    hid = r.scalar_one()
    yield hid
    await repo.execute(sa.delete(m.households).where(m.households.c.id == hid))


@pytest_asyncio.fixture
async def pixel(household):
    p = await repo.get_or_create_pixel(f"test-{household}", "lite", household_id=household)
    yield p
