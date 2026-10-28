from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Postgres only. psycopg 3 serves both the async app and the sync Alembic migrations from the same URL.
engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """One session per request. Services own the commit; anything uncommitted is rolled back on close."""
    async with SessionLocal() as db:
        yield db
