"""SSE stream GET /api/flight/stream (U4, step C4): auth, events, resume, caps, isolation, leaks, revocation, kill switch.
The stream is driven at the ASGI level inside one asyncio loop (the TestClient is blocking for long responses and
httpx buffers the body): the account is created with the TestClient, the 'program' is a fake connection registered
directly in the hub. Short timers through AppConfig, no network.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import app as srv  # noqa: E402
from server.api_auth import COOKIE  # noqa: E402
from server.api_stream import PADDING, delta_text, parse_event_id  # noqa: E402
from server.config import load_app_config  # noqa: E402
from server.test_accounts import PW, PW2, hdr, logged, login  # noqa: E402,F401
from server.test_agent import make_env, second_account  # noqa: E402,F401

PATH = "/api/flight/stream"
FAST = dict(stream_meta_interval=0.1, stream_heartbeat=0.2, stream_recheck=30.0, stream_max_duration=30.0)


def run(coro):
    async def guarded():
        return await asyncio.wait_for(coro, 30)
    return asyncio.run(guarded())


def view(marker="M1", **extra):
    d = {"phase": "flying", "log": [marker], "spoke_n": 1, "fields": {"origin": "LIMC/17R"}}
    d.update(extra)
    return d


class FakeWS:
    async def close(self, code=None):
        pass


def program(e, uid, d=None, seq=1):
    hub = e.app.state.hub
    conn = hub.new_conn(FakeWS(), "fam")
    hub.register(uid, conn)
    assert hub.store_snapshot(conn, seq, d or view())
    return conn


def delta(e, conn, seq, changed: dict, removed=()):
    new = dict(conn.view)
    new.update(changed)
    for k in removed:
        new.pop(k, None)
    assert e.app.state.hub.store_delta(conn, seq, new, list(changed), list(removed))


def cookie_of(client):
    return client.cookies.get(COOKIE)


def uid_of(e, cookie):
    return e.app.state.accounts.session(cookie)["uid"]


class Sse:
    """One HTTP request to the app, at the ASGI level; the body arrives as it is produced."""
    def __init__(self, app, cookie, ip="203.0.113.9", headers=None, query=""):
        hs = [(b"host", b"vc.example.test")]
        if cookie:
            hs.append((b"cookie", ("%s=%s" % (COOKIE, cookie)).encode()))
        hs += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        self.app, self.q, self.buf, self.gone = app, asyncio.Queue(), b"", asyncio.Event()
        self.scope = {"type": "http", "method": "GET", "path": PATH, "query_string": query.lstrip("?").encode(), "headers": hs,
                      "client": (ip, 1000), "server": ("vc.example.test", 443), "scheme": "https",
                      "http_version": "1.1", "root_path": ""}
        self.status, self.headers, self.error, self.done = None, {}, None, False

    async def _run(self):
        async def receive():
            await self.gone.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            self.q.put_nowait(msg)
        try:
            await self.app(self.scope, receive, send)
        except BaseException as exc:  # noqa: BLE001
            self.error = exc
        finally:
            self.q.put_nowait(None)

    async def open(self):
        self.task = asyncio.ensure_future(self._run())
        start = await asyncio.wait_for(self.q.get(), 5)
        self.status = start["status"]
        self.headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        return self

    async def _block(self, timeout):
        while b"\n\n" not in self.buf:
            msg = await asyncio.wait_for(self.q.get(), timeout)
            if msg is None:
                self.done = True
                return None
            self.buf += msg.get("body", b"")
        block, _, self.buf = self.buf.partition(b"\n\n")
        return block.decode()

    async def next(self, timeout=3.0):
        """Next event as (name, id, data), or ("hb",) for the heartbeat; padding/retry skipped; None when the stream ended."""
        while True:
            block = await self._block(timeout)
            if block is None:
                return None
            if block.startswith(":"):
                if block[1:].strip() == "hb":
                    return ("hb",)
                continue
            if block.startswith("retry:"):
                continue
            f = dict(line.split(": ", 1) for line in block.split("\n"))
            return (f["event"], f.get("id"), json.loads(f["data"]) if f["data"].startswith(("{", "[")) else f["data"])

    async def until(self, name, timeout=3.0):
        end = asyncio.get_running_loop().time() + timeout
        while True:
            ev = await self.next(max(0.05, end - asyncio.get_running_loop().time()))
            assert ev is not None, "stream ended before %s" % name
            if ev[0] == name:
                return ev

    async def ended(self, timeout=3.0) -> bool:
        """True if the stream ends within `timeout` seconds in total (events keep arriving meanwhile: they are skipped)."""
        end = asyncio.get_running_loop().time() + timeout
        try:
            while True:
                left = end - asyncio.get_running_loop().time()
                if left <= 0 or await self._block(left) is None:
                    return left > 0
        except asyncio.TimeoutError:
            return False

    async def close(self):
        self.gone.set()
        try:
            await asyncio.wait_for(self.task, 5)
        except asyncio.TimeoutError:
            self.task.cancel()


def mk(make_env, **kw):
    return make_env(**{**FAST, **kw})


# ---------- auth and headers ----------

def test_401_without_session_and_headers(make_env):
    e = mk(make_env)

    async def go():
        r = await Sse(e.app, None).open()
        assert r.status == 401
        assert r.headers["cache-control"] == "no-store" and "content-security-policy" in r.headers
        r2 = await Sse(e.app, "x" * 43).open()
        assert r2.status == 401
        await r.close(), await r2.close()
    run(go())


def test_stream_headers_and_padding(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))

    async def go():
        s = await Sse(e.app, ck).open()
        assert s.status == 200
        assert s.headers["content-type"].startswith("text/event-stream")
        assert s.headers["cache-control"] == "no-store" and s.headers["vary"] == "Cookie"
        assert "no-store" in s.headers["cache-control"] and s.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" in s.headers
        first = await asyncio.wait_for(s.q.get(), 3)
        assert len(first["body"]) >= len(PADDING) >= 2048
        await s.close()
    run(go())


def test_sec_fetch_site_and_origin_checked_when_present(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))

    async def go():
        for h, code in (({"Sec-Fetch-Site": "cross-site"}, 403), ({"Sec-Fetch-Site": "same-site"}, 403),
                        ({"Origin": "https://evil.example.test"}, 403), ({"Sec-Fetch-Site": "same-origin"}, 200),
                        ({"Origin": "https://vc.example.test"}, 200), ({}, 200)):
            s = await Sse(e.app, ck, headers=h).open()
            assert s.status == code, h
            await s.close()
    run(go())


# ---------- events ----------

def test_snapshot_then_meta_then_delta(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    uid = uid_of(e, ck)
    conn = program(e, uid, view("M1"))

    async def go():
        s = await Sse(e.app, ck).open()
        name, eid, data = await s.until("snapshot")
        assert eid == "%d.1" % conn.epoch and data == view("M1")
        meta = (await s.until("meta"))[2]
        assert meta["connected"] is True and meta["cmd"] is False and "age" in meta and "spoke_ago" in meta
        delta(e, conn, 2, {"log": ["M2"]}, ["fields"])
        name, eid, data = await s.until("delta")
        assert eid == "%d.2" % conn.epoch and data == {"d": {"log": ["M2"]}, "rm": ["fields"]}
        delta(e, conn, 3, {"phase": "landed"})
        assert (await s.until("delta"))[2] == {"d": {"phase": "landed"}, "rm": []}
        await s.close()
    run(go())


def test_meta_is_periodic(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("snapshot")
        await s.until("meta")
        await s.until("meta")
        await s.until("meta")
        await s.close()
    run(go())


def test_slow_client_is_coalesced_without_history(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    conn = program(e, uid_of(e, ck))

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("snapshot")
        for i in range(2, 8):   # several changes in one go (no await in between): one delta with the newest values
            delta(e, conn, i, {"log": ["M%d" % i]})
        _, eid, data = await s.until("delta")
        assert eid == "%d.7" % conn.epoch and data["d"] == {"log": ["M7"]}
        await s.close()
    run(go())


def test_offline_event_and_back(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    uid = uid_of(e, ck)
    conn = program(e, uid)

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("snapshot")
        e.app.state.hub.unregister(uid, conn)
        await s.until("offline")
        assert (await s.until("meta"))[2]["connected"] is False
        conn2 = program(e, uid, view("M9"))
        _, eid, data = await s.until("snapshot")     # a new program connection: new epoch, full snapshot
        assert eid == "%d.1" % conn2.epoch and data == view("M9")
        await s.close()
    run(go())


def test_offline_from_the_start(make_env):
    e = mk(make_env)

    async def go():
        s = await Sse(e.app, cookie_of(e.web)).open()
        await s.until("offline")
        assert (await s.until("meta"))[2]["connected"] is False
        await s.close()
    run(go())


def test_heartbeat_comment(make_env):
    e = mk(make_env, stream_meta_interval=5.0, stream_heartbeat=0.1)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("hb")
        await s.until("hb")
        await s.close()
    run(go())


# ---------- resume ----------

def test_resume_with_last_event_id(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    conn = program(e, uid_of(e, ck), view("M1"))
    delta(e, conn, 2, {"log": ["M2"]})
    delta(e, conn, 3, {"phase": "landed"})
    ep = conn.epoch

    async def first(header=None, query=""):
        s = await Sse(e.app, ck, headers=header, query=query).open()
        ev = await s.next()
        await s.close()
        return ev

    async def go():
        ev = await first({"Last-Event-ID": "%d.1" % ep})            # same epoch, older seq: only newer sections
        assert ev[0] == "delta" and ev[1] == "%d.3" % ep and ev[2]["d"] == {"log": ["M2"], "phase": "landed"}
        ev = await first({"Last-Event-ID": "%d.2" % ep})
        assert ev[0] == "delta" and ev[2]["d"] == {"phase": "landed"}
        ev = await first(query="?last=%d.2" % ep)                    # manual re-creation from the browser
        assert ev[0] == "delta" and ev[2]["d"] == {"phase": "landed"}
        ev = await first({"Last-Event-ID": "%d.9" % ep})            # a seq in the future
        assert ev[0] == "snapshot"
        ev = await first({"Last-Event-ID": "%d.1" % (ep + 1)})      # another epoch
        assert ev[0] == "snapshot"
        for bad in ("", "x", "1", "1.x", "-1.2", "1.2.3", "9" * 40):
            assert (await first({"Last-Event-ID": bad}))[0] == "snapshot", bad
        s = await Sse(e.app, ck, headers={"Last-Event-ID": "%d.3" % ep}).open()   # nothing newer: only meta
        assert (await s.next())[0] == "meta"
        await s.close()
        # the header wins over ?last=
        ev = await first({"Last-Event-ID": "%d.1" % ep}, query="?last=%d.9" % ep)
        assert ev[0] == "delta"
    run(go())


def test_parse_event_id_and_delta_text():
    assert parse_event_id("12.34") == (12, 34)
    assert parse_event_id(None) is None and parse_event_id("12") is None and parse_event_id("a.1") is None
    secs = {"a": ('1', 1), "b": ('{"x":2}', 5), "c": (None, 6)}
    assert json.loads(delta_text(secs, 1)) == {"d": {"b": {"x": 2}}, "rm": ["c"]}
    assert json.loads(delta_text(secs, 6)) == {"d": {}, "rm": []}


# ---------- caps ----------

def test_fourth_stream_of_a_user_is_429_and_slot_is_freed(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)

    async def go():
        ss = [await Sse(e.app, ck).open() for _ in range(3)]
        assert [s.status for s in ss] == [200] * 3
        r = await Sse(e.app, ck).open()
        assert r.status == 429 and r.headers["cache-control"] == "no-store" and r.headers["retry-after"]
        await r.close()
        await ss[0].close()
        await asyncio.sleep(0.05)
        again = await Sse(e.app, ck).open()
        assert again.status == 200
        for s in ss[1:] + [again]:
            await s.close()
    run(go())


def test_per_ip_and_global_caps(make_env):
    e = mk(make_env, stream_per_user=10, stream_per_ip=2, stream_max=3)
    ck = cookie_of(e.web)
    web2, _ = second_account(e)
    ck2 = cookie_of(web2)

    async def go():
        a = [await Sse(e.app, ck, ip="198.51.100.1").open() for _ in range(2)]
        assert (await Sse(e.app, ck, ip="198.51.100.1").open()).status == 429          # IP cap
        b = await Sse(e.app, ck2, ip="198.51.100.2").open()                            # another IP: ok (3 of 3)
        assert b.status == 200
        assert (await Sse(e.app, ck2, ip="198.51.100.3").open()).status == 429         # global cap
        for s in a + [b]:
            await s.close()
    run(go())


# ---------- isolation and leaks ----------

def test_accounts_are_isolated(make_env):
    e = mk(make_env)
    web2, _ = second_account(e)
    ca, cb = cookie_of(e.web), cookie_of(web2)
    pa = program(e, uid_of(e, ca), view("MARKER-A"))
    pb = program(e, uid_of(e, cb), view("MARKER-B"))

    async def go():
        sa, sb = await Sse(e.app, ca).open(), await Sse(e.app, cb, ip="203.0.113.77").open()
        assert (await sa.until("snapshot"))[2]["log"] == ["MARKER-A"]
        assert (await sb.until("snapshot"))[2]["log"] == ["MARKER-B"]
        delta(e, pb, 2, {"log": ["MARKER-B2"]})
        assert (await sb.until("delta"))[2]["d"] == {"log": ["MARKER-B2"]}
        delta(e, pa, 2, {"log": ["MARKER-A2"]})
        assert (await sa.until("delta"))[2]["d"] == {"log": ["MARKER-A2"]}
        # whatever else A received never mentions B
        for _ in range(3):
            ev = await sa.next()
            assert "MARKER-B" not in json.dumps(ev)
        await sa.close(), await sb.close()
    run(go())


def test_no_leak_after_100_open_close(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))
    hub, reg = e.app.state.hub, e.app.state.streams

    async def go():
        for i in range(100):
            s = await Sse(e.app, ck).open()
            if i % 2:
                await s.next()
            await s.close()
        await asyncio.sleep(0.1)
        assert not reg.streams and not hub._subs
        assert asyncio.all_tasks() == {asyncio.current_task()}
    run(go())


def test_blocked_write_ends_the_stream_and_frees_slot_and_subscription(make_env):
    e = mk(make_env, stream_write_timeout=0.3)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))
    hub, reg = e.app.state.hub, e.app.state.streams

    async def go():
        sent = []

        async def run_blocked():
            s = Sse(e.app, ck)

            async def receive():
                await asyncio.Event().wait()   # the client stays connected but stops reading

            async def send(msg):
                sent.append(msg["type"])
                if msg["type"] == "http.response.body":
                    await asyncio.Event().wait()   # the socket buffer is full: this write never completes
            await e.app(s.scope, receive, send)
        task = asyncio.ensure_future(run_blocked())
        await asyncio.sleep(0.1)
        assert len(reg.streams) == 1 and len(hub._subs) == 1
        await asyncio.wait_for(task, 3)             # ends by itself, no error reaches the server
        assert not reg.streams and not hub._subs and "http.response.start" in sent
        await asyncio.sleep(0.05)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    run(go())


# ---------- end of the stream ----------

def test_revocations_close_the_streams(make_env):
    e = mk(make_env)
    ck = cookie_of(e.web)
    acc = e.app.state.accounts
    sess = acc.session(ck)

    async def opened(n=2):
        return [await Sse(e.app, ck).open() for _ in range(n)]

    async def go():
        ss = await opened()
        await acc._notify_revoked(sess["uid"])                          # the callback of every revocation
        assert all([await s.ended() for s in ss])
        assert not e.app.state.streams.streams and not e.app.state.hub._subs
        ss = await opened(1)
        await acc.change_password(sess, PW, PW2)                         # real change of password
        assert await ss[0].ended()
    run(go())


def test_delete_account_and_reset_close_the_stream(make_env):
    e = mk(make_env)
    web2, _ = second_account(e)
    cb = cookie_of(web2)
    acc = e.app.state.accounts
    sess = acc.session(cb)
    web2.post("/api/auth/forgot", json={"email": "b@example.test"})
    token = e.mailer.token()

    async def go():
        s = await Sse(e.app, cb, ip="203.0.113.77").open()
        await acc.reset_password(token, PW2, "203.0.113.77")
        assert await s.ended()
        s = await Sse(e.app, cookie_of(e.web)).open()
        await acc.delete_account(acc.session(cookie_of(e.web)), PW)
        assert await s.ended()
    run(go())


def test_logout_closes_only_the_stream_of_that_session(make_env):
    e = mk(make_env)
    c2 = TestClient(e.app, base_url="https://vc.example.test", client=("203.0.113.9", 4001))
    c2.__enter__()
    try:
        assert login(c2, "a@example.test", PW).status_code == 200
        acc = e.app.state.accounts
        s1c, s2c = cookie_of(e.web), cookie_of(c2)
        assert s1c != s2c

        async def go():
            s1, s2 = await Sse(e.app, s1c).open(), await Sse(e.app, s2c).open()
            acc.logout(acc.session(s1c))
            assert await s1.ended()
            assert not await s2.ended(0.4)
            await s2.close()
        run(go())
    finally:
        c2.__exit__(None, None, None)


def test_session_removed_behind_our_back_is_noticed_by_the_recheck(make_env):
    e = mk(make_env, stream_recheck=0.1)
    ck = cookie_of(e.web)

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("meta")
        with e.app.state.accounts.db.tx() as c:
            c.execute("DELETE FROM sessions")
        assert await s.ended()
    run(go())


def test_max_duration_closes_cleanly_and_frees_everything(make_env):
    e = mk(make_env, stream_max_duration=0.3)
    ck = cookie_of(e.web)

    async def go():
        s = await Sse(e.app, ck).open()
        assert await s.ended(3.0)
        assert s.error is None
        await asyncio.sleep(0.05)
        assert not e.app.state.streams.streams and not e.app.state.hub._subs
    run(go())


# ---------- logs, kill switch, middleware ----------

def test_nothing_from_the_flight_is_logged(make_env, caplog):
    e = mk(make_env)
    ck = cookie_of(e.web)
    conn = program(e, uid_of(e, ck), view("SECRET-MARKER-1"))

    async def go():
        s = await Sse(e.app, ck).open()
        await s.until("snapshot")
        delta(e, conn, 2, {"log": ["SECRET-MARKER-2"]})
        await s.until("delta")
        await s.close()
        (await Sse(e.app, None).open())
    with caplog.at_level(logging.DEBUG):
        run(go())
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRET-MARKER" not in text and ck not in text


def test_kill_switch_answers_404_and_polling_still_works(make_env):
    e = make_env(stream=False)
    ck = cookie_of(e.web)
    program(e, uid_of(e, ck))

    async def go():
        s = await Sse(e.app, ck).open()
        assert s.status == 404
        await s.close()
    run(go())
    assert e.web.get("/api/flight/state").json()["connected"] is True


def test_kill_switch_from_the_environment():
    assert load_app_config({}).stream is True
    assert load_app_config({"VC_STREAM": "off"}).stream is False
    assert load_app_config({"VC_STREAM": "OFF"}).stream is False
    assert load_app_config({"VC_STREAM": "on"}).stream is True


def test_security_middleware_is_pure_asgi_and_keeps_the_headers(make_env):
    e = mk(make_env)
    from starlette.middleware.base import BaseHTTPMiddleware
    classes = [m.cls for m in e.app.user_middleware]
    assert srv.SecurityHeadersMiddleware in classes and BaseHTTPMiddleware not in classes

    async def go():
        sent = []

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"cache-control", b"max-age=99"), (b"x-keep", b"1")]})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(m):
            sent.append(m)
        mw = srv.SecurityHeadersMiddleware(inner)
        await mw({"type": "http", "path": "/api/flight/x", "headers": []}, None, send)
        h = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
        assert h["cache-control"] == "no-store" and h["x-keep"] == "1"
        assert all(h[k.lower()] == v for k, v in srv.SECURITY_HEADERS.items() if k != "Cache-Control")
        assert sent[1]["body"] == b"ok"
        sent.clear()
        await mw({"type": "http", "path": "/x.css", "headers": []}, None, send)
        assert dict((k.decode(), v.decode()) for k, v in sent[0]["headers"])["cache-control"] == "no-cache"
    run(go())
