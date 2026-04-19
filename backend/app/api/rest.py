from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.bank import adjust_balance
from app.config import get_settings
from app.db import get_session
from app.game.manager import manager
from app.models import Action, Hand, HoleCard, InviteCode, Room, RoomMember, User

router = APIRouter(prefix="/api")


# ---- auth ----

class RegisterPayload(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    invite_code: str = Field(min_length=4, max_length=32)

    @field_validator("username")
    @classmethod
    def _uname(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("username must be alphanumeric/underscore")
        return v

    @field_validator("invite_code")
    @classmethod
    def _code(cls, v: str) -> str:
        return v.strip().upper()


class LoginPayload(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def _user_public(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "balance": user.balance,
        "is_admin": user.is_admin,
    }


@router.post("/auth/register", response_model=TokenResponse)
async def register(payload: RegisterPayload, session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    existing = await session.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "username taken")

    # 行锁查邀请码，防并发双用。
    invite = await session.scalar(
        select(InviteCode)
        .where(InviteCode.code == payload.invite_code, InviteCode.used_by.is_(None))
        .with_for_update()
    )
    if not invite:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码无效或已被使用")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.username,
        balance=0,
    )
    session.add(user)
    try:
        await session.flush()  # 拿到 user.id，同时触发 UNIQUE(username) 约束
    except IntegrityError:
        # 并发双注册：两个请求都过了 existing 检查，此时 DB 拒绝其中一个。
        # 邀请码的 FOR UPDATE 锁此刻释放，另一个请求还能拿去用，没有浪费。
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "username taken")

    if settings.register_bonus > 0:
        await adjust_balance(
            session,
            user=user,
            amount=settings.register_bonus,
            type="register_bonus",
            note="新用户注册奖励",
        )
    invite.used_by = user.id
    invite.used_at = datetime.now(timezone.utc)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "username taken")

    token = create_access_token(user.id, user.username, user.password_version)
    return TokenResponse(access_token=token, user=_user_public(user))


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginPayload, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token = create_access_token(user.id, user.username, user.password_version)
    return TokenResponse(access_token=token, user=_user_public(user))


@router.get("/auth/me")
async def me(user: User = Depends(current_user)):
    return _user_public(user)


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


@router.post("/auth/change_password")
async def change_password(
    payload: ChangePasswordPayload,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码错误")
    if payload.old_password == payload.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "新密码不能与原密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.password_version = (user.password_version or 0) + 1
    await session.commit()
    # 签发新 token（携带新 password_version），前端应当替换本地 token
    new_token = create_access_token(user.id, user.username, user.password_version)
    return {"ok": True, "access_token": new_token}


# ---- rooms ----

class CreateRoomPayload(BaseModel):
    name: str = Field(max_length=64)
    sb: int = Field(ge=1)
    bb: int = Field(ge=2)
    buyin_min: int = Field(ge=1)
    buyin_max: int = Field(ge=1)
    max_seats: int = Field(default=9, ge=2, le=9)

    @field_validator("bb")
    @classmethod
    def _bb(cls, v, info):
        if v < info.data.get("sb", 0):
            raise ValueError("bb must be >= sb")
        return v

    @field_validator("buyin_max")
    @classmethod
    def _buyin_max(cls, v, info):
        lo = info.data.get("buyin_min")
        bb = info.data.get("bb")
        if lo is not None and v < lo:
            raise ValueError("buyin_max must be >= buyin_min")
        if bb is not None and lo is not None and lo < bb:
            raise ValueError("buyin_min must be >= bb")
        return v


@router.post("/rooms")
async def create_room(
    payload: CreateRoomPayload,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    room = await manager.create(
        session,
        name=payload.name,
        sb=payload.sb,
        bb=payload.bb,
        buyin_min=payload.buyin_min,
        buyin_max=payload.buyin_max,
        max_seats=payload.max_seats,
        created_by=user.id,
    )
    return {"code": room.code, "name": room.name}


@router.get("/rooms")
async def list_rooms(user: User = Depends(current_user)):
    return [
        {
            "code": r.code,
            "name": r.name,
            "sb": r.sb,
            "bb": r.bb,
            "seated": len(r.members),
            "max_seats": r.max_seats,
            "closes_at": r.closes_at.isoformat() if r.closes_at else None,
        }
        for r in manager.all()
        if not r.is_closed
    ]


@router.get("/rooms/{code}")
async def get_room(
    code: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    room = await manager.get_or_load(session, code.upper())
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "room not found")
    return {
        "code": room.code,
        "name": room.name,
        "sb": room.sb,
        "bb": room.bb,
        "buyin_min": room.buyin_min,
        "buyin_max": room.buyin_max,
        "max_seats": room.max_seats,
        "created_by": room.created_by,
        "closes_at": room.closes_at.isoformat() if room.closes_at else None,
        "closed": room.is_closed,
        "final_standings": room.final_standings,
    }


# ---- hand history ----

@router.get("/hands")
async def my_hands(
    limit: int = 50,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    # 用 hands.user_ids 的 GIN 索引直接命中；不再扫 JSONB / 上层过滤
    limit = max(1, min(limit, 500))
    stmt = (
        select(Hand)
        .where(Hand.user_ids.contains([user.id]))
        .order_by(desc(Hand.started_at))
        .limit(limit)
    )
    hands = (await session.scalars(stmt)).all()
    out = []
    for h in hands:
        seats = h.seats or {}
        seat_idx = next(
            (int(idx) for idx, info in seats.items() if info.get("user_id") == user.id),
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
async def hand_detail(
    hand_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    h = await session.get(Hand, hand_id)
    if not h:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hand not found")
    seats = h.seats or {}
    user_seat: int | None = None
    for idx, info in seats.items():
        if info.get("user_id") == user.id:
            user_seat = int(idx)
            break
    actions = (
        await session.scalars(
            select(Action).where(Action.hand_id == hand_id).order_by(Action.seq)
        )
    ).all()
    hole_rows = (
        await session.scalars(select(HoleCard).where(HoleCard.hand_id == hand_id))
    ).all()
    hole_cards = []
    for hc in hole_rows:
        visible = hc.shown or hc.seat_idx == user_seat
        hole_cards.append(
            {
                "seat_idx": hc.seat_idx,
                "cards": hc.cards if visible else None,
                "shown": hc.shown,
            }
        )
    # seats 脱敏：只暴露自己的 user_id，其他玩家的 user_id 剔除（admin 走另一个端点）
    safe_seats = {}
    for idx, info in seats.items():
        copy = dict(info)
        if copy.get("user_id") != user.id:
            copy.pop("user_id", None)
        safe_seats[idx] = copy
    return {
        "hand_id": h.id,
        "room_id": h.room_id,
        "hand_no": h.hand_no,
        "started_at": h.started_at.isoformat() if h.started_at else None,
        "ended_at": h.ended_at.isoformat() if h.ended_at else None,
        "button_seat": h.button_seat,
        "sb": h.sb,
        "bb": h.bb,
        "seats": safe_seats,
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
        "hole_cards": hole_cards,
    }
