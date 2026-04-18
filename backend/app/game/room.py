"""In-memory Room: seats, connections, game loop."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import SessionLocal
from app.game.bots import make_bot
from app.game.engine import ActionResult, HandEngine, SeatInfo
from app.game.recorder import Recorder
from app.models import Room as RoomModel

log = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class Member:
    seat_idx: int
    display_name: str
    stack: int
    is_bot: bool = False
    bot_tier: str | None = None
    user_id: int | None = None
    sitting_out: bool = False
    # per-connection asyncio.Queue for outbound messages (may have multiple tabs)
    connections: list[asyncio.Queue] = field(default_factory=list)
    # inbound action channel used only during that seat's turn
    action_channel: asyncio.Queue | None = None


class Room:
    def __init__(
        self,
        id: int,
        code: str,
        name: str,
        sb: int,
        bb: int,
        buyin_min: int,
        buyin_max: int,
        max_seats: int = 9,
    ):
        self.id = id
        self.code = code
        self.name = name
        self.sb = sb
        self.bb = bb
        self.buyin_min = buyin_min
        self.buyin_max = buyin_max
        self.max_seats = max_seats

        self.members: dict[int, Member] = {}
        self.observers: list[asyncio.Queue] = []
        self._membership_event = asyncio.Event()
        self.button_seat = 0
        self.hand_no = 0
        self._task: asyncio.Task | None = None
        self._closed = False
        self._lock = asyncio.Lock()
        self._current_engine: HandEngine | None = None
        self._actor_deadline_ms: int | None = None
        self._action_timeout_s: int = settings.action_timeout_s
        self.closes_at: datetime | None = None
        # key `u{user_id}` for humans, `b{seat_idx}` for bots — survives rename
        # and avoids collision when two humans happen to share a display name.
        self.standings: dict[str, dict[str, Any]] = {}
        self.final_standings: list[dict[str, Any]] | None = None

    # ---- membership ----

    def _next_seat(self) -> int | None:
        for i in range(self.max_seats):
            if i not in self.members:
                return i
        return None

    async def sit(
        self,
        user_id: int,
        display_name: str,
        seat_idx: int | None,
        buyin: int,
    ) -> Member:
        async with self._lock:
            if self._closed:
                raise ValueError("room is closed")
            if seat_idx is None:
                seat_idx = self._next_seat()
                if seat_idx is None:
                    raise ValueError("no open seats")
            if seat_idx in self.members:
                raise ValueError(f"seat {seat_idx} taken")
            if not (self.buyin_min <= buyin <= self.buyin_max):
                raise ValueError("buyin out of range")
            m = Member(seat_idx=seat_idx, display_name=display_name, stack=buyin, user_id=user_id)
            self.members[seat_idx] = m
            self._membership_event.set()
            return m

    async def add_bot(self, tier: str, seat_idx: int | None = None, buyin: int | None = None) -> Member:
        async with self._lock:
            if self._closed:
                raise ValueError("room is closed")
            if seat_idx is None:
                seat_idx = self._next_seat()
                if seat_idx is None:
                    raise ValueError("no open seats")
            if seat_idx in self.members:
                raise ValueError(f"seat {seat_idx} taken")
            if buyin is None:
                buyin = self.buyin_max
            m = Member(
                seat_idx=seat_idx,
                display_name=f"Bot-{tier}-{seat_idx}",
                stack=buyin,
                is_bot=True,
                bot_tier=tier,
            )
            self.members[seat_idx] = m
            self._membership_event.set()
            return m

    async def rebuy(self, seat_idx: int, buyin: int) -> None:
        async with self._lock:
            if self._closed:
                raise ValueError("room is closed")
            m = self.members.get(seat_idx)
            if not m or m.is_bot:
                raise ValueError("not seated")
            if m.stack >= self.bb:
                raise ValueError("still has chips")
            if not (self.buyin_min <= buyin <= self.buyin_max):
                raise ValueError("buyin out of range")
            m.stack = buyin
            m.sitting_out = False
            self._membership_event.set()

    async def stand_up(self, seat_idx: int) -> None:
        async with self._lock:
            m = self.members.pop(seat_idx, None)
            # If they were the current actor, unblock the game loop with a fold
            # so the next seat can act immediately.
            if m and m.action_channel is not None:
                try:
                    m.action_channel.put_nowait({"action": "fold"})
                except asyncio.QueueFull:
                    pass
            self._membership_event.set()

    def member_by_user(self, user_id: int) -> Member | None:
        for m in self.members.values():
            if m.user_id == user_id:
                return m
        return None

    # ---- connections ----

    def attach_connection(self, seat_idx: int | None, queue: asyncio.Queue) -> None:
        if seat_idx is not None and seat_idx in self.members:
            m = self.members[seat_idx]
            m.connections.append(queue)
            # User reconnected — re-seat if they were auto-sat-out.
            if m.sitting_out and not m.is_bot and m.stack >= self.bb:
                m.sitting_out = False
                self._membership_event.set()
        else:
            self.observers.append(queue)

    def detach_connection(self, seat_idx: int | None, queue: asyncio.Queue) -> None:
        if seat_idx is not None and seat_idx in self.members:
            m = self.members[seat_idx]
            try:
                m.connections.remove(queue)
            except ValueError:
                pass
            # When all WS tabs for a human seat disconnect, sit out so the
            # room loop skips them instead of waiting the full action timeout.
            if not m.is_bot and not m.connections:
                m.sitting_out = True
                if m.action_channel is not None:
                    try:
                        m.action_channel.put_nowait({"action": "fold"})
                    except asyncio.QueueFull:
                        pass
        try:
            self.observers.remove(queue)
        except ValueError:
            pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Send a message to every attached connection (observers + members)."""
        await self._broadcast(msg)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        queues: list[asyncio.Queue] = list(self.observers)
        for m in self.members.values():
            queues.extend(m.connections)
        for q in queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def _send_to(self, seat_idx: int, msg: dict[str, Any]) -> None:
        m = self.members.get(seat_idx)
        if not m:
            return
        for q in m.connections:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    # ---- state snapshots ----

    def _base_room_payload(self) -> dict[str, Any]:
        seats_payload = []
        for i in range(self.max_seats):
            m = self.members.get(i)
            if m:
                seats_payload.append(
                    {
                        "seat_idx": i,
                        "display_name": m.display_name,
                        "is_bot": m.is_bot,
                        "bot_tier": m.bot_tier,
                        "stack": m.stack,
                        "sitting_out": m.sitting_out,
                    }
                )
            else:
                seats_payload.append({"seat_idx": i, "empty": True})
        return {
            "code": self.code,
            "name": self.name,
            "sb": self.sb,
            "bb": self.bb,
            "buyin_min": self.buyin_min,
            "buyin_max": self.buyin_max,
            "max_seats": self.max_seats,
            "button_seat": self.button_seat,
            "hand_no": self.hand_no,
            "seats": seats_payload,
            "closes_at": self.closes_at.isoformat() if self.closes_at else None,
            "closed": self._closed,
            "final_standings": self.final_standings,
        }

    def public_lobby_state(self) -> dict[str, Any]:
        return {"type": "state", "room": self._base_room_payload(), "engine": None}

    async def push_state_to_all(self) -> None:
        lobby = self.public_lobby_state()
        if self._current_engine:
            eng_public = self._current_engine.public_state()
            eng_public["actor_deadline_ms"] = self._actor_deadline_ms
            eng_public["action_timeout_s"] = self._action_timeout_s
        else:
            eng_public = None
        for m in self.members.values():
            payload = {
                "type": "state",
                "room": self._base_room_payload(),
                "engine": eng_public,
                "your_hole_cards": (
                    self._current_engine.hole_cards_of(m.seat_idx)
                    if self._current_engine
                    and not m.is_bot
                    and m.seat_idx in self._current_engine.seats_by_idx
                    else None
                ),
                "your_best_hand": (
                    self._current_engine.best_hand_label(m.seat_idx)
                    if self._current_engine
                    and not m.is_bot
                    and m.seat_idx in self._current_engine.seats_by_idx
                    else None
                ),
                "your_seat_idx": m.seat_idx,
            }
            for q in m.connections:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass
        for q in self.observers:
            try:
                q.put_nowait(
                    {
                        "type": "state",
                        "room": self._base_room_payload(),
                        "engine": eng_public,
                        "your_hole_cards": None,
                        "your_best_hand": None,
                        "your_seat_idx": None,
                    }
                )
            except asyncio.QueueFull:
                pass

    # ---- game loop ----

    def start_loop(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"room-{self.code}")

    async def close(self) -> None:
        self._closed = True
        self._membership_event.set()
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        try:
            while not self._closed:
                if self._lifetime_expired():
                    await self._auto_close()
                    return
                active = [m for m in self.members.values() if not m.sitting_out]
                humans_active = [m for m in active if not m.is_bot]
                # bots only play when at least one human is active — otherwise
                # they grind against each other forever.
                if len(active) < 2 or not humans_active:
                    self._membership_event.clear()
                    await self.push_state_to_all()
                    # Wake up whenever membership changes OR the lifetime is
                    # about to expire, so idle rooms close on time.
                    timeout = self._seconds_until_close()
                    try:
                        if timeout is None:
                            await self._membership_event.wait()
                        else:
                            await asyncio.wait_for(
                                self._membership_event.wait(), timeout=max(0.1, timeout)
                            )
                    except asyncio.TimeoutError:
                        pass
                    continue
                try:
                    await self._play_hand()
                except Exception:
                    log.exception("hand loop error")
                    await asyncio.sleep(2)
                await asyncio.sleep(settings.between_hands_s)
        except asyncio.CancelledError:
            pass

    def _lifetime_expired(self) -> bool:
        return (
            self.closes_at is not None
            and datetime.now(timezone.utc) >= self.closes_at
        )

    def _seconds_until_close(self) -> float | None:
        if self.closes_at is None:
            return None
        return (self.closes_at - datetime.now(timezone.utc)).total_seconds()

    async def _auto_close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
        final = sorted(
            self.standings.values(), key=lambda v: v["net"], reverse=True
        )
        self.final_standings = final
        try:
            async with SessionLocal() as session:
                rm = await session.get(RoomModel, self.id)
                if rm:
                    rm.closed_at = datetime.now(timezone.utc)
                    rm.final_standings = final
                    await session.commit()
        except Exception:
            log.exception("failed to persist room close")
        await self._broadcast(
            {"type": "room_closed", "data": {"standings": final, "reason": "timeout"}}
        )
        # Final state push so clients see closed=True + final_standings.
        await self.push_state_to_all()

    def _active_seats(self) -> list[int]:
        return sorted(
            s for s, m in self.members.items() if not m.sitting_out and m.stack >= self.bb
        )

    def _advance_button(self, seats_in_hand: list[int]) -> None:
        if self.button_seat in seats_in_hand and len(seats_in_hand) > 1:
            idx = seats_in_hand.index(self.button_seat)
            self.button_seat = seats_in_hand[(idx + 1) % len(seats_in_hand)]
        else:
            self.button_seat = seats_in_hand[0]

    async def _play_hand(self) -> None:
        seats_in_hand = self._active_seats()
        if len(seats_in_hand) < 2:
            return
        if self.hand_no > 0:
            self._advance_button(seats_in_hand)
        else:
            self.button_seat = seats_in_hand[0]
        self.hand_no += 1

        seat_infos: list[SeatInfo] = []
        for s in seats_in_hand:
            m = self.members[s]
            seat_infos.append(
                SeatInfo(
                    seat_idx=s,
                    display_name=m.display_name,
                    stack=m.stack,
                    is_bot=m.is_bot,
                    bot_tier=m.bot_tier,
                    user_id=m.user_id,
                )
            )
        engine = HandEngine(seat_infos, self.sb, self.bb, self.button_seat)
        self._current_engine = engine
        recorder = Recorder(self.id, self.hand_no, engine, seat_infos)
        await recorder.on_hand_start()

        await self._broadcast(
            {
                "type": "event",
                "kind": "hand_start",
                "data": {"hand_no": self.hand_no, "button_seat": self.button_seat},
            }
        )
        await self.push_state_to_all()

        while not engine.is_hand_over:
            actor_seat = engine.current_actor_seat()
            if actor_seat is None:
                break
            await self.push_state_to_all()
            member = self.members.get(actor_seat)
            if member is None:
                engine.apply("fold")
                continue
            action_type, amount = await self._obtain_action(member, engine)
            pre_cards = engine.board_count()
            try:
                result: ActionResult = engine.apply(action_type, amount)
            except Exception:
                log.exception("apply action failed; auto-fold")
                result = engine.apply("fold")
            await recorder.on_action(result)
            await self._broadcast(
                {
                    "type": "event",
                    "kind": "action",
                    "data": {
                        "seat_idx": result.seat_idx,
                        "action_type": result.action_type,
                        "amount": result.amount,
                        "street": result.street,
                        "pot": result.pot_after,
                        "street_advanced": result.street_advanced,
                    },
                }
            )

            # If the action left everyone all-in, pokerkit runs out the board
            # immediately. Reveal flop/turn/river one street at a time so
            # clients see the runout as an animation.
            post_cards = engine.actual_board_count()
            if engine.went_to_showdown and not engine.status_active and post_cards > pre_cards:
                for cap in (3, 4, 5):
                    if cap <= pre_cards or cap > post_cards:
                        continue
                    engine.set_display_cap(cap)
                    await self._broadcast(
                        {
                            "type": "event",
                            "kind": "runout",
                            "data": {"stage": cap, "board": engine.board()},
                        }
                    )
                    await self.push_state_to_all()
                    await asyncio.sleep(settings.runout_stage_s)
                engine.set_display_cap(None)

        await recorder.on_hand_end()

        # Accumulate per-player running totals for the room's lifetime.
        # Key distinguishes humans (by user_id) from bots (by seat_idx).
        by_seat = {si.seat_idx: si for si in seat_infos}
        for w in engine.winner_summary():
            si = by_seat.get(w["seat_idx"])
            if not si:
                continue
            key = f"u{si.user_id}" if si.user_id is not None else f"b{si.seat_idx}"
            entry = self.standings.get(key) or {
                "display_name": si.display_name,
                "user_id": si.user_id,
                "is_bot": si.is_bot,
                "net": 0,
            }
            entry["net"] += int(w["net"])
            self.standings[key] = entry

        # sync stacks back to members
        for s in seats_in_hand:
            if s in self.members:
                self.members[s].stack = engine.stack_of(s)
                if self.members[s].stack < self.bb:
                    if self.members[s].is_bot:
                        # Broke bot vacates the seat instead of sitting out,
                        # so humans can reclaim it.
                        self.members.pop(s, None)
                        self._membership_event.set()
                    else:
                        self.members[s].sitting_out = True

        showdown_info = None
        if engine.went_to_showdown:
            showdown_info = {
                s: engine.hole_cards_of(s) for s in engine.showdown_seats()
            }
        await self._broadcast(
            {
                "type": "hand_end",
                "data": {
                    "hand_no": self.hand_no,
                    "pot_total": engine.peak_pot,
                    "winner_summary": engine.winner_summary(),
                    "board": engine.board(),
                    "showdown": showdown_info,
                },
            }
        )
        self._current_engine = None
        await self.push_state_to_all()

    async def _obtain_action(
        self, member: Member, engine: HandEngine
    ) -> tuple[str, int | None]:
        legal = engine.legal_actions()
        if member.is_bot:
            self._actor_deadline_ms = None
            await asyncio.sleep(
                random.uniform(settings.bot_think_min_s, settings.bot_think_max_s)
            )
            decision = make_bot(member.bot_tier or "regular").decide(engine, member.seat_idx)
            return decision.action, decision.amount

        # human: wait on queue
        self._actor_deadline_ms = int((time.time() + self._action_timeout_s) * 1000)
        await self.push_state_to_all()
        member.action_channel = asyncio.Queue(maxsize=1)
        try:
            try:
                action = await asyncio.wait_for(
                    member.action_channel.get(), timeout=settings.action_timeout_s
                )
            except asyncio.TimeoutError:
                # auto: check if possible, else fold
                if legal.can_check:
                    return "check", None
                return "fold", None
        finally:
            member.action_channel = None
            self._actor_deadline_ms = None

        return action["action"], action.get("amount")

    async def submit_action(self, seat_idx: int, action: dict) -> bool:
        m = self.members.get(seat_idx)
        if m is None or m.action_channel is None:
            return False
        try:
            m.action_channel.put_nowait(action)
            return True
        except asyncio.QueueFull:
            return False
