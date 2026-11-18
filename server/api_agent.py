"""Uplink of the PC program: `WS /ws/agent` and `GET /api/agent/status` (step U1).

The handshake is refused BEFORE accepting the socket (HTTP 4xx/5xx): any Origin header, too many
attempts from one IP, too many programs, no `Authorization: Bearer` header (a token in the query
string is ignored, so it counts as missing), an unknown or expired access token. Only then the
socket is accepted. Nothing sensitive is logged (no tokens, addresses or user data)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Optional

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from server import wire
from server.accounts import AccountError, Accounts
from server.api_auth import _fail, client_ip_from, current_session
from server.devicecode import DeviceCodes
from server.hub import Hub

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


SEND_TIMEOUT = 5.0  # a peer that does not read must not hold a handler (or the loop) hostage


async def _close(ws: WebSocket, code: int, timeout: float = SEND_TIMEOUT) -> None:
    try:
        await asyncio.wait_for(ws.close(code=code), timeout)
    except Exception:  # noqa: BLE001  (already closed, or too slow: give up)
        pass


def build_agent_router(accounts: Accounts, devices: DeviceCodes, hub: Hub) -> APIRouter:
    router = APIRouter()
    cfg = accounts.cfg

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

    return router
