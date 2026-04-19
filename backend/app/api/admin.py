"""Admin REST endpoints. require_admin 保护。"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_admin
from app.auth.security import hash_password
from app.bank import adjust_balance
from app.db import get_session
from app.models import Action, Hand, HoleCard, InviteCode, LedgerEntry, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")

_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_INVITE_LENGTH = 8


def _user_row(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "balance": u.balance,
        "is_admin": u.is_admin,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/users")
async def list_users(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(User).order_by(desc(User.created_at)))).all()
    return [_user_row(u) for u in rows]


@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    u = await session.get(User, user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return _user_row(u)


@router.get("/users/{user_id}/hands")
async def user_hands(
    user_id: int,
    limit: int = 100,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    limit = max(1, min(limit, 500))
    stmt = (
        select(Hand)
        .where(Hand.user_ids.contains([user_id]))
        .order_by(desc(Hand.started_at))
        .limit(limit)
    )
    hands = (await session.scalars(stmt)).all()
    out = []
    for h in hands:
        seats = h.seats or {}
        seat_idx = next(
            (int(idx) for idx, info in seats.items() if info.get("user_id") == user_id),
            None,
        )
        net = 0
        if seat_idx is not None and h.winner_summary:
            for w in h.winner_summary:
                if w.get("seat_idx") == seat_idx:
                    net = w.get("net", 0)
                    break
        out.append(
            {
                "hand_id": h.id,
                "room_id": h.room_id,
                "hand_no": h.hand_no,
                "started_at": h.started_at.isoformat() if h.started_at else None,
                "ended_at": h.ended_at.isoformat() if h.ended_at else None,
                "net": net,
                "board": h.board,
                "pot_total": h.pot_total,
            }
        )
    return out


@router.get("/hands/{hand_id}")
async def admin_hand_detail(
    hand_id: int,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    h = await session.get(Hand, hand_id)
    if not h:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hand not found")
    actions = (
        await session.scalars(
            select(Action).where(Action.hand_id == hand_id).order_by(Action.seq)
        )
    ).all()
    hole_rows = (
        await session.scalars(select(HoleCard).where(HoleCard.hand_id == hand_id))
    ).all()
    return {
        "hand_id": h.id,
        "room_id": h.room_id,
        "hand_no": h.hand_no,
        "started_at": h.started_at.isoformat() if h.started_at else None,
        "ended_at": h.ended_at.isoformat() if h.ended_at else None,
        "button_seat": h.button_seat,
        "sb": h.sb,
        "bb": h.bb,
        "seats": h.seats,
        "board": h.board,
        "pot_total": h.pot_total,
        "winner_summary": h.winner_summary,
        "actions": [
            {
                "seq": a.seq,
                "street": a.street,
                "seat_idx": a.seat_idx,
                "actor_name": a.actor_name,
                "action_type": a.action_type,
                "amount": a.amount,
                "stack_after": a.stack_after,
                "pot_after": a.pot_after,
            }
            for a in actions
        ],
        # admin 可以看所有底牌，不过滤
        "hole_cards": [
            {"seat_idx": hc.seat_idx, "cards": hc.cards, "shown": hc.shown}
            for hc in hole_rows
        ],
    }


@router.get("/users/{user_id}/ledger")
async def user_ledger(
    user_id: int,
    limit: int = 200,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.user_id == user_id)
            .order_by(desc(LedgerEntry.created_at))
            .limit(max(1, min(limit, 1000)))
        )
    ).all()
    return [
        {
            "id": e.id,
            "type": e.type,
            "amount": e.amount,
            "balance_after": e.balance_after,
            "room_id": e.room_id,
            "hand_id": e.hand_id,
            "note": e.note,
            "actor_user_id": e.actor_user_id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


_MAX_ADJUST = 10**9  # 10 亿筹码上限，够 MVP 用，防手滑/恶意传天文数字溢出


class TopupPayload(BaseModel):
    amount: int = Field(..., ge=-_MAX_ADJUST, le=_MAX_ADJUST, description="正数=充值，负数=扣款")
    note: str | None = Field(default=None, max_length=200)


@router.post("/users/{user_id}/topup")
async def topup(
    user_id: int,
    payload: TopupPayload,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if payload.amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be nonzero")
    target = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    try:
        entry = await adjust_balance(
            session,
            user=target,
            amount=payload.amount,
            type="admin_topup",
            note=payload.note,
            actor_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    await session.commit()
    return {
        "ok": True,
        "user_id": target.id,
        "balance": target.balance,
        "ledger_id": entry.id,  # 用于后续 ack pending cashout 时引用
    }


@router.get("/invite_codes")
async def list_invite_codes(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(select(InviteCode).order_by(desc(InviteCode.created_at)))
    ).all()
    return [
        {
            "id": c.id,
            "code": c.code,
            "created_by": c.created_by,
            "used_by": c.used_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "used_at": c.used_at.isoformat() if c.used_at else None,
        }
        for c in rows
    ]


class ResetPasswordPayload(BaseModel):
    # 留空后端随机生成；指定则用指定值（限制长度防拍脑袋输入 1 位）。
    new_password: str | None = Field(default=None, min_length=6, max_length=128)


@router.post("/users/{user_id}/reset_password")
async def reset_password(
    user_id: int,
    payload: ResetPasswordPayload,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    target = await session.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    new_pw = payload.new_password or secrets.token_urlsafe(9)
    target.password_hash = hash_password(new_pw)
    target.password_version = (target.password_version or 0) + 1  # 作废旧 JWT
    await session.commit()
    # 审计：谁在什么时候重置了谁的密码（密码本身不落库）
    log.info(
        "admin %d (%s) reset password for user %d (%s)",
        admin.id, admin.username, target.id, target.username,
    )
    return {"ok": True, "new_password": new_pw}


@router.get("/pending_cashouts")
async def pending_cashouts(
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """列出所有未 ack 的 stand 失败遗留审计行 + 对应用户基本信息。

    流程：admin 看到后先进"用户 & 余额"给该用户手动 topup 补偿，记下 topup 那条 ledger id，
    回到这里 POST /api/admin/pending_cashouts/{id}/ack，body {matched_ledger_id} 引用 topup。
    """
    rows = (
        await session.scalars(
            select(LedgerEntry)
            .where(
                LedgerEntry.type == "room_cashout_pending",
                LedgerEntry.acked_at.is_(None),
            )
            .order_by(desc(LedgerEntry.created_at))
            .limit(500)
        )
    ).all()
    if not rows:
        return []
    # 一次查所有涉及的 user（避免 N+1）
    user_ids = list({r.user_id for r in rows})
    users = (
        await session.scalars(select(User).where(User.id.in_(user_ids)))
    ).all()
    u_map = {u.id: u for u in users}
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "username": u_map[r.user_id].username if r.user_id in u_map else None,
            "display_name": u_map[r.user_id].display_name if r.user_id in u_map else None,
            "amount": r.amount,
            "room_id": r.room_id,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


class AckPendingPayload(BaseModel):
    # 引用一条 admin_topup ledger，证明 admin 已经真的补过钱
    matched_ledger_id: int = Field(..., ge=1)


@router.post("/pending_cashouts/{entry_id}/ack")
async def ack_pending_cashout(
    entry_id: int,
    payload: AckPendingPayload,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """标记一条 pending cashout 已处理：要求传 matched_ledger_id 引用一条同用户、
    同等或更大金额的 admin_topup ledger 行作为补偿凭证。"""
    row = await session.scalar(select(LedgerEntry).where(LedgerEntry.id == entry_id))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entry not found")
    if row.type != "room_cashout_pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a pending cashout")
    if row.acked_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "already acked")
    match = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.id == payload.matched_ledger_id)
    )
    if not match:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "matched ledger not found")
    if match.type != "admin_topup":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "matched must be admin_topup")
    if match.user_id != row.user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "matched user_id mismatch")
    if match.amount < row.amount:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"matched amount {match.amount} < pending amount {row.amount}",
        )
    row.acked_at = datetime.now(timezone.utc)
    row.note = (row.note or "") + (
        f" | acked by admin {admin.username}, matched ledger={payload.matched_ledger_id}"
    )
    await session.commit()
    return {"ok": True}


class GenInvitesPayload(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


@router.post("/invite_codes")
async def gen_invite_codes(
    payload: GenInvitesPayload,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    codes: list[str] = []
    attempts = 0
    while len(codes) < payload.count and attempts < payload.count * 10:
        attempts += 1
        code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(_INVITE_LENGTH))
        session.add(InviteCode(code=code, created_by=admin.id))
        try:
            await session.flush()  # 单条 flush 触发 UNIQUE 约束
        except IntegrityError:
            # 碰撞（32^8 空间下极罕见）或与 CLI 并发冲突：回滚这一条继续。
            await session.rollback()
            continue
        codes.append(code)
    await session.commit()
    return {"codes": codes}
