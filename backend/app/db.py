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
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS balance BIGINT NOT NULL DEFAULT 0")
        )
        # 旧 schema 的 INTEGER → BIGINT 升位宽（幂等：已经是 bigint 的话 PG 会报错但不影响）。
        await conn.execute(text("ALTER TABLE users ALTER COLUMN balance TYPE BIGINT"))
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
        )
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_version INTEGER NOT NULL DEFAULT 0")
        )
        # ledger 同步升位宽（对空表/新建不影响）。
        await conn.execute(
            text("ALTER TABLE ledger_entries ALTER COLUMN amount TYPE BIGINT")
        )
        await conn.execute(
            text("ALTER TABLE ledger_entries ALTER COLUMN balance_after TYPE BIGINT")
        )
        # hands.user_ids 数组列 + GIN 索引，my_hands 查询用
        await conn.execute(
            text(
                "ALTER TABLE hands ADD COLUMN IF NOT EXISTS "
                "user_ids INTEGER[] NOT NULL DEFAULT '{}'"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_hands_user_ids "
                "ON hands USING gin (user_ids)"
            )
        )
        # 历史行的 user_ids 为空，用 seats JSONB 回填。
        # EXISTS 过滤 all-bot 的手（seats 里没有 user_id 字段），避免它们被反复 UPDATE。
        await conn.execute(
            text(
                """
                UPDATE hands
                SET user_ids = (
                    SELECT array_agg(DISTINCT (v->>'user_id')::int)
                    FROM jsonb_each(seats) e(k, v)
                    WHERE v ? 'user_id' AND v->>'user_id' IS NOT NULL
                )
                WHERE user_ids = '{}'::int[]
                  AND EXISTS (
                    SELECT 1 FROM jsonb_each(seats) e(k, v)
                    WHERE v ? 'user_id' AND v->>'user_id' IS NOT NULL
                  )
                """
            )
        )
        # room_members 同 (room_id, user_id) 活跃行（left_at IS NULL）应唯一。
        # 旧数据可能有重复：先保留 id 最小的活跃行删掉其余，再建 partial unique。
        await conn.execute(
            text(
                """
                DELETE FROM room_members
                WHERE left_at IS NULL
                  AND id NOT IN (
                    SELECT MIN(id)
                    FROM room_members
                    WHERE left_at IS NULL AND user_id IS NOT NULL
                    GROUP BY room_id, user_id
                  )
                  AND user_id IS NOT NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_room_members_active
                ON room_members (room_id, user_id)
                WHERE left_at IS NULL AND user_id IS NOT NULL
                """
            )
        )
        # ledger_entries.acked_at 独立字段（原先用 type 字符串后缀做状态机，换成字段更干净）
        await conn.execute(
            text("ALTER TABLE ledger_entries ADD COLUMN IF NOT EXISTS acked_at TIMESTAMPTZ")
        )
        # 游客模式：users.is_guest + rooms.allow_guest
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_guest BOOLEAN NOT NULL DEFAULT FALSE")
        )
        await conn.execute(
            text("ALTER TABLE rooms ADD COLUMN IF NOT EXISTS allow_guest BOOLEAN NOT NULL DEFAULT FALSE")
        )
