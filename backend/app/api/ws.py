"""WebSocket endpoint for game play."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_token
from app.bank import adjust_balance
from app.db import SessionLocal, get_session
from app.game.manager import manager
from app.game.membership import upsert_active as _member_upsert
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
        user = await session.scalar(select(User).where(User.id == int(payload["sub"])))
        if not user:
            return None
        if int(payload.get("pv", 0)) != (user.password_version or 0):
            return None  # 改密后的旧 token
        return user


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
                if user.is_guest and not room.allow_guest:
                    await ws.send_json({"type": "error", "msg": "该房间不允许游客入座"})
                    continue
                buyin = int(data.get("buyin") or room.buyin_max)
                if not (room.buyin_min <= buyin <= room.buyin_max):
                    await ws.send_json({"type": "error", "msg": "buyin out of range"})
                    continue
                new_balance: int | None = None
                # 事务：扣余额 → room.sit。任一失败则回滚。
                async with SessionLocal() as session:
                    from sqlalchemy import select as _sel
                    fresh = await session.scalar(
                        _sel(User).where(User.id == user.id).with_for_update()
                    )
                    if not fresh:
                        await ws.send_json({"type": "error", "msg": "user not found"})
                        continue
                    # 快照 balance，rollback 后 ORM 会过期。
                    have = fresh.balance
                    try:
                        await adjust_balance(
                            session, user=fresh, amount=-buyin, type="buyin_lock",
                            room_id=room.id, note=f"入桌 {room.code}",
                        )
                    except ValueError:
                        await session.rollback()
                        await ws.send_json({"type": "error", "msg": f"余额不足（需要 {buyin}，当前 {have}）"})
                        continue
                    try:
                        m = await room.sit(
                            user_id=user.id,
                            display_name=user.display_name,
                            seat_idx=data.get("seat_idx"),
                            buyin=buyin,
                        )
                    except ValueError as e:
                        await session.rollback()
                        await ws.send_json({"type": "error", "msg": str(e)})
                        continue
                    # RoomMember 快照，崩溃止损
                    await _member_upsert(
                        session, room_id=room.id, user_id=user.id,
                        seat_idx=m.seat_idx, display_name=user.display_name, stack=buyin,
                    )
                    await session.commit()
                    new_balance = fresh.balance
                seat_idx = m.seat_idx
                room.detach_connection(None, outbound)
                room.attach_connection(seat_idx, outbound)
                if new_balance is not None:
                    await ws.send_json({"type": "balance_update", "balance": new_balance})
                await room.push_state_to_all()
            elif t == "rebuy":
                if seat_idx is None:
                    await ws.send_json({"type": "error", "msg": "not seated"})
                    continue
                buyin = int(data.get("buyin") or room.buyin_max)
                if not (room.buyin_min <= buyin <= room.buyin_max):
                    await ws.send_json({"type": "error", "msg": "buyin out of range"})
                    continue
                new_balance = None
                async with SessionLocal() as session:
                    from sqlalchemy import select as _sel
                    fresh = await session.scalar(
                        _sel(User).where(User.id == user.id).with_for_update()
                    )
                    if not fresh:
                        await ws.send_json({"type": "error", "msg": "user not found"})
                        continue
                    have = fresh.balance
                    try:
                        await adjust_balance(
                            session, user=fresh, amount=-buyin, type="buyin_lock",
                            room_id=room.id, note=f"补带 {room.code}",
                        )
                    except ValueError:
                        await session.rollback()
                        await ws.send_json({"type": "error", "msg": f"余额不足（需要 {buyin}，当前 {have}）"})
                        continue
                    try:
                        await room.rebuy(seat_idx=seat_idx, buyin=buyin)
                    except ValueError as e:
                        await session.rollback()
                        await ws.send_json({"type": "error", "msg": str(e)})
                        continue
                    # 刷新 RoomMember 快照为累加后的 stack
                    mem = room.members.get(seat_idx)
                    if mem is not None:
                        await _member_upsert(
                            session, room_id=room.id, user_id=user.id,
                            seat_idx=seat_idx, display_name=user.display_name, stack=mem.stack,
                        )
                    await session.commit()
                    new_balance = fresh.balance
                if new_balance is not None:
                    await ws.send_json({"type": "balance_update", "balance": new_balance})
                await room.push_state_to_all()
            elif t == "stand":
                if seat_idx is not None:
                    await room.stand_up(seat_idx)
                    room.detach_connection(seat_idx, outbound)
                    room.attach_connection(None, outbound)
                    seat_idx = None
                    # stand_up 已经结算 balance 到 DB，这里查一下推给前端
                    try:
                        from sqlalchemy import select as _sel
                        async with SessionLocal() as session:
                            fresh = await session.scalar(_sel(User).where(User.id == user.id))
                            if fresh is not None:
                                await ws.send_json({"type": "balance_update", "balance": fresh.balance})
                    except Exception:
                        log.exception("balance_update after stand failed")
                    await room.push_state_to_all()
            elif t == "add_bot":
                try:
                    await room.add_bot(
                        tier=data.get("tier") or "patron",
                        seat_idx=data.get("seat_idx"),
                        buyin=data.get("buyin"),
                    )
                    await room.push_state_to_all()
                except ValueError as e:
                    await ws.send_json({"type": "error", "msg": str(e)})
            elif t == "remove_bot":
                sid = data.get("seat_idx")
                if sid is None or sid not in room.members or not room.members[sid].is_bot:
                    await ws.send_json({"type": "error", "msg": "not a bot seat"})
                    continue
                if user.id != room.created_by:
                    await ws.send_json({"type": "error", "msg": "仅房主可踢 AI"})
                    continue
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
