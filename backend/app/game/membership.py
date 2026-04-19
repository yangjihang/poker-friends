"""持久化 RoomMember.stack 的 helper。

目的：进程崩溃止损。内存里 Room.members 丢了以后，至少 DB 里还有
每手末最后一次已知的 stack，admin 可以据此人工补偿。

写入时机：
- sit 成功时插入或复用同 (room_id, user_id) 的活跃行
- rebuy 成功时累加更新
- 每手 hand_end 后批量更新
- stand_up 时标记 left_at（当前这笔 sit-session 结束）

唯一行保证：DB 层有 partial unique index `ux_room_members_active`
（(room_id, user_id) WHERE left_at IS NULL），配合 INSERT ... ON CONFLICT 防并发双插。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RoomMember


async def upsert_active(
    session: AsyncSession,
    *,
    room_id: int,
    user_id: int,
    seat_idx: int,
    display_name: str,
    stack: int,
) -> None:
    """原子 upsert 活跃 (room_id, user_id) 行。DB 层 partial unique 保证唯一。
    不 commit（由调用方统一事务）。
    """
    # ON CONFLICT 的 index_where 必须和 DB 里 partial unique 的谓词一致
    # （见 db.py: `WHERE left_at IS NULL AND user_id IS NOT NULL`）
    from sqlalchemy import and_
    stmt = (
        pg_insert(RoomMember)
        .values(
            room_id=room_id,
            user_id=user_id,
            seat_idx=seat_idx,
            display_name=display_name,
            is_bot=False,
            stack=stack,
        )
        .on_conflict_do_update(
            index_elements=["room_id", "user_id"],
            index_where=and_(
                RoomMember.left_at.is_(None),
                RoomMember.user_id.is_not(None),
            ),
            set_=dict(seat_idx=seat_idx, display_name=display_name, stack=stack),
        )
    )
    await session.execute(stmt)


async def update_stack(
    session: AsyncSession, *, room_id: int, user_id: int, stack: int
) -> None:
    """只改 stack，不建新行。活跃行不存在就忽略（通常是 bot 或 race）。"""
    row = await session.scalar(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == user_id,
            RoomMember.left_at.is_(None),
        )
    )
    if row is not None:
        row.stack = stack


async def mark_left(
    session: AsyncSession, *, room_id: int, user_id: int, final_stack: int
) -> None:
    """离桌：活跃行标 left_at + 记录最终 stack（方便事后对账）。"""
    row = await session.scalar(
        select(RoomMember).where(
            RoomMember.room_id == room_id,
            RoomMember.user_id == user_id,
            RoomMember.left_at.is_(None),
        )
    )
    if row is not None:
        row.stack = final_stack
        row.left_at = datetime.now(timezone.utc)
