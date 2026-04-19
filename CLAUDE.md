# 项目速查（给 Claude 看的）

自托管 NLHE 德州扑克。FastAPI + SQLAlchemy async + PostgreSQL + pokerkit / Vite + React + TS + Tailwind。面向朋友局 MVP，不是生产级产品。

## 常用命令

```bash
# 后端（用户 .venv 已存在于 backend/.venv）
cd backend && source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd frontend && npm run dev     # 已是 --host 0.0.0.0，局域网可连

# 查端口是否起着
lsof -iTCP -sTCP:LISTEN -P -n | grep -E ':(5173|8000)'

# 类型检查（写完 TS 改动必跑）
cd frontend && npx tsc --noEmit

# 生成邀请码（新人注册必须要）
python -m app.scripts.make_invite --count 5

# 直接查 DB 数据（用户习惯用这种 inline 脚本）
cd backend && source .venv/bin/activate && python3 -c "
import asyncio
from app.db import SessionLocal
from app.models import Hand
from sqlalchemy import select, desc
async def main():
    async with SessionLocal() as s:
        for h in (await s.scalars(select(Hand).order_by(desc(Hand.id)).limit(5))).all():
            print(h.id, h.hand_no, h.seats)
asyncio.run(main())
"
```

## 架构关键点

- **`Room`**（`backend/app/game/room.py`）：in-memory 单房间状态 + asyncio 游戏循环（`_run` / `_play_hand`）。成员 `members: dict[seat_idx, Member]`，每个 Member 有多个 WS 连接队列。游戏循环不在 `_lock` 内；`_lock` 只保护 sit/stand/rebuy/add_bot/_auto_close 这种成员变更。
- **`HandEngine`**（`backend/app/game/engine.py`）：每手一个实例，薄包装 pokerkit 的 `NoLimitTexasHoldem`。座位用稀疏 seat_idx，内部按 blind order 映射到 pokerkit 的 player index。
- **`Recorder`**（`backend/app/game/recorder.py`）：纯写入，异常只 log 不 raise，保证 DB 挂了游戏循环还能跑。每手写 `hands.user_ids`（INT[] + GIN 索引）供 my_hands 用。
- **Bank / Ledger**（`backend/app/bank.py`）：所有资金变动走 `adjust_balance`，必写 `ledger_entries` 行。type 约定：`register_bonus` / `buyin_lock` / `room_cashout` / `room_cashout_pending` / `admin_topup`。余额字段是 `BigInteger`。
- **RoomMember 快照**（`backend/app/game/membership.py`）：sit / rebuy / hand_end / stand 时 upsert `room_members` 行，进程崩溃时 DB 里有最后已知 stack 供 admin 对账。
- **Admin 权限**：`backend/app/auth/deps.py` 的 `require_admin` 依赖保护所有 `/api/admin/*`。第一个 admin 靠 env `ADMIN_USERNAME` + `ADMIN_PASSWORD` 在 lifespan 里 bootstrap。
- **WS 消息类型**（`type` 字段）：`state`（全量快照）、`event`（hand_start / action / runout）、`hand_end`、`room_closed`、`chat`、`balance_update`（sit/rebuy/stand 后推）、`error`。`state` 里 `your_hole_cards` / `your_best_hand` 是 per-member 的，其他人看不到。
- **监控**：`/metrics` 走 `prometheus-fastapi-instrumentator`；自定义 `_GameStateCollector` 暴露 `poker_rooms_active` / `poker_seats_occupied` / `poker_humans_seated`。

## 必须遵守的约定

- **不要动 `_private` 命名的字段从类外**。已经暴露了公共 API，用这些：
  - `Room.is_closed`（不是 `._closed`）
  - `Room.broadcast(msg)`（不是 `._broadcast`）
  - `HandEngine.status_active`（不是 `._state.status`）
  - `HandEngine.actual_board_count()`（不是 `._actual_board_count()` / `_state.get_board_cards(0)`）
- **pokerkit 的 `get_board_cards(0)` 返回 generator**，不能 `len()`。要计数用 `sum(1 for _ in ...)` 或 list 化。这个坑我们踩过一次，导致游戏循环挂了、留下一堆只有 `hand_start` 的空 hand 行。
- **`StandardHighHand.from_game(hole, board)` 需要合起来 ≥5 张牌**。翻牌前（<3 张公共牌）不能用 pokerkit 评牌，自己判断口袋对。
- **`HandEngine` 有显示盖子 `_display_cap_cards`**（runout 动画用）。修改 `board()` / `street` / `is_hand_over` 时必须保持尊重盖子的行为；动画结束要 `set_display_cap(None)`。
- **不要加 Alembic 迁移**。当前策略是 `Base.metadata.create_all()` + `ALTER TABLE ... IF NOT EXISTS`（在 `backend/app/db.py:init_models`）。用户知道这不是生产方案，`backend/migrations/` 目录先空着。加新列就在 `init_models` 里加一条 ALTER。
- **密码 hash 用 `_prepare()` 预哈希**（sha256 → base64 → bcrypt）。不要改回裸 bcrypt，那样会有 72 字节截断漏洞。
- **CORS `*` 和 `JWT_SECRET` 默认值**是已知的 dev-only 配置，不要在 review 里反复提。
- **`Room` 里涉及 DB I/O 的方法（`stand_up` / `_finalize_cashouts` 等）严禁把 DB 读写放在 `self._lock` 内**。锁只保护内存状态变更，DB 调用必须在释放锁之后做。踩过一次 stand_up 持锁跑完整个 SessionLocal 导致游戏循环卡死的坑。
- **ORM 对象在 `session.rollback()` 后会 expired**，再访问字段会触发 lazy-load，在 async context 直接崩。读字段前先快照到本地变量（例：`have = fresh.balance` 再 rollback）。WS sit/rebuy 走的就是这个模式。
- **`users.balance` / `ledger.amount` / `ledger.balance_after` 是 `BigInteger`**。不要改回 Integer，admin topup 手滑会溢出。`TopupPayload.amount` 有 `±10⁹` 上下限。
- **手牌历史查询用 `Hand.user_ids.contains([uid])` 走 GIN 索引**（`backend/app/api/rest.py` my_hands / `admin.py` user_hands）。不要再扫 `seats` JSONB 在 Python 里过滤，那是老实现已经修掉的坑。
- **改密 / admin 重置密码时必须 bump `user.password_version`**。JWT 里带了 `pv` 字段，`current_user` 会校验，不匹配即视作失效。`change_password` 接口会签发新 token 在返回体里，前端必须替换本地。

## 代码风格偏好

- 中文注释 / 中文 UI 文案。代码标识符 / 路径 / 日志是英文。
- 用户不喜欢多余的 abstraction 和防御式代码。fix 就 fix，别搭脚手架。
- commit 之前跑 `npx tsc --noEmit` 和后端 `python3 -c "import app.main"`。
- 文件引用用 `[filename.ts:line](path)` markdown 链接格式（VS Code 扩展里能点）。

## 用户习惯（session 记忆）

- 用户同时用 Mac 和手机（同 WiFi）测试。手机访问 `http://<Mac 的局域网 IP>:5173`。
- `backend/.venv` 已存在，别重新创建。Python 3.12。
- 用户语言：中文。回答要简短，别重复解释代码功能。
- 提交代码前用户会说"准备提交"/"review 一下"，这时候跑类型检查 + 独立 reviewer（general-purpose agent），给出 must-fix / should-fix / nice-to-have 分级。

## 正在遗留的已知问题（非本次修复）

- `ws.py` 对 401 会无限重连（`frontend/src/lib/ws.ts`）
- WS action 的 `amount` 没做类型校验，非法输入走 `_obtain_action` 兜底 fold
- Alembic 目录空着
- WS token 走 query string（`?token=xxx`），可能被反代 access log 截获。生产需关日志或过滤 query string（DEPLOY.md 里有说）
