from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client.core import GaugeMetricFamily, REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.admin import router as admin_router
from app.api.rest import router as rest_router
from app.api.ws import router as ws_router
from app.auth.bootstrap import ensure_admin_user
from app.config import get_settings
from app.db import init_models
from app.game.manager import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    await ensure_admin_user()
    yield
    await manager.shutdown()


app = FastAPI(title="poker-friends", lifespan=lifespan)

_origins = get_settings().cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router)
app.include_router(ws_router)
app.include_router(admin_router)


# Prometheus：HTTP 层指标用 Instrumentator；游戏内存状态用自定义 collector（scrape 时即时读）
class _GameStateCollector:
    def collect(self):  # type: ignore[no-untyped-def]
        # manager.all() 已返回 list；r.members / r._current_engine 是 asyncio loop 内维护，
        # Prometheus scrape 走 async endpoint 所以在同一 loop 里，但还是快照一次避免迭代过程中被改
        rooms = [r for r in manager.all() if not r.is_closed]
        seats_total = 0
        humans = 0
        bots = 0
        hands_in_progress = 0
        for r in rooms:
            members = list(r.members.values())
            seats_total += len(members)
            for m in members:
                if m.user_id is not None:
                    humans += 1
                elif m.is_bot:
                    bots += 1
            if r._current_engine is not None:  # noqa: SLF001 (ok, MVP 观测)
                hands_in_progress += 1

        g = [
            GaugeMetricFamily("poker_rooms_active", "Active in-memory rooms"),
            GaugeMetricFamily("poker_seats_occupied", "Occupied seats across all rooms"),
            GaugeMetricFamily("poker_humans_seated", "Seated human players"),
            GaugeMetricFamily("poker_bots_seated", "Seated bot players"),
            GaugeMetricFamily("poker_hands_in_progress", "Hands currently being played"),
        ]
        g[0].add_metric([], len(rooms))
        g[1].add_metric([], seats_total)
        g[2].add_metric([], humans)
        g[3].add_metric([], bots)
        g[4].add_metric([], hands_in_progress)
        for m in g:
            yield m


REGISTRY.register(_GameStateCollector())

# HTTP 请求直方图：Instrumentator 仅安装中间件采集，不自动挂 endpoint
Instrumentator(
    should_group_status_codes=False,
    excluded_handlers=["/health", "/metrics"],
).instrument(app)


@app.get("/metrics", include_in_schema=False)
async def metrics(authorization: str | None = Header(default=None)):
    token = get_settings().metrics_token
    if token:
        if authorization != f"Bearer {token}":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid metrics token")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    return {"ok": True}
