from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 所有资金变动都走这张表，只追加。
# type 约定：
#   register_bonus  注册奖励（+）
#   admin_topup     管理员充值（+/-）
#   buyin_lock      入桌/补带质押（-）
#   room_cashout    房间关闭结算（+）
class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(BigInteger)  # 有符号：正=入账，负=扣款
    balance_after: Mapped[int] = mapped_column(BigInteger)
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    hand_id: Mapped[int | None] = mapped_column(ForeignKey("hands.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # room_cashout_pending 类型专用：admin 确认人工补偿后设置。其他类型始终为 NULL。
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("ix_ledger_user_created", "user_id", "created_at"),)
