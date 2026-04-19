"""Bank / ledger helpers: 所有资金变动都经过这里。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LedgerEntry, User


async def adjust_balance(
    session: AsyncSession,
    *,
    user: User,
    amount: int,
    type: str,
    room_id: int | None = None,
    hand_id: int | None = None,
    note: str | None = None,
    actor_user_id: int | None = None,
) -> LedgerEntry:
    """Mutate `user.balance` by `amount` and append a ledger entry.

    Caller is responsible for committing the session. Raises ValueError if
    the resulting balance would go negative (configurable via `allow_negative`
    later if we ever need it).
    """
    new_balance = user.balance + amount
    if new_balance < 0:
        raise ValueError(f"insufficient balance: have {user.balance}, need {-amount}")
    user.balance = new_balance
    entry = LedgerEntry(
        user_id=user.id,
        type=type,
        amount=amount,
        balance_after=new_balance,
        room_id=room_id,
        hand_id=hand_id,
        note=note,
        actor_user_id=actor_user_id,
    )
    session.add(entry)
    return entry
