# 部署到腾讯云

面向单机小规模朋友局，不是高可用方案。

## 一、前置

- 一台 CVM（2c4g 起步够用），装好 Docker + docker compose plugin
- 域名 + SSL 证书（可选但强烈推荐 —— 手机浏览器对非 HTTPS 的 WebSocket 不友好）
- 安全组放通：80、443（或你要用的 `WEB_PORT`）。8000 不需要暴露，CORS 同源走反代即可

## 二、选数据库

### 方案 A：compose 自带 postgres（最简单）

`.env` 里保留 `POSTGRES_USER/PASSWORD/DB`，`DATABASE_URL` 指向 `db:5432`。
数据卷 `poker-db` 存在宿主机，CVM 重启不丢数据。**但 CVM 重装/迁移会丢**，记得定期 `docker exec` 进去 `pg_dump`。

### 方案 B：腾讯云 CDB PostgreSQL（推荐生产）

1. 控制台开一个 PostgreSQL 实例（和 CVM 同 VPC），建库 `poker`
2. `.env` 注释掉 `POSTGRES_*`，改 `DATABASE_URL`：
   ```
   DATABASE_URL=postgresql+asyncpg://用户:密码@cdb-xxx.sql.tencentcdb.com:5432/poker
   ```
3. [docker-compose.yml](infra/docker-compose.yml) 里的 `db` 服务可以删掉（或保留不启动：`docker compose up -d backend web`）

## 三、首次部署

```bash
# 在 CVM 上
git clone <你的 repo> && cd poker-friends/infra
cp .env.example .env

# 必改 4 项
#   JWT_SECRET       → openssl rand -hex 32 的输出
#   DATABASE_URL     → 按上面二选一
#   CORS_ORIGINS     → 你的域名，比如 https://poker.example.com
#   ADMIN_PASSWORD   → 强密码（≥12 位），会在首次启动自动创建 admin 账号
vim .env

docker compose up -d --build
docker compose logs -f backend   # 确认 init_models 完成、admin bootstrap、没报 DB 连不上
```

### 生成第一批邀请码

新用户注册需要邀请码，否则无人能注册。admin 账号本身是通过 env 创建的，不需要邀请码。两种生成方式：

```bash
# 方式 1：CLI（在 CVM 上进容器跑，任意时候可用）
docker compose exec backend python -m app.scripts.make_invite --count 5

# 方式 2：admin 登录后，进 /admin → "邀请码" tab → 批量生成
# 生成的码直接复制发朋友
```

## 四、接域名 + HTTPS

推荐直接用腾讯云**负载均衡 CLB**（HTTP/HTTPS 监听）：

- 监听器 443 → 后端 CVM:`WEB_PORT`（默认 8080）
- CLB 上挂 SSL 证书
- **必须开启 WebSocket**（CLB 监听器设置里有选项），不然 `/ws/` 会 502

前端代码已经处理：`window.location.protocol === "https:"` 时自动用 `wss://`，不用改。

不想上 CLB 也可以在 CVM 上装 Caddy / nginx 做 443 终端 → 反代到 `127.0.0.1:8080`，证书用 Let's Encrypt。

## 五、更新

```bash
cd poker-friends && git pull
cd infra && docker compose up -d --build
```

DB schema 变了也没关系 —— [backend/app/db.py](backend/app/db.py) `init_models` 里用的是
`create_all` + `ALTER TABLE ... IF NOT EXISTS`，新列加在那里即可，没有 Alembic 迁移。

**已有的自动迁移**（老库升级上来会幂等执行）：
- `rooms.closes_at` / `rooms.final_standings`
- `users.balance`（BIGINT）/ `users.is_admin` / `users.password_version`
- `ledger_entries.amount` 和 `balance_after` 升到 BIGINT（兼容旧 INTEGER 数据）
- `hands.user_ids INTEGER[]` + GIN 索引，并且自动从旧 `seats` JSONB 回填历史数据

## 六、备份（方案 A 适用）

```bash
docker exec infra-db-1 pg_dump -U poker poker > backup-$(date +%F).sql
```

建个 cron 每天跑一次，传 COS。方案 B 用 CDB 自带的备份策略，不用管。

## 七、常见坑

- **手机连不上 WebSocket**：99% 是 HTTPS 混合内容或 CLB 没开 WS 支持
- **CORS 报错**：`.env` 里 `CORS_ORIGINS` 的协议/端口/子域要**完全匹配**前端实际访问的 URL
- **JWT 登录失效**：`JWT_SECRET` 变了会让所有已发 token 失效，生产别随便改
- **房间关了没保存**：`ROOM_LIFETIME_S` 默认 2 小时自动关，想开长桌自己调

## 八、安全注意事项

- **Admin 初始密码**：`.env` 里 `ADMIN_PASSWORD` **必须换成强密码**（≥12 位随机），默认占位 `PLEASE_CHANGE_ME_to_strong_password` 只是提示，生产上线前务必改。bootstrap 逻辑仅在首次启动创建该 admin，之后不覆盖密码，所以一旦用弱密码启动过，漏洞就一直在 —— 进 admin 后台重置即可。
- **JWT 密钥**：`JWT_SECRET` 用 `openssl rand -hex 32` 生成，不要用默认值。
- **WebSocket 鉴权**：前端把 token 作为 query 参数 `?token=xxx` 传给 `/ws/room/<code>`。token 会出现在反代/网关的 access log 里（哪怕是 HTTPS）。务必**关闭 nginx/CLB 的 access log，或者在日志格式里过滤 query string**。后续打算改成 WebSocket subprotocol 认证以彻底规避。
- **改密/重置会作废旧 token**：用户自己改密码或 admin 重置后，所有旧 JWT 立即失效（通过 `password_version` 校验），原已登录的客户端会收到 401。change_password 接口会返回新 token，前端已自动替换。

## 九、监控（Prometheus /metrics）

后端在 `/metrics` 暴露 Prometheus 指标。**生产务必设置 `METRICS_TOKEN`**，留空则端点公开。

**业务 gauge**：
- `poker_rooms_active` —— 当前活跃房间数
- `poker_seats_occupied` —— 所有桌已占座位总数
- `poker_humans_seated` —— 在座人类玩家数
- `poker_bots_seated` —— 在座 AI 数
- `poker_hands_in_progress` —— 正在进行的手牌数（观察游戏循环是否 stuck）

**HTTP 指标**（instrumentator 自动采集）：
- `http_request_duration_seconds`（直方图）、`http_requests_total`（按状态码分桶）

### Prometheus 抓取配置示例

```yaml
# prometheus.yml
scrape_configs:
  - job_name: poker
    scrape_interval: 30s
    authorization:
      type: Bearer
      credentials: <你在 .env 设的 METRICS_TOKEN>
    static_configs:
      - targets: ['<cvm-内网-ip>:8000']
```

### 建议告警

- `poker_rooms_active > 20` 持续 10 分钟：房间累积异常
- `rate(http_requests_total{status=~"5.."}[5m]) > 0.01`：5xx 错误率偏高
- `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 1`：接口变慢

或者用腾讯云**云监控**的"自定义监控"拉这个端点，免搭 Prometheus 栈。

### 反代层保护

nginx / CLB 上**不要把 `/metrics` 暴露到公网**。示例 nginx 片段：

```nginx
location /metrics {
    allow 10.0.0.0/8;    # 内网
    deny all;
    proxy_pass http://backend:8000;
}
```
