"""In-memory Room: seats, connections, game loop."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.bank import adjust_balance
from app.config import get_settings
from app.db import SessionLocal
from app.game.bots import make_bot
from app.game.engine import ActionResult, HandEngine, SeatInfo
from app.game.membership import mark_left as _member_mark_left
from app.game.membership import update_stack as _member_update_stack
from app.game.recorder import Recorder
from app.models import LedgerEntry
from app.models import Room as RoomModel
from app.models import User

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
        created_by: int,
        max_seats: int = 9,
        allow_guest: bool = False,
    ):
        self.id = id
        self.code = code
        self.name = name
        self.sb = sb
        self.bb = bb
        self.buyin_min = buyin_min
        self.buyin_max = buyin_max
        self.created_by = created_by
        self.max_seats = max_seats
        self.allow_guest = allow_guest

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
        # 已离桌玩家的待结算 stack 累计（user_id -> amount）。
        # 关桌时把在座玩家的 stack 也合进来，然后统一写 ledger + 回 bank。
        self.cashouts: dict[int, int] = {}
        self._cashouts_done: bool = False
        # 崩溃止损相关：上次 RoomMember snapshot 成功的 hand_no，用于观察偏差
        self._last_snapshot_hand_no: int = 0
        self._snapshot_fail_streak: int = 0
        # 上一手被清退的 bot 座位：_play_hand 结尾不立即 pop，延后到下一手开始前，
        # 这样 showdown 阶段玩家仍能看到它们的底牌与 0 筹码。
        self._pending_broke_bot_seats: list[int] = []
        # 观战模式：没人类坐下但有 observer 时 bot 继续打并加速；为 False 时常规节奏。
        self._fast_mode: bool = False

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
            # 累加后不能超过 buyin_max，否则会绕过房间上限
            if m.stack + buyin > self.buyin_max:
                raise ValueError(
                    f"stack {m.stack} + buyin {buyin} 超过 buyin_max {self.buyin_max}"
                )
            m.stack += buyin
            m.sitting_out = False
            self._membership_event.set()

    async def stand_up(self, seat_idx: int) -> int:
        """离桌。返回捕获到的 stack（用于调试/日志）。

        人类离桌：stack 立即回灌 bank + 写 ledger，用户可以马上去别的桌 buyin。
        DB 写失败会写一条 pending 审计 ledger + 暂存 self.cashouts，等关桌时重试。
        手牌进行中：从 engine 取实时 stack，动作队列塞 fold 立即解除等待。
        bot 离桌：丢弃 stack，不走 bank。

        注意：DB I/O 不在 self._lock 内，避免阻塞游戏循环/其他 sit/rebuy。
        """
        # ---- 阶段 1：锁内修改内存状态，拿到 stack 和 user_id ----
        async with self._lock:
            m = self.members.get(seat_idx)
            if not m:
                return 0
            stack = m.stack
            if self._current_engine and seat_idx in self._current_engine.seats_by_idx:
                try:
                    stack = int(self._current_engine.stack_of(seat_idx))
                except Exception:
                    stack = m.stack
            user_id = m.user_id
            self.members.pop(seat_idx, None)
            if m.action_channel is not None:
                try:
                    m.action_channel.put_nowait({"action": "fold"})
                except asyncio.QueueFull:
                    pass
            self._membership_event.set()

        # ---- 阶段 2：锁外做 DB 结算 + RoomMember mark_left ----
        if user_id is None:
            return stack
        settled = False
        try:
            async with SessionLocal() as session:
                # RoomMember 标记离桌（就算 stack=0 也要标，方便审计）
                await _member_mark_left(
                    session, room_id=self.id, user_id=user_id, final_stack=stack
                )
                if stack > 0:
                    u = await session.scalar(
                        select(User).where(User.id == user_id).with_for_update()
                    )
                    if u:
                        await adjust_balance(
                            session, user=u, amount=stack, type="room_cashout",
                            room_id=self.id, note=f"离桌 {self.code}",
                        )
                await session.commit()
                settled = True
        except Exception:
            log.exception("stand settle failed for user %d", user_id)
        # stack=0 的情况不做兜底（没钱可补）
        if stack <= 0:
            return stack
        if not settled:
            # 兜底 1：内存记录，关桌时重试
            async with self._lock:
                self.cashouts[user_id] = self.cashouts.get(user_id, 0) + stack
            # 兜底 2：尽可能落一条审计痕迹，方便事后对账
            try:
                async with SessionLocal() as session:
                    session.add(
                        LedgerEntry(
                            user_id=user_id,
                            type="room_cashout_pending",
                            amount=stack,
                            balance_after=0,
                            room_id=self.id,
                            note=f"离桌 {self.code} 结算失败，待关桌重试",
                        )
                    )
                    await session.commit()
            except Exception:
                log.exception("pending ledger write also failed")
        return stack

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
            "created_by": self.created_by,
            "button_seat": self.button_seat,
            "hand_no": self.hand_no,
            "seats": seats_payload,
            "closes_at": self.closes_at.isoformat() if self.closes_at else None,
            "closed": self._closed,
            "final_standings": self.final_standings,
            "allow_guest": self.allow_guest,
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
        # 进程停机前尽量把筹码回灌到 bank（幂等）。
        try:
            await self._finalize_cashouts()
        except Exception:
            log.exception("cashout on close failed for room %s", self.code)

    async def _run(self) -> None:
        try:
            while not self._closed:
                if self._lifetime_expired():
                    await self._auto_close()
                    return
                # 清理上一手延迟 pop 的 broke bot（_play_hand 结尾只打标记，
                # 给 showdown 时间展示其底牌）
                if self._pending_broke_bot_seats:
                    for s in self._pending_broke_bot_seats:
                        self.members.pop(s, None)
                    self._pending_broke_bot_seats.clear()
                    self._membership_event.set()
                    await self.push_state_to_all()
                active = [m for m in self.members.values() if not m.sitting_out]
                humans_active = [m for m in active if not m.is_bot]
                # 常态：至少一个活跃人类才打。无人类但有 observer 时进入"观战
                # 模式"：bot 之间继续对打并加速；完全无人看时仍挂起节省 CPU。
                if len(active) < 2:
                    can_play = False
                elif humans_active:
                    can_play = True
                    self._fast_mode = False
                elif self.observers:
                    can_play = True
                    self._fast_mode = True
                else:
                    can_play = False
                if not can_play:
                    self._fast_mode = False
                    self._membership_event.clear()
                    await self.push_state_to_all()
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
                await asyncio.sleep(settings.between_hands_s * self._speed_factor())
        except asyncio.CancelledError:
            pass

    def _speed_factor(self) -> float:
        return settings.spectate_speed_factor if self._fast_mode else 1.0

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
        await self._finalize_cashouts()
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

    async def _finalize_cashouts(self) -> None:
        """关桌/进程停机时统一结算：把所有在座 member 的 stack 以及 cashouts 里的
        待结算金额全部回灌到对应 user 的 bank，并写 ledger。幂等（只会结算一次）。
        """
        async with self._lock:
            if self._cashouts_done:
                return
            self._cashouts_done = True
            # 把还在座的人的 stack 也合进 cashouts。
            for m in self.members.values():
                if m.user_id is not None and m.stack > 0:
                    self.cashouts[m.user_id] = self.cashouts.get(m.user_id, 0) + m.stack
            pending = {uid: amt for uid, amt in self.cashouts.items() if amt > 0}
            self.cashouts.clear()
        if not pending:
            return
        try:
            async with SessionLocal() as session:
                for user_id, amount in pending.items():
                    u = await session.scalar(
                        select(User).where(User.id == user_id).with_for_update()
                    )
                    if not u:
                        continue
                    await adjust_balance(
                        session,
                        user=u,
                        amount=amount,
                        type="room_cashout",
                        room_id=self.id,
                        note=f"房间 {self.code} 关闭结算",
                    )
                await session.commit()
        except Exception:
            log.exception("failed to settle cashouts for room %s", self.code)

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
                    await asyncio.sleep(settings.runout_stage_s * self._speed_factor())
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
        human_updates: list[tuple[int, int]] = []  # (user_id, stack)
        for s in seats_in_hand:
            if s in self.members:
                self.members[s].stack = engine.stack_of(s)
                if self.members[s].user_id is not None:
                    human_updates.append((self.members[s].user_id, self.members[s].stack))
                if self.members[s].stack < self.bb:
                    if self.members[s].is_bot:
                        # 延后 pop：showdown 期间要能看见被清退 bot 的底牌。
                        # _run 循环下一次迭代前统一清掉。
                        self._pending_broke_bot_seats.append(s)
                    else:
                        self.members[s].sitting_out = True

        # 每手末把人类玩家最新 stack 刷到 RoomMember（崩溃止损）
        if human_updates:
            try:
                async with SessionLocal() as session:
                    for uid, stk in human_updates:
                        await _member_update_stack(
                            session, room_id=self.id, user_id=uid, stack=stk
                        )
                    await session.commit()
                self._last_snapshot_hand_no = self.hand_no
                self._snapshot_fail_streak = 0
            except Exception:
                self._snapshot_fail_streak += 1
                behind = self.hand_no - self._last_snapshot_hand_no
                log.exception(
                    "hand_end member snapshot failed (fail_streak=%d, behind=%d hands)",
                    self._snapshot_fail_streak, behind,
                )
                # 连续失败 5 手或落后 10 手以上，打 WARN（运维可按日志 grep 告警）
                if self._snapshot_fail_streak >= 5 or behind >= 10:
                    log.warning(
                        "room %s RoomMember snapshot stale: last_ok_hand=%d, current=%d",
                        self.code, self._last_snapshot_hand_no, self.hand_no,
                    )

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
            factor = self._speed_factor()
            await asyncio.sleep(
                random.uniform(
                    settings.bot_think_min_s * factor,
                    settings.bot_think_max_s * factor,
                )
            )
            decision = make_bot(member.bot_tier or "patron").decide(engine, member.seat_idx)
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
