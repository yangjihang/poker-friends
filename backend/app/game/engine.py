"""Thin wrapper over PokerKit's NoLimitTexasHoldem state.

The wrapper hides PokerKit's verbose API and exposes a seat-centric interface
(seats 0..max_seats-1 with sparse occupancy). Each started hand maps seated
seats to PokerKit player indices in blind order: player 0 is SB (or BTN in
heads-up), then BB, then UTG..BTN.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pokerkit import Automation, Card, NoLimitTexasHoldem, StandardHighHand

ActionType = Literal["fold", "check", "call", "bet", "raise"]

STREETS = ("preflop", "flop", "turn", "river")

_HAND_LABEL_CN = {
    "Straight flush": "同花顺",
    "Four of a kind": "四条",
    "Full house": "葫芦",
    "Flush": "同花",
    "Straight": "顺子",
    "Three of a kind": "三条",
    "Two pair": "两对",
    "One pair": "一对",
    "High card": "高牌",
}

_RANK_CN = {
    "A": "A", "K": "K", "Q": "Q", "J": "J", "T": "10",
    "9": "9", "8": "8", "7": "7", "6": "6",
    "5": "5", "4": "4", "3": "3", "2": "2",
}


@dataclass(frozen=True)
class SeatInfo:
    seat_idx: int
    display_name: str
    stack: int
    is_bot: bool = False
    bot_tier: str | None = None
    user_id: int | None = None


@dataclass(frozen=True)
class LegalActions:
    can_fold: bool
    can_check: bool
    can_call: bool
    call_amount: int
    can_raise: bool
    min_raise_to: int
    max_raise_to: int


@dataclass(frozen=True)
class ActionResult:
    seat_idx: int
    action_type: ActionType
    amount: int | None
    street: str
    stack_after: int
    pot_after: int
    street_advanced: bool
    hand_over: bool


def _card_str(c: Card) -> str:
    return repr(c)  # short form like "7d"


class HandEngine:
    """Encapsulates a single hand. Create a new instance per hand."""

    def __init__(self, seats: list[SeatInfo], sb: int, bb: int, button_seat: int):
        if len(seats) < 2:
            raise ValueError("need >=2 seats to start a hand")
        self.seats_by_idx: dict[int, SeatInfo] = {s.seat_idx: s for s in seats}
        self.sb = sb
        self.bb = bb
        self.button_seat = button_seat

        # Order seats in blind order starting from SB. Heads-up special: BTN is SB.
        ordered = self._blind_order(seats, button_seat)
        self._player_to_seat: list[int] = [s.seat_idx for s in ordered]
        self._seat_to_player: dict[int, int] = {
            seat_idx: i for i, seat_idx in enumerate(self._player_to_seat)
        }

        stacks = tuple(s.stack for s in ordered)
        self._state = NoLimitTexasHoldem.create_state(
            (
                Automation.ANTE_POSTING,
                Automation.BET_COLLECTION,
                Automation.BLIND_OR_STRADDLE_POSTING,
                Automation.CARD_BURNING,
                Automation.HOLE_DEALING,
                Automation.BOARD_DEALING,
                Automation.HAND_KILLING,
                Automation.CHIPS_PUSHING,
                Automation.CHIPS_PULLING,
                Automation.RUNOUT_COUNT_SELECTION,
                Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
            ),
            True,           # uniform_antes
            {-1: 0},        # no antes
            (sb, bb),       # blinds
            bb,             # min bet
            stacks,
            len(stacks),
        )
        self._last_street_index = self._state.street_index
        self._action_seq = 0
        self._folded_seats: set[int] = set()
        self._peak_pot = int(self._state.total_pot_amount)
        # When set (3/4/5), caps how many board cards public snapshots expose —
        # used to animate runouts after all-ins so clients see flop/turn/river
        # appear in sequence rather than all at once.
        self._display_cap_cards: int | None = None
        # PokerKit may muck losers' hole cards at showdown, so snapshot now.
        self._hole_cache: dict[int, list[str]] = {
            seat_idx: [_card_str(c) for c in self._state.hole_cards[pi]]
            for pi, seat_idx in enumerate(self._player_to_seat)
        }

    @staticmethod
    def _blind_order(seats: list[SeatInfo], button_seat: int) -> list[SeatInfo]:
        by_seat = sorted(seats, key=lambda s: s.seat_idx)
        idxs = [s.seat_idx for s in by_seat]
        if button_seat not in idxs:
            button_seat = idxs[0]
        btn_pos = idxs.index(button_seat)
        n = len(by_seat)
        if n == 2:
            # heads-up: BTN posts SB, other posts BB
            return [by_seat[btn_pos], by_seat[(btn_pos + 1) % n]]
        # multi-way: SB = next after BTN, BB = next, then UTG..BTN
        rotated = by_seat[btn_pos + 1 :] + by_seat[: btn_pos + 1]
        return rotated

    # -- queries --

    def current_actor_seat(self) -> int | None:
        pi = self._state.actor_index
        if pi is None:
            return None
        return self._player_to_seat[pi]

    @property
    def is_hand_over(self) -> bool:
        if self._display_cap_cards is not None and self._display_cap_cards < self._actual_board_count():
            # Runout pacing: suppress hand_over until every street has been
            # revealed to clients.
            return False
        return not self._state.status

    @property
    def street(self) -> str:
        if self._display_cap_cards is not None:
            # 0→preflop, 3→flop, 4→turn, 5→river
            mapping = {0: 0, 3: 1, 4: 2, 5: 3}
            return STREETS[mapping.get(self._display_cap_cards, 3)]
        idx = max(0, min(self._state.street_index or 0, len(STREETS) - 1))
        return STREETS[idx]

    def _actual_board_count(self) -> int:
        return sum(1 for _ in self._state.get_board_cards(0))

    def actual_board_count(self) -> int:
        """Total board cards currently dealt in pokerkit (ignores display cap)."""
        return self._actual_board_count()

    @property
    def status_active(self) -> bool:
        """True while pokerkit considers the hand still in progress."""
        return bool(self._state.status)

    def board_count(self) -> int:
        """Number of board cards currently exposed to clients (respects cap)."""
        n = self._actual_board_count()
        if self._display_cap_cards is not None:
            return min(n, self._display_cap_cards)
        return n

    def set_display_cap(self, cards: int | None) -> None:
        self._display_cap_cards = cards

    @property
    def total_pot(self) -> int:
        v = int(self._state.total_pot_amount)
        if v > self._peak_pot:
            self._peak_pot = v
        return v

    @property
    def peak_pot(self) -> int:
        return self._peak_pot

    def stack_of(self, seat_idx: int) -> int:
        pi = self._seat_to_player[seat_idx]
        return int(self._state.stacks[pi])

    def bet_of(self, seat_idx: int) -> int:
        pi = self._seat_to_player[seat_idx]
        return int(self._state.bets[pi])

    def is_folded(self, seat_idx: int) -> bool:
        return seat_idx in self._folded_seats

    def hole_cards_of(self, seat_idx: int) -> list[str]:
        return list(self._hole_cache.get(seat_idx, []))

    def board(self) -> dict[str, list[str] | str | None]:
        """Return board grouped by street (respects display cap for pacing)."""
        flat = [_card_str(c) for c in self._state.get_board_cards(0)]
        if self._display_cap_cards is not None:
            flat = flat[: self._display_cap_cards]
        result: dict[str, list[str] | str | None] = {
            "flop": flat[:3] if len(flat) >= 3 else [],
            "turn": flat[3] if len(flat) >= 4 else None,
            "river": flat[4] if len(flat) >= 5 else None,
        }
        return result

    def best_hand_label(self, seat_idx: int) -> str | None:
        """Chinese label for the player's current best 5-card hand.

        Preflop (<3 board cards) only detects pocket pairs; otherwise delegates
        to pokerkit's evaluator.
        """
        hole = self.hole_cards_of(seat_idx)
        if len(hole) < 2:
            return None
        board = self.board()
        flat = [*(board.get("flop") or [])]
        if board.get("turn"):
            flat.append(board["turn"])
        if board.get("river"):
            flat.append(board["river"])
        if len(flat) < 3:
            if hole[0][0] == hole[1][0]:
                return f"口袋对 {_RANK_CN.get(hole[0][0], hole[0][0])}"
            return None
        try:
            h = StandardHighHand.from_game(
                tuple(Card.parse("".join(hole))),
                tuple(Card.parse("".join(flat))),
            )
        except Exception:
            return None
        label = h.entry.label.value
        # Distinguish royal flush from other straight flushes.
        if label == "Straight flush":
            ranks = [repr(c)[0] for c in h.cards]
            if "A" in ranks and "K" in ranks and "T" in ranks:
                return "皇家同花顺"
        return _HAND_LABEL_CN.get(label, label)

    def legal_actions(self) -> LegalActions:
        s = self._state
        can_fold = bool(s.can_fold())
        can_check_or_call = bool(s.can_check_or_call())
        call_amount = int(s.checking_or_calling_amount or 0)
        can_check = can_check_or_call and call_amount == 0
        can_call = can_check_or_call and call_amount > 0
        can_raise = bool(s.can_complete_bet_or_raise_to())
        min_raise = int(s.min_completion_betting_or_raising_to_amount or 0)
        max_raise = int(s.max_completion_betting_or_raising_to_amount or 0)
        return LegalActions(
            can_fold=can_fold,
            can_check=can_check,
            can_call=can_call,
            call_amount=call_amount,
            can_raise=can_raise,
            min_raise_to=min_raise,
            max_raise_to=max_raise,
        )

    # -- mutations --

    def apply(self, action_type: ActionType, amount: int | None = None) -> ActionResult:
        seat_idx = self.current_actor_seat()
        if seat_idx is None:
            raise RuntimeError("no actor to act")

        pre_street_idx = self._state.street_index
        pre_pot = int(self._state.total_pot_amount)

        if action_type == "fold":
            self._state.fold()
            self._folded_seats.add(seat_idx)
            amount_out: int | None = None
        elif action_type in ("check", "call"):
            self._state.check_or_call()
            amount_out = int(self._state.bets[self._seat_to_player[seat_idx]])
            if action_type == "check":
                amount_out = None
        elif action_type in ("bet", "raise"):
            if amount is None:
                raise ValueError("raise/bet requires amount")
            self._state.complete_bet_or_raise_to(amount)
            amount_out = amount
        else:
            raise ValueError(f"unknown action_type: {action_type}")

        self._action_seq += 1
        new_street_idx = self._state.street_index
        street_advanced = new_street_idx != pre_street_idx
        self._last_street_index = new_street_idx

        # If the action ended the hand, chip pushing automation has zeroed
        # total_pot — fall back to pre-action pot so the row is meaningful.
        pot_after = max(self.total_pot, pre_pot)
        return ActionResult(
            seat_idx=seat_idx,
            action_type=action_type,
            amount=amount_out,
            street=STREETS[max(0, min(pre_street_idx or 0, len(STREETS) - 1))],
            stack_after=self.stack_of(seat_idx),
            pot_after=pot_after,
            street_advanced=street_advanced,
            hand_over=self.is_hand_over,
        )

    # -- summary --

    def winner_summary(self) -> list[dict[str, int]]:
        """Net result per seat (positive = won, negative = lost)."""
        result = []
        for pi, payoff in enumerate(self._state.payoffs):
            seat_idx = self._player_to_seat[pi]
            result.append({"seat_idx": seat_idx, "net": int(payoff)})
        return result

    def showdown_seats(self) -> list[int]:
        """Seats that went to showdown (reached hand-over without folding)."""
        return [s for s in self._player_to_seat if s not in self._folded_seats]

    @property
    def went_to_showdown(self) -> bool:
        """True if 2+ players reached hand-over without folding."""
        return len(self.showdown_seats()) >= 2

    def public_state(self) -> dict:
        """Snapshot safe to broadcast to everyone (no hole cards)."""
        actor_seat = self.current_actor_seat()
        seats_payload = []
        for pi, seat_idx in enumerate(self._player_to_seat):
            info = self.seats_by_idx[seat_idx]
            seats_payload.append(
                {
                    "seat_idx": seat_idx,
                    "display_name": info.display_name,
                    "is_bot": info.is_bot,
                    "bot_tier": info.bot_tier,
                    "stack": int(self._state.stacks[pi]),
                    "bet": int(self._state.bets[pi]),
                    "folded": seat_idx in self._folded_seats,
                    "is_actor": seat_idx == actor_seat,
                }
            )
        legal = self.legal_actions() if actor_seat is not None else None
        return {
            "street": self.street,
            "button_seat": self.button_seat,
            "pot": self.total_pot,
            "board": self.board(),
            "seats": seats_payload,
            "actor_seat": actor_seat,
            "legal": (
                {
                    "can_fold": legal.can_fold,
                    "can_check": legal.can_check,
                    "can_call": legal.can_call,
                    "call_amount": legal.call_amount,
                    "can_raise": legal.can_raise,
                    "min_raise_to": legal.min_raise_to,
                    "max_raise_to": legal.max_raise_to,
                }
                if legal
                else None
            ),
            "hand_over": self.is_hand_over,
        }
