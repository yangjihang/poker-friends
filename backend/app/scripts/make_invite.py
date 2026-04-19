"""CLI：批量生成邀请码。

用法：
    python -m app.scripts.make_invite              # 生成 1 个
    python -m app.scripts.make_invite --count 5    # 生成 5 个

前置条件：数据库 + 表已存在（至少跑过一次应用或 `python -c 'from app.db import init_models; ...'`）。

生成后打印到 stdout，每行一个码。
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, init_models
from app.models import InviteCode

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I/O/0/1
_LENGTH = 8


def _gen_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))


async def _make(count: int) -> list[str]:
    # 空库也能跑：确保表已存在。
    await init_models()
    codes: list[str] = []
    async with SessionLocal() as session:
        attempts = 0
        while len(codes) < count and attempts < count * 10:
            attempts += 1
            code = _gen_code()
            session.add(InviteCode(code=code))
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                continue
            codes.append(code)
        await session.commit()
    return codes


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate invite codes.")
    ap.add_argument("--count", "-n", type=int, default=1)
    args = ap.parse_args()
    if args.count < 1 or args.count > 500:
        raise SystemExit("count must be 1..500")
    codes = asyncio.run(_make(args.count))
    for c in codes:
        print(c)


if __name__ == "__main__":
    main()
