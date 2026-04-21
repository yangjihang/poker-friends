from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    sb: Mapped[int] = mapped_column(Integer)
    bb: Mapped[int] = mapped_column(Integer)
    buyin_min: Mapped[int] = mapped_column(Integer)
    buyin_max: Mapped[int] = mapped_column(Integer)
    max_seats: Mapped[int] = mapped_column(Integer, default=9)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_standings: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    # 游客模式开关：True 则 is_guest 用户可入座；False（默认）为真钱房，仅非游客可入。
    allow_guest: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class RoomMember(Base):
    __tablename__ = "room_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    seat_idx: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(64))
    stack: Mapped[int] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Hand(Base):
    __tablename__ = "hands"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    hand_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    button_seat: Mapped[int] = mapped_column(Integer)
    sb: Mapped[int] = mapped_column(Integer)
    bb: Mapped[int] = mapped_column(Integer)
    seats: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # 参与这手的人类 user_id 列表，用 GIN 索引做 `my_hands` 查询（避免扫 seats JSONB）。
    user_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{}"
    )
    board: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    pot_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winner_summary: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_hands_room_started", "room_id", "started_at"),
        Index("ix_hands_user_ids", "user_ids", postgresql_using="gin"),
    )


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    hand_id: Mapped[int] = mapped_column(ForeignKey("hands.id"), index=True)
    street: Mapped[str] = mapped_column(String(12))
    seq: Mapped[int] = mapped_column(Integer)
    seat_idx: Mapped[int] = mapped_column(Integer)
    actor_name: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stack_after: Mapped[int] = mapped_column(Integer)
    pot_after: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("ix_actions_hand_seq", "hand_id", "seq"),)


class HoleCard(Base):
    __tablename__ = "hole_cards"

    hand_id: Mapped[int] = mapped_column(ForeignKey("hands.id"), primary_key=True)
    seat_idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    cards: Mapped[list[str]] = mapped_column(JSONB)
    shown: Mapped[bool] = mapped_column(Boolean, default=False)
