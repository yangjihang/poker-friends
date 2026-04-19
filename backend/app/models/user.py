from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(64))
    # 银行余额：注册奖励 + 充值 + 桌面结算，减 sit/rebuy 质押。单位=筹码。
    # BigInteger 避免 INT4 溢出（admin topup 手滑输大数字）。
    balance: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 密码版本号：change_password / reset_password 后 +1，JWT 带这个字段，
    # 不匹配就视作失效，达到改密后旧 token 失效的效果。
    password_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
