from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.db import get_session
from app.game.manager import manager
from app.models import Action, Hand, HoleCard, Room, RoomMember, User

router = APIRouter(prefix="/api")


# ---- auth ----

class RegisterPayload(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _uname(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("username must be alphanumeric/underscore")
        return v


class LoginPayload(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/auth/register", response_model=TokenResponse)
async def register(payload: RegisterPayload, session: AsyncSession = Depends(get_session)):
    existing = await session.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "username taken")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.username,
    )
    session.add(user)
    await session.flush()
    await session.commit()
    token = create_access_token(user.id, user.username)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "username": user.username, "display_name": user.display_name},
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginPayload, session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token = create_access_token(user.id, user.username)
    return TokenResponse(
        access_token=token,
        user={"id": user.id, "username": user.username, "display_name": user.display_name},
    )


@router.get("/auth/me")
async def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


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
    # find hands where the user's seat is in seats snapshot
    stmt = select(Hand).order_by(desc(Hand.started_at)).limit(limit)
    hands = (await session.scalars(stmt)).all()
    out = []
    for h in hands:
        seats = h.seats or {}
        mine = next(
            (
                (int(idx), info)
                for idx, info in seats.items()
                if info.get("user_id") == user.id
            ),
            None,
        )
        if not mine:
            continue
        seat_idx, info = mine
        net = 0
        if h.winner_summary:
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
        "hole_cards": hole_cards,
    }
