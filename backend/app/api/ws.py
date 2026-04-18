"""WebSocket endpoint for game play."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_token
from app.db import SessionLocal, get_session
from app.game.manager import manager
from app.game.room import Room
from app.models import User

log = logging.getLogger(__name__)
router = APIRouter()


async def _authenticate(ws: WebSocket) -> User | None:
    token = ws.query_params.get("token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    async with SessionLocal() as session:
        from sqlalchemy import select
        return await session.scalar(select(User).where(User.id == int(payload["sub"])))


@router.websocket("/ws/room/{code}")
async def game_ws(ws: WebSocket, code: str):
    await ws.accept()
    user = await _authenticate(ws)
    if not user:
        await ws.send_json({"type": "error", "msg": "unauthenticated"})
        await ws.close()
        return
    code = code.upper()
    async with SessionLocal() as session:
        room = await manager.get_or_load(session, code)
    if not room:
        await ws.send_json({"type": "error", "msg": "room not found"})
        await ws.close()
        return

    # existing seat if user already seated (reconnect)
    existing = room.member_by_user(user.id)
    seat_idx = existing.seat_idx if existing else None

    outbound: asyncio.Queue = asyncio.Queue(maxsize=64)
    room.attach_connection(seat_idx, outbound)

    # send initial state
    await room.push_state_to_all()

    async def sender():
        try:
            while True:
                msg = await outbound.get()
                await ws.send_json(msg)
        except Exception:
            pass

    send_task = asyncio.create_task(sender())

    try:
        while True:
            data = await ws.receive_json()
            t = data.get("type")
            if t == "sit":
                if seat_idx is not None:
                    await ws.send_json({"type": "error", "msg": "already seated"})
                    continue
                try:
                    m = await room.sit(
                        user_id=user.id,
                        display_name=user.display_name,
                        seat_idx=data.get("seat_idx"),
                        buyin=int(data.get("buyin") or room.buyin_max),
                    )
                    seat_idx = m.seat_idx
                    room.detach_connection(None, outbound)
                    room.attach_connection(seat_idx, outbound)
                    await room.push_state_to_all()
                except ValueError as e:
                    await ws.send_json({"type": "error", "msg": str(e)})
            elif t == "rebuy":
                if seat_idx is None:
                    await ws.send_json({"type": "error", "msg": "not seated"})
                    continue
                try:
                    await room.rebuy(
                        seat_idx=seat_idx,
                        buyin=int(data.get("buyin") or room.buyin_max),
                    )
                    await room.push_state_to_all()
                except ValueError as e:
                    await ws.send_json({"type": "error", "msg": str(e)})
            elif t == "stand":
                if seat_idx is not None:
                    await room.stand_up(seat_idx)
                    room.detach_connection(seat_idx, outbound)
                    room.attach_connection(None, outbound)
                    seat_idx = None
                    await room.push_state_to_all()
            elif t == "add_bot":
                try:
                    await room.add_bot(
                        tier=data.get("tier") or "regular",
                        seat_idx=data.get("seat_idx"),
                        buyin=data.get("buyin"),
                    )
                    await room.push_state_to_all()
                except ValueError as e:
                    await ws.send_json({"type": "error", "msg": str(e)})
            elif t == "remove_bot":
                sid = data.get("seat_idx")
                if sid is not None and sid in room.members and room.members[sid].is_bot:
                    await room.stand_up(sid)
                    await room.push_state_to_all()
            elif t == "action":
                if seat_idx is None:
                    await ws.send_json({"type": "error", "msg": "not seated"})
                    continue
                ok = await room.submit_action(seat_idx, data)
                if not ok:
                    await ws.send_json({"type": "error", "msg": "not your turn"})
            elif t == "chat":
                text = str(data.get("text", ""))[:500]
                if text:
                    await room.broadcast(
                        {"type": "chat", "from": user.display_name, "text": text}
                    )
            else:
                await ws.send_json({"type": "error", "msg": f"unknown type {t}"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error")
    finally:
        send_task.cancel()
        room.detach_connection(seat_idx, outbound)
