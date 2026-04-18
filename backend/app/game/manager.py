from __future__ import annotations

import asyncio
import random
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.game.room import Room
from app.models import Room as RoomModel

settings = get_settings()


_ALPHA = string.ascii_uppercase + string.digits


def _gen_code(n: int = 6) -> str:
    return "".join(random.choices(_ALPHA, k=n))


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        sb: int,
        bb: int,
        buyin_min: int,
        buyin_max: int,
        created_by: int,
        max_seats: int = 9,
    ) -> Room:
        async with self._lock:
            for _ in range(10):
                code = _gen_code()
                existing = await session.scalar(select(RoomModel).where(RoomModel.code == code))
                if not existing:
                    break
            else:
                raise RuntimeError("could not generate unique code")

            closes_at = datetime.now(timezone.utc) + timedelta(seconds=settings.room_lifetime_s)
            room_model = RoomModel(
                code=code,
                name=name,
                sb=sb,
                bb=bb,
                buyin_min=buyin_min,
                buyin_max=buyin_max,
                max_seats=max_seats,
                created_by=created_by,
                closes_at=closes_at,
            )
            session.add(room_model)
            await session.flush()
            await session.commit()

            room = Room(
                id=room_model.id,
                code=code,
                name=name,
                sb=sb,
                bb=bb,
                buyin_min=buyin_min,
                buyin_max=buyin_max,
                max_seats=max_seats,
            )
            room.closes_at = closes_at
            room.start_loop()
            self._rooms[code] = room
            return room

    async def get_or_load(self, session: AsyncSession, code: str) -> Room | None:
        async with self._lock:
            if code in self._rooms:
                return self._rooms[code]
            rm = await session.scalar(select(RoomModel).where(RoomModel.code == code))
            if not rm or rm.closed_at:
                return None
            room = Room(
                id=rm.id,
                code=rm.code,
                name=rm.name,
                sb=rm.sb,
                bb=rm.bb,
                buyin_min=rm.buyin_min,
                buyin_max=rm.buyin_max,
                max_seats=rm.max_seats,
            )
            room.closes_at = rm.closes_at
            room.start_loop()
            self._rooms[code] = room
            return room

    def forget(self, code: str) -> None:
        self._rooms.pop(code, None)

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code)

    def all(self) -> list[Room]:
        return list(self._rooms.values())

    async def shutdown(self) -> None:
        for room in list(self._rooms.values()):
            await room.close()
        self._rooms.clear()


manager = RoomManager()
