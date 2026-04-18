"""Phase 1 end-to-end smoke test.

Uses only public HTTP + WebSocket APIs. Expects uvicorn running on :8000 and
Postgres reachable at DATABASE_URL env var (same as the server).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import websockets

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
WS_BASE = BASE.replace("http", "ws")


async def register(client: httpx.AsyncClient, username: str, password: str) -> tuple[str, int]:
    r = await client.post("/api/auth/register", json={"username": username, "password": password})
    if r.status_code == 409:
        r = await client.post("/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["user"]["id"]


async def play_hand_via_ws(token_a: str, token_b: str, room_code: str) -> dict:
    """Connect two human players + trigger bot, play one full hand."""
    url_a = f"{WS_BASE}/ws/room/{room_code}?token={token_a}"
    url_b = f"{WS_BASE}/ws/room/{room_code}?token={token_b}"

    ws_a = await websockets.connect(url_a)
    ws_b = await websockets.connect(url_b)

    # A sits seat 0 with 200, B sits seat 2 with 200. A adds a regular bot on seat 5.
    await ws_a.send(json.dumps({"type": "sit", "seat_idx": 0, "buyin": 200}))
    await ws_b.send(json.dumps({"type": "sit", "seat_idx": 2, "buyin": 200}))
    await asyncio.sleep(0.5)

    hand_done = asyncio.Event()
    last_summary: dict = {}

    async def driver(ws, my_seat: int, label: str):
        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "state":
                eng = msg.get("engine")
                if eng and eng.get("actor_seat") == my_seat and eng.get("legal"):
                    legal = eng["legal"]
                    # simple strategy: call/check/fold
                    if legal["can_check"]:
                        await ws.send(json.dumps({"type": "action", "action": "check"}))
                    elif legal["can_call"] and legal["call_amount"] <= 4:
                        await ws.send(json.dumps({"type": "action", "action": "call"}))
                    else:
                        await ws.send(json.dumps({"type": "action", "action": "fold"}))
            elif t == "hand_end":
                last_summary.update(msg.get("data") or {})
                hand_done.set()

    task_a = asyncio.create_task(driver(ws_a, 0, "A"))
    task_b = asyncio.create_task(driver(ws_b, 2, "B"))

    # B also adds a regular bot (so we have 3 players for a nontrivial hand)
    await ws_b.send(json.dumps({"type": "add_bot", "seat_idx": 5, "tier": "regular"}))

    try:
        await asyncio.wait_for(hand_done.wait(), timeout=45)
    finally:
        task_a.cancel()
        task_b.cancel()
        await ws_a.close()
        await ws_b.close()

    return last_summary


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:
        tok_a, _ = await register(client, "alice", "password1")
        tok_b, _ = await register(client, "bob", "password1")

        r = await client.post(
            "/api/rooms",
            headers={"Authorization": f"Bearer {tok_a}"},
            json={
                "name": "smoke",
                "sb": 1,
                "bb": 2,
                "buyin_min": 50,
                "buyin_max": 200,
                "max_seats": 6,
            },
        )
        r.raise_for_status()
        code = r.json()["code"]
        print(f"room code: {code}")

        summary = await play_hand_via_ws(tok_a, tok_b, code)
        print("hand_end summary:", summary)

        # fetch hand list as alice
        r = await client.get("/api/hands", headers={"Authorization": f"Bearer {tok_a}"})
        r.raise_for_status()
        hands = r.json()
        print(f"alice hand count: {len(hands)}")
        assert hands, "no hands recorded!"

        # fetch first hand detail
        first = hands[0]
        r = await client.get(
            f"/api/hands/{first['hand_id']}",
            headers={"Authorization": f"Bearer {tok_a}"},
        )
        r.raise_for_status()
        detail = r.json()
        actions = detail["actions"]
        hole = detail["hole_cards"]
        print(f"actions: {len(actions)}, hole rows: {len(hole)}")
        assert actions, "no actions recorded!"
        assert hole, "no hole cards recorded!"
        print("winner_summary:", detail["winner_summary"])
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
