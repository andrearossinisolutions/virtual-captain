"""Uplink of the PC program: `WS /ws/agent`, `GET /api/agent/status` (step U1) and the flight
snapshot with `GET /api/flight/state` (step U2).

The handshake is refused BEFORE accepting the socket (HTTP 4xx/5xx): any Origin header, too many
attempts from one IP, too many programs, no `Authorization: Bearer` header (a token in the query
string is ignored, so it counts as missing), an unknown or expired access token. Only then the
socket is accepted. Nothing sensitive is logged (no tokens, addresses or user data, never the content
of a snapshot). The snapshot lives only in the RAM of the connection and goes only to the browser
session of the same account (the hub key is the session's user id: no id travels in the URL)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Optional

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from server import wire
from server.accounts import AccountError, Accounts
from server.api_auth import _fail, client_ip_from, current_session
from server.devicecode import DeviceCodes
from server.hub import Hub
from server.ratelimit import MemoryRateLimiter

log = logging.getLogger("virtual_captain")


class _Gone(Exception):
    """The program closed the connection."""


def bearer_token(headers) -> Optional[str]:
    """Token of `Authorization: Bearer <token>`; the query string is never looked at."""
    value = headers.get("authorization") or ""
    scheme, _, token = value.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        return None
    return token


async def _deny(ws: WebSocket, status: int) -> None:
    """Refuse the handshake with an HTTP status (ASGI denial response); plain close as fallback."""
    try:
        if "websocket.http.response" in ws.scope.get("extensions", {}):
            await ws.send_denial_response(PlainTextResponse("", status_code=status,
                                                            headers={"Cache-Control": "no-store"}))
        else:
            await ws.close(code=1008)
    except Exception:  # noqa: BLE001  (peer already gone)
        pass


# Snapshot limits (the program sends at most 1 per second): more is a misbehaving client.
SNAP_MAX_PER_WINDOW, SNAP_WINDOW = 4, 2.0
SNAP_MAX_BYTES, SNAP_BYTES_WINDOW = 1 << 20, 10.0
SNAP_MAX_INVALID = 5          # consecutive invalid snapshots before the connection is closed (1008)
FLIGHT_PER_USER_MINUTE, FLIGHT_PER_IP_MINUTE = 120, 600   # polling of /api/flight/state, RAM only

SEND_TIMEOUT = 5.0  # a peer that does not read must not hold a handler (or the loop) hostage


async def _close(ws: WebSocket, code: int, timeout: float = SEND_TIMEOUT) -> None:
    try:
        await asyncio.wait_for(ws.close(code=code), timeout)
    except Exception:  # noqa: BLE001  (already closed, or too slow: give up)
        pass


def build_agent_router(accounts: Accounts, devices: DeviceCodes, hub: Hub,
                       memory_limiter: Optional[MemoryRateLimiter] = None) -> APIRouter:
    router = APIRouter()
    cfg = accounts.cfg
    mem = memory_limiter or MemoryRateLimiter(hub.monotonic)

    async def recv(ws: WebSocket, timeout: float):
        """Next message dict, or None on timeout; _Gone when the program disconnected."""
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout)
        except asyncio.TimeoutError:
            return None
        if msg["type"] == "websocket.disconnect":
            raise _Gone()
        return msg

    def parse(msg) -> Optional[dict]:
        """JSON object from a text frame (size already checked), else None."""
        text = msg.get("text")
        if text is None:
            return None
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("t"), str) else None

    def too_big(msg) -> bool:
        payload = msg.get("text")
        size = len(payload.encode("utf-8", "replace")) if payload is not None else len(msg.get("bytes") or b"")
        return size > wire.MAX_MSG

    async def hello(ws: WebSocket) -> bool:
        """Waits for the hello; closes the socket and returns False on any problem."""
        msg = await recv(ws, cfg.agent_hello_timeout)
        if msg is None:
            await _close(ws, 1008)
            return False
        if too_big(msg):
            await _close(ws, wire.CLOSE_TOO_BIG)
            return False
        data = parse(msg)
        if data is None or data["t"] != wire.T_HELLO:
            await _close(ws, 1008)
            return False
        version = data.get("v")
        if isinstance(version, bool) or version not in wire.SUPPORTED_VERSIONS:
            await _close(ws, wire.CLOSE_BAD_VERSION)
            return False
        return True

    async def run(ws: WebSocket, user_id: int, family_id: str) -> None:
        conn = hub.new_conn(ws, family_id)
        old = hub.register(user_id, conn)
        log.info("agent connected (%d online)", hub.count())
        try:
            if old is not None:  # bounded: an unresponsive old socket must not stall the new one
                await _close(old.websocket, wire.CLOSE_REPLACED, 2.0)
            mono = hub.monotonic
            started = mono()
            next_family = started + cfg.agent_family_recheck
            recent: deque = deque()
            snaps: deque = deque()      # (time, bytes) of the last snapshots, for the rate limits
            invalid = 0
            while True:
                now = mono()
                if now - started >= cfg.agent_max_lifetime:
                    await _close(ws, 1000)
                    return
                if now - conn.last_seen >= cfg.agent_stale_after:
                    await _close(ws, 1001)
                    return
                if now >= next_family:
                    next_family = now + cfg.agent_family_recheck
                    try:
                        alive = await run_in_threadpool(devices.family_active, family_id)
                    except Exception:  # noqa: BLE001  (transient DB problem: ask again next time)
                        log.warning("agent authorisation re-check failed")
                        alive = True
                    if not alive:
                        await _close(ws, wire.CLOSE_REVOKED)
                        return
                timeout = max(0.01, min(conn.last_seen + cfg.agent_stale_after, next_family,
                                        started + cfg.agent_max_lifetime) - now)
                msg = await recv(ws, timeout)
                if msg is None:
                    continue
                now = mono()
                while recent and now - recent[0] > 1.0:
                    recent.popleft()
                if len(recent) >= cfg.agent_msgs_per_second or too_big(msg):
                    await _close(ws, wire.CLOSE_TOO_BIG)
                    return
                recent.append(now)
                data = parse(msg)
                if data is None:
                    await _close(ws, 1008)
                    return
                hub.touch(conn)
                if data["t"] == wire.T_PING:
                    await asyncio.wait_for(ws.send_text(json.dumps({"t": wire.T_PONG})), SEND_TIMEOUT)
                elif data["t"] == wire.T_BYE:
                    await _close(ws, 1000)
                    return
                elif data["t"] == wire.T_SNAPSHOT:
                    size = len(msg["text"].encode("utf-8", "replace"))
                    snaps.append((now, size))
                    while snaps and now - snaps[0][0] > SNAP_BYTES_WINDOW:
                        snaps.popleft()
                    if (sum(1 for t, _ in snaps if now - t <= SNAP_WINDOW) > SNAP_MAX_PER_WINDOW
                            or sum(b for _, b in snaps) > SNAP_MAX_BYTES):
                        await _close(ws, wire.CLOSE_TOO_BIG)
                        return
                    sv = data.get("sv")
                    if isinstance(sv, bool) or sv not in wire.SUPPORTED_SNAPSHOT_VERSIONS:
                        await _close(ws, wire.CLOSE_BAD_VERSION)
                        return
                    seq, view = data.get("seq"), None
                    if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
                        try:
                            view = wire.sanitize_snapshot(data.get("d"))
                        except Exception:  # noqa: BLE001  (SnapshotError, RecursionError...: never log the content)
                            view = None
                    if view is None:
                        invalid += 1
                        if invalid >= SNAP_MAX_INVALID:
                            await _close(ws, 1008)
                            return
                    else:
                        invalid = 0
                        hub.store_snapshot(conn, seq, view)
                # pong, repeated hello and unknown types: only keep-alive (forward compatible)
        finally:
            hub.unregister(user_id, conn)
            log.info("agent disconnected (%d online)", hub.count())

    async def agent_ws(websocket: WebSocket) -> None:
        h = websocket.headers
        if "origin" in h:  # a browser must never be able to open this socket
            return await _deny(websocket, 403)
        peer = websocket.client.host if websocket.client else ""
        ip = client_ip_from(peer, h)
        if not await run_in_threadpool(accounts.limiter.allow, "ws_ip", ip,
                                       [(cfg.agent_ip_per_minute, 60)]):
            return await _deny(websocket, 429)
        if hub.count() >= cfg.agent_max:
            return await _deny(websocket, 503)
        token = bearer_token(h)
        verified = await run_in_threadpool(devices.verify_access, token) if token else None
        if verified is None:
            return await _deny(websocket, 401)
        user_id, family_id = verified
        await websocket.accept()
        try:
            await asyncio.wait_for(websocket.send_text(json.dumps(wire.welcome_message())), SEND_TIMEOUT)
            if await hello(websocket):
                await run(websocket, user_id, family_id)
        except _Gone:
            pass
        except Exception:  # noqa: BLE001  (peer vanished mid-send, etc.): never log the content
            log.info("agent connection ended with an error")
            await _close(websocket, 1011)

    router.add_api_websocket_route(wire.WS_PATH, agent_ws)

    @router.get("/api/agent/status", include_in_schema=False)
    async def status(request: Request):
        try:
            sess = current_session(accounts, request, False)
        except AccountError as e:
            return _fail(e.status, e.code)
        return JSONResponse({"connected": hub.status(sess["uid"])})

    def client_key(request: Request) -> str:
        return client_ip_from(request.client.host if request.client else "", request.headers)

    @router.get("/api/flight/state", include_in_schema=False)
    async def flight_state(request: Request):
        """Live state for the signed-in account (session cookie only, never a token or an id in the URL)."""
        try:
            sess = current_session(accounts, request, False)
        except AccountError as e:
            return _fail(e.status, e.code)
        if (not mem.allow("flight_user", str(sess["uid"]), FLIGHT_PER_USER_MINUTE, 60.0)
                or not mem.allow("flight_ip", client_key(request), FLIGHT_PER_IP_MINUTE, 60.0)):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "5", "Vary": "Cookie"})
        raw_seq = request.query_params.get("seq", "")
        seq = int(raw_seq) if raw_seq.isascii() and raw_seq.isdigit() and len(raw_seq) <= 12 else None  # anything else: full snapshot
        raw_epoch = request.query_params.get("epoch", "")
        epoch = int(raw_epoch) if raw_epoch.isascii() and raw_epoch.isdigit() and len(raw_epoch) <= 12 else None  # missing/garbage: full snapshot
        state = hub.flight_state(sess["uid"])
        headers = {"Vary": "Cookie"}
        if state is None:
            return JSONResponse({"connected": False}, headers=headers)
        same = seq is not None and seq == state["seq"] and epoch == state["epoch"]
        age = "null" if state["age"] is None else "%.1f" % state["age"]
        ago = "null" if state["spoke_ago"] is None else "%.1f" % state["spoke_ago"]
        if state["text"] is None:
            snap = "null"
        elif same:
            snap = "null"
        else:
            snap = state["text"]
        unchanged = ',"unchanged":true' if state["text"] is not None and same else ""
        body = '{"connected":true,"seq":%d,"epoch":%d,"age":%s,"spoke_ago":%s,"snapshot":%s%s}' % (state["seq"], state["epoch"], age, ago, snap, unchanged)
        return Response(body, media_type="application/json", headers=headers)

    return router
