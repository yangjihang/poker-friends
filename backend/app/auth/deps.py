from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_token
from app.db import get_session
from app.models import User


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = await session.scalar(select(User).where(User.id == int(payload["sub"])))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    # 密码版本对不上（改密/重置后的旧 token），作废。
    token_pv = int(payload.get("pv", 0))
    if token_pv != (user.password_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired (password changed)")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
