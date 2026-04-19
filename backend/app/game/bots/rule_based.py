"""Rule-based poker bots, 3 difficulty tiers.

Rookie is random; Patron plays a simple value-oriented strategy; Pro currently
reuses Patron's strategy and will be upgraded later with Monte Carlo equity.
"""

from __future__ import annotations

import random

from app.game.bots.base import Bot, BotDecision
from app.game.engine import HandEngine

TIERS = ("rookie", "patron", "pro")


_RANK_VAL = {r: i for i, r in enumerate("23456789TJQKA", start=2)}


def _hand_category(hole: list[str]) -> str:
    """Crude preflop strength bucket."""
    if len(hole) != 2:
        return "trash"
    r1, s1 = hole[0][0], hole[0][1]
    r2, s2 = hole[1][0], hole[1][1]
    v1, v2 = _RANK_VAL[r1], _RANK_VAL[r2]
    hi, lo = max(v1, v2), min(v1, v2)
    suited = s1 == s2
    pair = v1 == v2

    if pair and hi >= 10:
        return "premium"            # TT+
    if hi == 14 and lo >= 13:
        return "premium"            # AK
    if pair and hi >= 7:
        return "strong"             # 77-99
    if hi == 14 and lo >= 11:
        return "strong"             # AJ+
    if suited and hi >= 13 and lo >= 10:
        return "strong"             # KTs+, KJs, KQs
    if pair:
        return "speculative"        # small pairs
    if suited and hi - lo == 1 and lo >= 5:
        return "speculative"        # 54s..T9s suited connectors
    if suited and hi == 14:
        return "speculative"        # suited aces
    if hi >= 12 and lo >= 10:
        return "speculative"        # broadway offsuit
    return "trash"


def _made_hand_on_board(hole: list[str], board_flat: list[str]) -> int:
    """Return a crude strength score 0..4 on the flop/turn/river.
    0=air, 1=weak pair, 2=top pair, 3=two pair+, 4=strong (set/str/flush+).
    """
    if not board_flat:
        return 0
    ranks = [c[0] for c in hole]
    suits = [c[1] for c in hole]
    board_ranks = [c[0] for c in board_flat]
    board_suits = [c[1] for c in board_flat]

    hole_vals = sorted((_RANK_VAL[r] for r in ranks), reverse=True)
    board_vals = sorted((_RANK_VAL[r] for r in board_ranks), reverse=True)

    all_ranks = ranks + board_ranks
    all_suits = suits + board_suits

    # pair check
    same = set(ranks) & set(board_ranks)
    if ranks[0] == ranks[1] and _RANK_VAL[ranks[0]] > (board_vals[0] if board_vals else 0):
        return 4                    # overpair
    if ranks[0] == ranks[1]:
        return 3                    # underpair/set-chance → treat as decent
    if same:
        hit_rank = max(_RANK_VAL[r] for r in same)
        if hit_rank >= board_vals[0]:
            return 2                # top pair
        return 1                    # weaker pair

    # two pair on board with hole kicker?
    # flush potential
    suit_counts: dict[str, int] = {}
    for s in all_suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1
    if max(suit_counts.values()) >= 5:
        return 4

    # straight potential rough
    unique_vals = sorted(set(_RANK_VAL[r] for r in all_ranks))
    run = 1
    for i in range(1, len(unique_vals)):
        if unique_vals[i] == unique_vals[i - 1] + 1:
            run += 1
            if run >= 5:
                return 4
        else:
            run = 1

    return 0


def _clamp_raise(engine: HandEngine, target: int) -> int:
    la = engine.legal_actions()
    if not la.can_raise:
        raise RuntimeError("cannot raise")
    return max(la.min_raise_to, min(target, la.max_raise_to))


class RookieBot:
    tier = "rookie"

    def decide(self, engine: HandEngine, seat_idx: int) -> BotDecision:
        la = engine.legal_actions()
        roll = random.random()
        if la.can_check and roll < 0.9:
            return BotDecision("check")
        if la.can_call and roll < 0.65:
            return BotDecision("call")
        if la.can_fold and roll < 0.95:
            return BotDecision("fold")
        if la.can_raise:
            return BotDecision("raise", _clamp_raise(engine, la.min_raise_to))
        if la.can_check:
            return BotDecision("check")
        if la.can_call:
            return BotDecision("call")
        return BotDecision("fold")


class PatronBot:
    tier = "patron"

    def decide(self, engine: HandEngine, seat_idx: int) -> BotDecision:
        la = engine.legal_actions()
        hole = engine.hole_cards_of(seat_idx)
        board = engine.board()
        flat = list(board.get("flop") or [])
        if board.get("turn"):
            flat.append(board["turn"])
        if board.get("river"):
            flat.append(board["river"])

        if engine.street == "preflop":
            cat = _hand_category(hole)
            if cat == "premium":
                if la.can_raise:
                    return BotDecision("raise", _clamp_raise(engine, 3 * engine.bb + la.call_amount))
                if la.can_call:
                    return BotDecision("call")
            if cat == "strong":
                if la.can_call and la.call_amount <= 3 * engine.bb:
                    return BotDecision("call")
                if la.can_check:
                    return BotDecision("check")
                return BotDecision("fold") if la.can_fold else BotDecision("call")
            if cat == "speculative":
                if la.can_check:
                    return BotDecision("check")
                if la.can_call and la.call_amount <= engine.bb * 2:
                    return BotDecision("call")
                return BotDecision("fold") if la.can_fold else BotDecision("call")
            # trash
            if la.can_check:
                return BotDecision("check")
            return BotDecision("fold") if la.can_fold else BotDecision("call")

        # postflop
        strength = _made_hand_on_board(hole, flat)
        pot = engine.total_pot
        if strength >= 3:
            if la.can_raise:
                return BotDecision("raise", _clamp_raise(engine, max(engine.bb, pot // 2 + la.call_amount)))
            if la.can_call:
                return BotDecision("call")
        if strength == 2:
            if la.can_check:
                return BotDecision("check") if random.random() < 0.5 else (
                    BotDecision("raise", _clamp_raise(engine, max(engine.bb, pot // 2))) if la.can_raise else BotDecision("check")
                )
            if la.can_call and la.call_amount <= pot // 2:
                return BotDecision("call")
            return BotDecision("fold") if la.can_fold else BotDecision("call")
        if strength == 1:
            if la.can_check:
                return BotDecision("check")
            if la.can_call and la.call_amount <= pot // 4:
                return BotDecision("call")
            return BotDecision("fold") if la.can_fold else BotDecision("call")
        # air
        if la.can_check:
            return BotDecision("check")
        return BotDecision("fold") if la.can_fold else BotDecision("call")


class ProBot(PatronBot):
    tier = "pro"


_REGISTRY: dict[str, type[Bot]] = {
    "rookie": RookieBot,
    "patron": PatronBot,
    "pro": ProBot,
}


def make_bot(tier: str) -> Bot:
    cls = _REGISTRY.get(tier, PatronBot)
    return cls()
