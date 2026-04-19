"""Persist hand history to Postgres.

A Recorder instance is bound to a single hand. Call `on_action` for every
engine action and `on_hand_end` when the hand finishes — errors are logged
but not re-raised so DB issues never crash the game loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.game.engine import ActionResult, HandEngine, SeatInfo
from app.models import Action, Hand, HoleCard

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, room_id: int, hand_no: int, engine: HandEngine, seats: list[SeatInfo]):
        self.room_id = room_id
        self.hand_no = hand_no
        self.engine = engine
        self.seats = seats
        self.hand_id: int | None = None
        self._seq = 0

    async def on_hand_start(self) -> None:
        seats_snapshot: dict[str, Any] = {
            str(s.seat_idx): {
                "display_name": s.display_name,
                "user_id": s.user_id,
                "is_bot": s.is_bot,
                "bot_tier": s.bot_tier,
                "starting_stack": s.stack,
            }
            for s in self.seats
        }
        # 人类玩家 id 列表，写到 hands.user_ids 列供 my_hands 查询（GIN 索引）
        user_ids = sorted({s.user_id for s in self.seats if s.user_id is not None})
        try:
            async with SessionLocal() as session:
                hand = Hand(
                    room_id=self.room_id,
                    hand_no=self.hand_no,
                    button_seat=self.engine.button_seat,
                    sb=self.engine.sb,
                    bb=self.engine.bb,
                    seats=seats_snapshot,
                    user_ids=user_ids,
                    board={},
                )
                session.add(hand)
                await session.flush()
                self.hand_id = hand.id
                await session.commit()
        except Exception:
            log.exception("recorder: failed to create hand row")

    async def on_action(self, result: ActionResult) -> None:
        if self.hand_id is None:
            return
        self._seq += 1
        try:
            async with SessionLocal() as session:
                seat_info = next(
                    (s for s in self.seats if s.seat_idx == result.seat_idx), None
                )
                actor_name = seat_info.display_name if seat_info else f"seat{result.seat_idx}"
                session.add(
                    Action(
                        hand_id=self.hand_id,
                        street=result.street,
                        seq=self._seq,
                        seat_idx=result.seat_idx,
                        actor_name=actor_name,
                        action_type=result.action_type,
                        amount=result.amount,
                        stack_after=result.stack_after,
                        pot_after=result.pot_after,
                    )
                )
                await session.commit()
        except Exception:
            log.exception("recorder: failed to persist action")

    async def on_hand_end(self) -> None:
        if self.hand_id is None:
            return
        try:
            async with SessionLocal() as session:
                hand = await session.get(Hand, self.hand_id)
                if hand is None:
                    return
                hand.ended_at = datetime.now(timezone.utc)
                hand.board = self.engine.board()
                hand.pot_total = self.engine.peak_pot
                hand.winner_summary = self.engine.winner_summary()

                showdown = set(self.engine.showdown_seats()) if self.engine.went_to_showdown else set()
                for s in self.seats:
                    cards = self.engine.hole_cards_of(s.seat_idx)
                    if not cards:
                        continue
                    session.add(
                        HoleCard(
                            hand_id=self.hand_id,
                            seat_idx=s.seat_idx,
                            cards=cards,
                            shown=s.seat_idx in showdown,
                        )
                    )
                await session.commit()
        except Exception:
            log.exception("recorder: failed to finalize hand")
