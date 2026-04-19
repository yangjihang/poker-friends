"""Admin account bootstrap: ensure an admin user exists at startup."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.auth.security import hash_password
from app.config import get_settings
from app.db import SessionLocal
from app.models import User

log = logging.getLogger(__name__)


async def ensure_admin_user() -> None:
    s = get_settings()
    if not s.admin_username or not s.admin_password:
        return
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.username == s.admin_username)
        )
        if existing:
            # 存在就强制确保 is_admin=True，但不覆盖密码。
            if not existing.is_admin:
                existing.is_admin = True
                await session.commit()
                log.info("promoted existing user %s to admin", s.admin_username)
            return
        user = User(
            username=s.admin_username,
            password_hash=hash_password(s.admin_password),
            display_name=s.admin_display_name,
            balance=0,
            is_admin=True,
        )
        session.add(user)
        await session.commit()
        log.info("bootstrapped admin user %s", s.admin_username)
