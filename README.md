# 朋友局 · 自托管德州扑克平台

自建的 NLHE 德州扑克平台，面向朋友局：Web 端登录、≤20 人在线、可加 AI、全量手牌写入 Postgres 用于后续复盘。内置 bank 余额 + 管理后台 + 邀请码注册。

## 功能特性

- **邀请码注册**：只有持码者才能注册，一次性消费；admin 后台或 CLI 批量生成
- **Bank 余额系统**：新用户注册自动到账 20000 筹码（可配置）；入桌 / 补带从余额扣除，离席或关桌自动回灌
- **Admin 管理后台**：查看所有用户余额、资金流水、手牌历史；充值 / 扣款；生成 / 管理邀请码；重置用户密码；处理异常结算
- **3 档机器人**（菜鸟 / 常客 / 职业），空座位点 `+AI` 直接坐下
- **房间 2h 自动关闭**：倒计时期间大厅卡片和桌面 header 都会显示剩余时间；到点后打完当前一手再关，弹出全场输赢排名
- **手牌回放**：历史页面每一手都能逐帧播放（弃牌/下注/过街/all-in runout/摊牌），支持 0.5x–2x 变速和拖拽进度条
- **实时牌型提示**:翻牌后在牌桌中央显示你当前的最佳牌型
- **All-in 分阶段发牌**:pre-flop 对 all-in 不再瞬间出结果，flop → turn → river 按 1.2s 间隔动画亮出
- **再买入**：筹码耗尽不必离席，点"再买入继续"就能续筹码；AI 输光自动离座让位
- **密码自服务 + 管理员重置**：用户可自己改密码；忘记密码找 admin 后台一键重置并返回临时密码；改密后所有旧 JWT 立即失效
- **Prometheus `/metrics`**：暴露活跃房间数、在座玩家数、HTTP 请求直方图等
- **全量 DB 写入**：每手的动作流、底牌、结算都进 Postgres，历史 / 回放直接读库；所有资金变动走 `ledger_entries` 表可审计

## 本地开发

### 需要
- Python 3.11+
- Node 20+
- Docker Desktop（推荐，用 docker-compose 起 Postgres + 全栈）

### 方式 A：docker-compose 一把梭（推荐）
```bash
cd infra
cp .env.example .env    # 改 JWT_SECRET、ADMIN_PASSWORD
docker compose up --build
```
- 前端：http://localhost:8080
- 后端：http://localhost:8000 （/docs 查 OpenAPI、/metrics 查监控指标）
- Postgres：localhost:5432（user/pass 都是 `poker`）

### 方式 B：开发模式分开跑
```bash
# 1) Postgres 单独起
docker run -d --name poker-db -p 5432:5432 \
  -e POSTGRES_USER=poker -e POSTGRES_PASSWORD=poker -e POSTGRES_DB=poker \
  postgres:16-alpine

# 2) 后端
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
DATABASE_URL=postgresql+asyncpg://poker:poker@localhost:5432/poker \
ADMIN_USERNAME=admin ADMIN_PASSWORD=admin-dev-pass \
  .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3) 前端
cd frontend
npm install
npm run dev     # http://localhost:5173
```

### 首次使用（生成邀请码）

没有邀请码谁也注册不了。首次部署完有两条路：

```bash
# 方式 1：CLI（admin 还没创建时兜底）
cd backend && source .venv/bin/activate
python -m app.scripts.make_invite --count 5

# 方式 2：启动时通过 ADMIN_USERNAME / ADMIN_PASSWORD env
# bootstrap 一个管理员，登录后进 /admin → 邀请码 tab → 批量生成
```

### 手机局域网调试
Vite 已经是 `--host 0.0.0.0`，同一 WiFi 下手机浏览器直接访问 `http://<Mac 的局域网 IP>:5173` 即可（`ipconfig getifaddr en0` 查 IP）。前端的 `/api` `/ws` 代理会转到本机 8000 端口，不用改配置。连不上先检查 macOS 防火墙放行 node/python。

## 可配置项
所有后端配置通过环境变量覆盖（pydantic-settings），默认值见 `backend/app/config.py`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://poker:poker@localhost:5432/poker` | Postgres 异步连接串 |
| `JWT_SECRET` | `dev-secret-change-me` | **上线前必改**，`openssl rand -hex 32` |
| `JWT_EXP_HOURS` | 168 | Token 有效期 |
| `CORS_ORIGINS` | `*` | 逗号分隔白名单，生产填具体域名 |
| `REGISTER_BONUS` | 20000 | 新用户注册奖励筹码 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 空 | 填了才会在启动时自动创建 admin；已存在则只确保 `is_admin=true`，不覆盖密码 |
| `ADMIN_DISPLAY_NAME` | `Admin` | bootstrap admin 的展示名 |
| `ROOM_LIFETIME_S` | 7200 | 房间自动关闭时间（秒），默认 2 小时 |
| `ACTION_TIMEOUT_S` | 15 | 单次行动超时（超时自动 check/fold） |
| `BETWEEN_HANDS_S` | 3.0 | 两手之间停顿 |
| `BOT_THINK_MIN_S` / `BOT_THINK_MAX_S` | 0.8 / 2.2 | AI 思考随机时长区间 |
| `RUNOUT_STAGE_S` | 1.2 | All-in runout 每街动画间隔 |

## ⚠️ 升级 / 首次运行提示

- **Schema 初始化**：后端启动时会执行 `Base.metadata.create_all()` + 一堆幂等 `ALTER TABLE ... IF NOT EXISTS` 处理新增的 `users.balance` / `users.is_admin` / `users.password_version` / `hands.user_ids` / `ledger_entries.amount`→BigInt 等。这是**开发态**方案，`backend/migrations/` 目录预留给 Alembic，上线前请建 baseline migration。
- **密码 hash**：使用 `sha256 → bcrypt` 预哈希（修掉 bcrypt 72 字节静默截断）。如果有本次大改之前注册的旧账号（没有 `balance` / `is_admin` 字段），迁移会给它们补默认值（balance=0，非 admin）。

## 主要目录

### 后端
- `backend/app/main.py` — FastAPI 入口、CORS、lifespan（init_models + admin bootstrap）、Prometheus `/metrics` 接入
- `backend/app/config.py` — pydantic-settings 配置项
- `backend/app/db.py` — Postgres async engine + `init_models` 幂等迁移
- `backend/app/bank.py` — 统一的余额调整 + ledger 写入 helper
- `backend/app/auth/security.py` — 密码 hash / JWT（带 `password_version`）
- `backend/app/auth/bootstrap.py` — 启动时自动创建 admin 账号（按 env）
- `backend/app/auth/deps.py` — `current_user` / `require_admin` 依赖
- `backend/app/api/rest.py` — 注册 / 登录 / 改密 / 房间 CRUD / 手牌历史
- `backend/app/api/admin.py` — 全部 `/api/admin/*`：用户、流水、充值、邀请码、密码重置、pending cashout
- `backend/app/api/ws.py` — WebSocket 游戏端点（sit/stand/rebuy/add_bot/remove_bot/action/chat）
- `backend/app/game/engine.py` — pokerkit 封装（NLHE 状态机、牌型评估、runout 显示盖子）
- `backend/app/game/room.py` — 单房间 asyncio 游戏循环（自动关闭 + cashout 结算）
- `backend/app/game/membership.py` — `RoomMember` 快照 helper，进程崩溃止损
- `backend/app/game/recorder.py` — 每手动作 → DB，写 `hands.user_ids` 供 GIN 查询
- `backend/app/game/bots/` — 3 档机器人
- `backend/app/models/` — `user.py` / `invite.py` / `ledger.py` / `game.py`
- `backend/app/scripts/make_invite.py` — CLI 批量生成邀请码

### 前端
- `frontend/src/routes/Login.tsx` — 登录 / 注册（注册要填邀请码）
- `frontend/src/routes/Lobby.tsx` — 大厅、余额显示、改密弹窗
- `frontend/src/routes/Table.tsx` — 牌桌主视图
- `frontend/src/routes/Admin.tsx` — 管理后台（用户 / 邀请码 / 待处理 cashout 三个 tab）
- `frontend/src/routes/Hands.tsx` — 手牌历史入口
- `frontend/src/components/HandReplay.tsx` — 回放组件
- `frontend/src/components/BettingControls.tsx` — 下注条
- `frontend/src/components/Seat.tsx` — 座位（含 bot 的 ✕ 踢出按钮）

## 腾讯云部署

见 [DEPLOY.md](DEPLOY.md)：涵盖 CDB 选型、CLB + HTTPS、WebSocket 支持、安全清单（admin 密码、JWT、token 泄漏）、Prometheus 监控。
