# 朋友局 · 自托管德州扑克平台

自建的 NLHE 德州扑克平台，面向朋友局：Web 端登录、≤20 人在线、可加难度机器人、全量手牌写入 Postgres 用于后续复盘。

## 功能特性

- **5 档机器人**（菜鸟 / 常规 / 常客 / 半职业 / 职业），空座位点 `+AI` 直接坐下
- **房间 2h 自动关闭**：倒计时期间大厅卡片和桌面 header 都会显示剩余时间；到点后打完当前一手再关，弹出全场输赢排名
- **手牌回放**：历史页面每一手都能逐帧播放（弃牌/下注/过街/all-in runout/摊牌），支持 0.5x–2x 变速和拖拽进度条
- **实时牌型提示**：翻牌后在牌桌中央显示你当前的最佳牌型（"一对 A" / "同花" / "顺子" …），减少读牌失误
- **All-in 分阶段发牌**：pre-flop 对 all-in 不再瞬间出结果，flop → turn → river 按 1.2s 间隔动画亮出
- **再买入**：筹码耗尽不必离席，点"再买入继续"就能续筹码；AI 输光自动离座让位
- **全量 DB 写入**：每手的动作流、底牌、结算都进 Postgres，历史/回放直接读库

## 本地开发

### 需要
- Python 3.11+
- Node 20+
- Docker Desktop（推荐，用 docker-compose 起 Postgres + 全栈）

### 方式 A：docker-compose 一把梭（推荐）
```bash
cd infra
cp .env.example .env    # 改 JWT_SECRET
docker compose up --build
```
- 前端：http://localhost:8080
- 后端：http://localhost:8000 （/docs 查 OpenAPI）
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
  .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3) 前端
cd frontend
npm install
npm run dev     # http://localhost:5173
```

### 手机局域网调试
Vite 已经是 `--host 0.0.0.0`，同一 WiFi 下手机浏览器直接访问 `http://<Mac 的局域网 IP>:5173` 即可（`ipconfig getifaddr en0` 查 IP）。前端的 `/api` `/ws` 代理会转到本机 8000 端口，不用改配置。连不上先检查 macOS 防火墙放行 node/python。

## 可配置项
所有后端配置通过环境变量覆盖（pydantic-settings），默认值见 `backend/app/config.py`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://poker:poker@localhost:5432/poker` | Postgres 异步连接串 |
| `JWT_SECRET` | `dev-secret-change-me` | **上线前必改**，建议 ≥32 字节随机串 |
| `JWT_EXP_HOURS` | 168 | Token 有效期 |
| `ROOM_LIFETIME_S` | 7200 | 房间自动关闭时间（秒），默认 2 小时 |
| `ACTION_TIMEOUT_S` | 15 | 单次行动超时（超时自动 check/fold） |
| `BETWEEN_HANDS_S` | 3.0 | 两手之间停顿 |
| `BOT_THINK_MIN_S` / `BOT_THINK_MAX_S` | 0.8 / 2.2 | AI 思考随机时长区间 |
| `RUNOUT_STAGE_S` | 1.2 | All-in runout 每街动画间隔 |

## ⚠️ 升级 / 首次运行提示

- **Schema 初始化**：后端启动时会执行 `Base.metadata.create_all()` 加若干幂等 `ALTER TABLE ... IF NOT EXISTS`（为后加的 `closes_at` / `final_standings` 做兜底）。这是**开发态**方案，`backend/migrations/` 目录预留给 Alembic，上线前请建 baseline migration。
- **密码 hash 迁移**：现在使用 `sha256 → bcrypt` 预哈希（修掉 bcrypt 72 字节静默截断）。如果你的 Postgres 里已经有旧账号（本次提交之前注册的），这些账号的 hash 不兼容，登录会失败。处理方式：`TRUNCATE users CASCADE;` 清库后重新注册，或手动让用户 reset。

## 主要目录
- `backend/app/game/engine.py` — PokerKit 封装（NLHE 状态机、牌型评估、runout 显示盖子）
- `backend/app/game/room.py` — 单房间 asyncio 游戏循环（含自动关闭、standings 累计、rebuy、AI 自动离座）
- `backend/app/game/bots/` — 5 档机器人（Phase 1 仅常规完整）
- `backend/app/game/recorder.py` — 每手动作 → DB
- `backend/app/api/ws.py` — WebSocket 游戏端点（sit/stand/rebuy/add_bot/action/chat）
- `backend/app/api/rest.py` — 房间列表、创建、手牌历史
- `frontend/src/routes/Lobby.tsx` — 大厅 + 剩余时间倒计时
- `frontend/src/routes/Table.tsx` — 牌桌主视图
- `frontend/src/routes/Hands.tsx` — 手牌历史入口
- `frontend/src/components/HandReplay.tsx` — 回放组件（从动作日志重建每一帧）
- `frontend/src/components/BettingControls.tsx` — 下注条

## 腾讯云部署提示
- 推荐 2 vCPU / 4GB CVM，Ubuntu 22.04
- `git pull && cd infra && docker compose up -d --build`
- 配域名 + Nginx 前置 TLS（或使用腾讯云 SSL）
- 备份：`pg_dump -U poker poker > backup.sql` 定时上传 COS
