from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.game.engine import HandEngine


@dataclass(frozen=True)
class BotDecision:
    action: str  # fold/check/call/raise
    amount: int | None = None


class Bot(Protocol):
    tier: str

    def decide(self, engine: HandEngine, seat_idx: int) -> BotDecision: ...
