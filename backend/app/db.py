from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    # Dev-time: create tables if missing. Prod uses Alembic.
    from app import models  # noqa: F401  register mappers
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent dev-time column additions for schema that predates them.
        await conn.execute(
            text("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS closes_at TIMESTAMPTZ")
        )
        await conn.execute(
            text("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS final_standings JSONB")
        )
