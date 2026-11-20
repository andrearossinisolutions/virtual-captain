"""Live state to the browser as Server-Sent Events: `GET /api/flight/stream` (U4, step C4).

Same auth as the polling route (session cookie only; the hub key is the session's user id, no id in the URL).
Events: `snapshot` (the whole state, id "<epoch>.<seq>"), `delta` ({"d": changed sections, "rm": removed keys}: only the
sections newer than the last seq sent to THAT subscriber, so a slow client is coalesced and no history is kept),
`meta` ({connected, age, spoke_ago, cmd}), `offline`; a ": hb" comment every `stream_heartbeat` seconds keeps the line alive for proxies (the browser never sees it). Resume with
`Last-Event-ID` (or `?last=`): same epoch and a seq not in the future -> only the newer sections, else a full snapshot.
Caps per account / per IP / global, a maximum duration, the session re-checked in the thread pool, closed at once on
revocation. Nothing from a flight is ever logged. Kill switch: VC_STREAM=off (the route does not exist: 404)."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from server.accounts import AccountError, Accounts
from server.api_auth import COOKIE, _fail, client_ip_from, current_session, origin_check
from server.hub import Hub
from server.ratelimit import MemoryRateLimiter

PADDING = b": " + b" " * 2048 + b"\n\nretry: 3000\n\n"   # defeats proxy buffering of the first bytes
HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Accel-Buffering": "no"}


class Stream:
    """One open stream: who it belongs to and the way to close it from outside."""
    def __init__(self, uid: int, sid: int, ip: str, ev: asyncio.Event):
        self.uid, self.sid, self.ip, self.ev = uid, sid, ip, ev
        self.closed = False

    def close(self) -> None:
        self.closed = True
        self.ev.set()


class StreamRegistry:
    def __init__(self):
        self.streams: set = set()

    def admit(self, uid: int, ip: str, per_user: int, per_ip: int, total: int) -> bool:
        if len(self.streams) >= total:
            return False
        if sum(1 for s in self.streams if s.uid == uid) >= per_user:
            return False
        return sum(1 for s in self.streams if s.ip == ip) < per_ip

    def close_user(self, uid: int) -> None:
        for s in list(self.streams):
            if s.uid == uid:
                s.close()

    def close_session(self, sid: int) -> None:
        for s in list(self.streams):
            if s.sid == sid:
                s.close()


class StreamResponse(StreamingResponse):
    """Frees the subscription and the counters however the response ends (also when it is cancelled or never iterated)."""
    def __init__(self, content, release, write_timeout: float = 25.0, **kw):
        super().__init__(content, **kw)
        self._release = release
        self._write_timeout = write_timeout

    async def __call__(self, scope, receive, send):
        timed_out = []

        async def guarded_send(msg):        # a client that stops reading must not hold the slot for ever
            try:
                await asyncio.wait_for(send(msg), self._write_timeout)
            except asyncio.TimeoutError:
                timed_out.append(True)
                raise
        try:
            await super().__call__(scope, receive, guarded_send)
        except BaseException:
            if not timed_out:
                raise                       # the response simply ends on a write timeout
        finally:
            self._release()


def parse_event_id(raw: Optional[str]) -> Optional[tuple]:
    """"<epoch>.<seq>" -> (epoch, seq), anything else -> None (full snapshot)."""
    if not raw or len(raw) > 32:
        return None
    a, dot, b = raw.partition(".")
    if not dot or not (a.isascii() and a.isdigit() and b.isascii() and b.isdigit()):
        return None
    return int(a), int(b)


def sse(event: str, data: str, event_id: Optional[str] = None) -> bytes:
    head = "event: %s\n" % event + ("id: %s\n" % event_id if event_id else "")
    return (head + "data: " + data + "\n\n").encode("ascii")


def delta_text(sections: dict, after: int) -> str:
    """The sections with ver > after, from the stored JSON texts (never parsed again)."""
    d, rm = [], []
    for k in sorted(sections):
        text, ver = sections[k]
        if ver > after:
            if text is None:
                rm.append(json.dumps(k))
            else:
                d.append('"%s":%s' % (k, text))
    return '{"d":{%s},"rm":[%s]}' % (",".join(d), ",".join(rm))


def meta_text(fs: Optional[dict]) -> str:
    if fs is None:
        return '{"connected":false,"age":null,"spoke_ago":null,"cmd":false}'
    age = "null" if fs["age"] is None else "%.1f" % fs["age"]
    ago = "null" if fs["spoke_ago"] is None else "%.1f" % fs["spoke_ago"]
    return '{"connected":true,"age":%s,"spoke_ago":%s,"cmd":%s}' % (age, ago, "true" if fs.get("cmd") else "false")


async def event_stream(stream: Stream, hub: Hub, accounts: Accounts, cfg, token: Optional[str], resume: Optional[tuple]):
    uid, ev, now = stream.uid, stream.ev, time.monotonic
    yield PADDING
    start = now()
    deadline = start + cfg.stream_max_duration
    next_meta, next_hb, next_check = start, start + cfg.stream_heartbeat, start + cfg.stream_recheck
    cur_epoch = cur_seq = None
    st = hub.stream_state(uid)
    if st is not None and resume is not None and resume[0] == st[0] and resume[1] <= st[1]:
        cur_epoch, cur_seq = resume          # same program connection, seq not in the future: only what is newer
    was_connected, last_key = None, None
    while not stream.closed:
        ev.clear()
        t = now()
        if t >= deadline:
            break
        out = []
        fs = hub.flight_state(uid)
        connected = fs is not None
        if not connected:
            if was_connected is not False:
                out.append(sse("offline", "{}"))
        else:
            st = hub.stream_state(uid)
            if st is not None:
                epoch, seq, sections = st
                if cur_epoch != epoch or cur_seq is None or seq < cur_seq:
                    out.append(sse("snapshot", fs["text"], "%d.%d" % (epoch, seq)))
                elif seq > cur_seq:
                    out.append(sse("delta", delta_text(sections, cur_seq), "%d.%d" % (epoch, seq)))
                cur_epoch, cur_seq = epoch, seq
        was_connected = connected
        key = (connected, bool(fs and fs.get("cmd")))
        if key != last_key or t >= next_meta:
            out.append(sse("meta", meta_text(fs)))
            last_key, next_meta = key, t + cfg.stream_meta_interval
        if t >= next_hb:
            out.append(b": hb\n\n")
            next_hb = t + cfg.stream_heartbeat
        if out:
            yield b"".join(out)
        if t >= next_check:
            sess = await run_in_threadpool(accounts.session, token)   # DB access off the event loop
            if sess is None or sess["sid"] != stream.sid or stream.closed:
                break
            next_check = t + cfg.stream_recheck
        wait = max(0.0, min(next_meta, next_hb, next_check, deadline) - now())
        try:
            await asyncio.wait_for(ev.wait(), wait)
        except asyncio.TimeoutError:
            pass


def build_stream_router(accounts: Accounts, hub: Hub, registry: StreamRegistry) -> APIRouter:
    router = APIRouter()
    cfg = accounts.cfg
    mem = MemoryRateLimiter()
    base = cfg.public_base_url

    @router.get("/api/flight/stream", include_in_schema=False)
    async def flight_stream(request: Request):
        try:
            sess = current_session(accounts, request, False)
            origin_check(base, request)   # a GET has no CSRF token: only Sec-Fetch-Site / Origin when present
        except AccountError as e:
            return _fail(e.status, e.code, e.retry_after)
        ip = client_ip_from(request.client.host if request.client else "", request.headers)
        uid = sess["uid"]
        if (not mem.allow("stream_user", str(uid), cfg.stream_open_user_minute, 60.0)
                or not mem.allow("stream_ip", ip, cfg.stream_open_ip_minute, 60.0)
                or not registry.admit(uid, ip, cfg.stream_per_user, cfg.stream_per_ip, cfg.stream_max)):
            return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "5", "Vary": "Cookie"})
        resume = parse_event_id(request.headers.get("last-event-id") or request.query_params.get("last"))
        stream = Stream(uid, sess["sid"], ip, hub.subscribe(uid))
        registry.streams.add(stream)

        def release() -> None:      # idempotent
            registry.streams.discard(stream)
            hub.unsubscribe(uid, stream.ev)
        try:
            return StreamResponse(event_stream(stream, hub, accounts, cfg, request.cookies.get(COOKIE), resume),
                                  release, cfg.stream_write_timeout, media_type="text/event-stream", headers=HEADERS)
        except BaseException:
            release()
            raise

    return router
