"""Uplink tests (step U1): WebSocket /ws/agent and /api/agent/status with the Starlette TestClient.
Real (short) timers configured through AppConfig, injectable clock for the tokens, no network.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import json
import logging
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from starlette.testclient import TestClient, WebSocketDenialResponse  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from server import wire  # noqa: E402
from server.hub import Hub  # noqa: E402
from server.test_accounts import PW, PW2, hdr, logged  # noqa: E402
from server.test_device import Env  # noqa: E402

HELLO = {"t": "hello", "v": 1, "app": "virtual-captain/test", "platform": "test"}
REPEAT = pytest.mark.parametrize("rep", range(3))  # critical timing tests run several times


class AgentEnv(Env):
    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.prog.__enter__()  # one event loop per client for the whole test
        self.web.__enter__()
        self.token = self.pair()

    @property
    def access(self):
        return self.token["access_token"]

    def close(self):
        self.prog.__exit__(None, None, None)
        self.web.__exit__(None, None, None)

    def status(self, client=None):
        return (client or self.web).get("/api/agent/status")

    def connected(self, client=None) -> bool:
        r = self.status(client)
        assert r.status_code == 200
        return r.json()["connected"]

    def ws(self, token=None, client=None, headers=None, path="/ws/agent"):
        h = {} if headers is not None else {"Authorization": "Bearer " + (token or self.access)}
        h.update(headers or {})
        return (client or self.prog).websocket_connect(path, headers=h)


@pytest.fixture
def make_env(tmp_path):
    made = []

    def make(**kw):
        e = AgentEnv(tmp_path / f"e{len(made)}", **kw)
        made.append(e)
        return e
    (tmp_path / "e0").mkdir()
    for i in range(1, 4):
        (tmp_path / f"e{i}").mkdir()
    yield make
    for e in made:
        e.close()


@pytest.fixture
def env(make_env):
    return make_env()


def wait_for(fn, expected=True, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn() == expected:
            return True
        time.sleep(0.02)
    return fn() == expected


def open_agent(e, **kw):
    """Context manager: connected, welcome read, hello sent, registered in the hub."""
    class Ctx:
        def __enter__(self_):
            self_.cm = e.ws(**kw)
            self_.ws = self_.cm.__enter__()
            self_.welcome = json.loads(self_.ws.receive_text())
            self_.ws.send_text(json.dumps(HELLO))
            return self_.ws

        def __exit__(self_, *a):
            return self_.cm.__exit__(*a)
    return Ctx()


def denied(e, **kw) -> int:
    with pytest.raises(WebSocketDenialResponse) as exc:
        with e.ws(**kw):
            pass
    return exc.value.status_code


def closed_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc:
        while True:
            ws.receive_text()
    return exc.value.code


# ---------- contract ----------

def test_wire_contract_values():
    assert (wire.PROTOCOL_VERSION, wire.HEARTBEAT, wire.STALE_AFTER, wire.MAX_MSG) == (1, 5, 12, 262144)
    assert (wire.HELLO_TIMEOUT, wire.FAMILY_RECHECK, wire.MAX_LIFETIME) == (5, 10, 86400)
    assert wire.SUPPORTED_VERSIONS == {1}
    assert (wire.CLOSE_REPLACED, wire.CLOSE_BAD_VERSION, wire.CLOSE_REVOKED, wire.CLOSE_TOO_BIG,
            wire.CLOSE_RESTART) == (4001, 4002, 4003, 4004, 4005)


def test_wire_is_stdlib_only():
    import ast
    import pathlib
    tree = ast.parse((pathlib.Path(wire.__file__)).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods <= {"__future__"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import)]


# ---------- hub ----------

def test_hub_register_replace_and_unregister():
    t = [100.0]
    hub = Hub(stale_after=12, monotonic=lambda: t[0])
    a, b = hub.new_conn("wsA", "f1"), hub.new_conn("wsB", "f1")
    assert hub.register(7, a) is None and hub.status(7) and not hub.status(8)
    assert hub.register(7, b) is a and hub.count() == 1
    assert hub.unregister(7, a) is False and hub.status(7)  # the replaced one must not evict b
    t[0] += 11.9
    assert hub.status(7)
    t[0] += 0.2
    assert not hub.status(7)  # silent past STALE: not connected even before the loop notices
    hub.touch(b)
    assert hub.status(7)
    assert hub.unregister(7, b) and hub.count() == 0 and not hub.status(7)


# ---------- connection ----------

@REPEAT
def test_connect_hello_ping_bye(env, rep):
    assert env.connected() is False
    with open_agent(env) as ws:
        assert wait_for(env.connected)
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text()) == {"t": "pong"}
        ws.send_text(json.dumps({"t": "bye"}))
        assert closed_code(ws) == 1000
    assert wait_for(env.connected, False)


def test_welcome_message(env):
    with open_agent(env) as ws:
        pass
    with env.ws() as ws:
        assert json.loads(ws.receive_text()) == {"t": "welcome", "v": 1, "heartbeat": 5, "max_msg": 262144}


def test_unknown_message_types_are_ignored_but_keep_alive(env):
    with open_agent(env) as ws:
        ws.send_text(json.dumps({"t": "something-new", "x": 1}))
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text()) == {"t": "pong"}


@pytest.mark.parametrize("kind", ["none", "basic", "empty", "wrong", "short", "query"])
def test_bad_credentials_refused_before_accept(env, kind):
    if kind == "none":
        assert denied(env, headers={}) == 401
    elif kind == "basic":
        assert denied(env, headers={"Authorization": "Basic " + env.access}) == 401
    elif kind == "empty":
        assert denied(env, headers={"Authorization": "Bearer "}) == 401
    elif kind == "wrong":
        assert denied(env, token="x" * 43) == 401
    elif kind == "short":
        assert denied(env, token="abc") == 401
    else:  # a token in the query string counts as absent
        assert denied(env, headers={}, path="/ws/agent?access_token=" + env.access) == 401
        assert denied(env, headers={}, path="/ws/agent?token=" + env.access) == 401
    assert env.connected() is False


def test_expired_and_revoked_tokens_refused(env):
    env.clock.advance(601)
    assert denied(env) == 401
    e2 = env.token
    fresh = env.refresh(e2["refresh_token"]).json()  # still usable refresh: new access
    assert fresh["access_token"] != env.access
    with env.ws(token=fresh["access_token"]) as ws:
        assert json.loads(ws.receive_text())["t"] == "welcome"
    env.prog.post("/api/device/revoke", json={"token": fresh["refresh_token"]})
    assert denied(env, token=fresh["access_token"]) == 401


def test_any_origin_header_refused(env):
    assert denied(env, headers={"Origin": "https://vc.example.test"}) == 403
    assert denied(env, headers={"Origin": "null"}) == 403
    assert denied(env, headers={"Origin": ""}) == 403
    assert env.connected() is False


@pytest.mark.parametrize("hello", [{"t": "hello", "v": 2}, {"t": "hello"}, {"t": "hello", "v": "1"},
                                   {"t": "hello", "v": True}, {"t": "hello", "v": 99}])
def test_unsupported_version_closes_4002(env, hello):
    with env.ws() as ws:
        ws.receive_text()
        ws.send_text(json.dumps(hello))
        assert closed_code(ws) == 4002
    assert env.connected() is False


def test_hello_must_come_first_and_be_valid(env):
    with env.ws() as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"t": "ping"}))
        assert closed_code(ws) == 1008
    with env.ws() as ws:
        ws.receive_text()
        ws.send_text("not json")
        assert closed_code(ws) == 1008


@REPEAT
def test_missing_hello_closes_after_timeout(make_env, rep):
    e = make_env(agent_hello_timeout=0.3)
    with e.ws() as ws:
        ws.receive_text()
        assert closed_code(ws) == 1008
    assert e.connected() is False


@REPEAT
def test_second_connection_replaces_the_first_with_4001(env, rep):
    with open_agent(env) as first:
        assert wait_for(env.connected)
        with open_agent(env) as second:
            assert closed_code(first) == 4001
            second.send_text(json.dumps({"t": "ping"}))
            assert json.loads(second.receive_text()) == {"t": "pong"}
            assert env.connected() is True  # the survivor stays registered
    assert wait_for(env.connected, False)


@REPEAT
def test_silence_past_stale_closes_and_status_goes_false(make_env, rep):
    e = make_env(agent_stale_after=0.5)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        assert closed_code(ws) == 1001
    assert e.connected() is False


def test_pings_keep_the_connection_alive_past_stale(make_env):
    e = make_env(agent_stale_after=0.6)
    with open_agent(e) as ws:
        for _ in range(6):
            time.sleep(0.2)
            ws.send_text(json.dumps({"t": "ping"}))
            assert json.loads(ws.receive_text()) == {"t": "pong"}
        assert e.connected() is True


def test_lifetime_limit_closes_normally(make_env):
    e = make_env(agent_max_lifetime=0.5, agent_stale_after=5)
    with open_agent(e) as ws:
        assert closed_code(ws) == 1000
    assert e.connected() is False


# ---------- revocation within the family re-check ----------

def _revoke_by_password(e):
    assert e.web.post("/api/auth/password", json={"current_password": PW, "new_password": PW2},
                      headers=hdr(e.csrf)).status_code == 200


def _revoke_by_endpoint(e):
    assert e.prog.post("/api/device/revoke", json={"token": e.token["refresh_token"]}).status_code == 200


def _revoke_by_deletion(e):
    assert e.web.post("/api/auth/delete", json={"password": PW}, headers=hdr(e.csrf)).status_code == 200


def _revoke_by_new_code(e):
    e.pair()  # a new authorisation revokes the previous tokens of the account


@pytest.mark.parametrize("revoke", [_revoke_by_password, _revoke_by_endpoint, _revoke_by_deletion,
                                    _revoke_by_new_code])
@REPEAT
def test_revoked_family_is_closed_within_the_recheck(make_env, revoke, rep):
    e = make_env(agent_family_recheck=0.3, agent_stale_after=5)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        t0 = time.monotonic()
        revoke(e)
        assert closed_code(ws) == 4003
        assert time.monotonic() - t0 < 2.0
    assert wait_for(lambda: e.app.state.hub.count(), 0)


def test_rotation_keeps_the_connection_open(make_env):
    e = make_env(agent_family_recheck=0.2, agent_stale_after=5)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        assert e.refresh(e.token["refresh_token"]).status_code == 200  # rotates, same family
        time.sleep(0.6)  # at least two re-checks
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text()) == {"t": "pong"}


def test_verify_access_and_family_active(env):
    devices = env.app.state.devices
    uid, family = devices.verify_access(env.access)
    assert uid == devices.verify_access_token(env.access) and family
    assert devices.family_active(family) is True
    assert devices.verify_access(env.token["refresh_token"]) is None
    assert devices.verify_access("x" * 40) is None and devices.verify_access(None) is None
    env.clock.advance(601)
    assert devices.verify_access(env.access) is None
    assert devices.family_active(family) is True  # refresh still valid
    env.clock.advance(91 * 86400)
    assert devices.family_active(family) is False


# ---------- status isolation ----------

def second_account(e):
    from fastapi.testclient import TestClient as TC
    web2 = TC(e.app, base_url="https://testserver", client=("203.0.113.77", 4000))
    web2.__enter__()
    csrf2 = logged(web2, e.mailer, email="b@example.test")
    s = e.start()
    assert e.lookup(s["user_code"], client=web2, csrf=csrf2).status_code == 200
    assert web2.post("/api/device/approve", json={"user_code": s["user_code"], "confirm": s["user_code"][-4:]},
                     headers=hdr(csrf2)).status_code == 200
    r = e.poll(s["device_code"])
    assert r.status_code == 200
    return web2, r.json()


def test_status_is_isolated_between_accounts(env):
    web2, tok_b = second_account(env)
    try:
        assert env.connected() is False and env.connected(web2) is False
        with open_agent(env):
            assert wait_for(env.connected) and env.connected(web2) is False
            with open_agent(env, token=tok_b["access_token"]):
                assert wait_for(lambda: env.connected(web2))
                assert env.connected() is True
            assert wait_for(lambda: env.connected(web2), False)
            assert env.connected() is True
    finally:
        web2.__exit__(None, None, None)


def test_status_needs_a_session_and_is_never_cached(env):
    from fastapi.testclient import TestClient as TC
    anon = TC(env.app, base_url="https://testserver")
    r = anon.get("/api/agent/status")
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    r = env.status()
    assert r.status_code == 200 and r.json() == {"connected": False}
    assert r.headers["cache-control"] == "no-store"
    assert "content-security-policy" in r.headers
    # an access token is not a session
    assert anon.get("/api/agent/status", headers={"Authorization": "Bearer " + env.access}).status_code == 401


# ---------- limits ----------

def test_oversized_message_closes_4004(env):
    with open_agent(env) as ws:
        ws.send_text("x" * (wire.MAX_MSG + 1))
        assert closed_code(ws) == 4004
    with open_agent(env) as ws:
        ws.send_text(json.dumps({"t": "ping", "pad": "y" * (wire.MAX_MSG - 60)}))  # still within the limit
        assert json.loads(ws.receive_text()) == {"t": "pong"}


def test_oversized_hello_closes_4004(env):
    with env.ws() as ws:
        ws.receive_text()
        ws.send_text("x" * (wire.MAX_MSG + 1))
        assert closed_code(ws) == 4004


def test_message_flood_closes_4004(make_env):
    e = make_env(agent_msgs_per_second=3)
    with open_agent(e) as ws:
        for _ in range(12):
            ws.send_text(json.dumps({"t": "ping"}))
        assert closed_code(ws) == 4004


def test_binary_frame_is_a_protocol_violation(env):
    with open_agent(env) as ws:
        ws.send_bytes(b"\x00\x01")
        assert closed_code(ws) == 1008


def test_agent_ceiling_503(make_env):
    e = make_env(agent_max=1)
    web2, tok_b = second_account(e)
    try:
        with open_agent(e):
            assert wait_for(e.connected)
            assert denied(e, token=tok_b["access_token"]) == 503
        assert wait_for(lambda: e.app.state.hub.count(), 0)
        with open_agent(e, token=tok_b["access_token"]):
            assert wait_for(lambda: e.connected(web2))
    finally:
        web2.__exit__(None, None, None)


def test_per_ip_limit_429_counts_failures_too(make_env):
    e = make_env(agent_ip_per_minute=3)
    for _ in range(3):
        assert denied(e, token="x" * 43) == 401
    assert denied(e) == 429  # even a valid token: the IP is over the limit
    assert denied(e, token="x" * 43) == 429


def test_forwarded_for_is_trusted_only_from_loopback(make_env):
    e = make_env(agent_ip_per_minute=1)
    lo = TestClient(e.app, base_url="https://testserver", client=("127.0.0.1", 1))
    other = TestClient(e.app, base_url="https://testserver", client=("203.0.113.50", 1))
    bad = "x" * 43
    with lo, other:
        # behind the local Apache: the LAST X-Forwarded-For value is the client
        assert denied(e, client=lo, token=bad, headers={"X-Forwarded-For": "198.51.100.1"}) == 401
        assert denied(e, client=lo, token=bad, headers={"X-Forwarded-For": "198.51.100.1"}) == 429
        assert denied(e, client=lo, token=bad, headers={"X-Forwarded-For": "198.51.100.2"}) == 401
        assert denied(e, client=lo, token=bad, headers={"X-Forwarded-For": "198.51.100.9, 198.51.100.2"}) == 429
        # any other peer: the header is ignored, one bucket whatever it claims
        assert denied(e, client=other, token=bad, headers={"X-Forwarded-For": "198.51.100.3"}) == 401
        assert denied(e, client=other, token=bad, headers={"X-Forwarded-For": "198.51.100.4"}) == 429


# ---------- logs ----------

def test_logs_hold_no_token_or_user_data(env, caplog):
    secrets_ = [env.access, env.token["refresh_token"], "a@example.test", "198.51.100.7", "203.0.113.9"]
    with caplog.at_level(logging.DEBUG):
        with open_agent(env) as ws:
            assert wait_for(env.connected)
            ws.send_text("not json")
            closed_code(ws)
        denied(env, token="x" * 43)
        denied(env, headers={"Origin": "https://evil.example"})
        with env.ws() as ws:
            ws.receive_text()
            ws.send_text("x" * (wire.MAX_MSG + 1))
            closed_code(ws)
    text = caplog.text
    assert "agent connected" in text
    for s in secrets_:
        assert s not in text


# ---------- mounting and headers ----------

def test_ws_route_exists_only_when_the_api_is_mounted(tmp_path):
    from server.app import create_app
    from server.config import AppConfig
    app = create_app(AppConfig(db_path=str(tmp_path / "x.db")))
    c = TestClient(app)
    with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
        with c.websocket_connect("/ws/agent", headers={"Authorization": "Bearer " + "x" * 40}):
            pass
    assert c.get("/api/agent/status").status_code == 404


def test_http_security_headers_still_apply_next_to_websockets(env):
    r = env.web.get("/waiting")
    assert r.status_code == 200 and "default-src 'none'" in r.headers["content-security-policy"]
    with open_agent(env):
        assert env.web.get("/healthz").status_code == 200


def test_uvicorn_finds_the_websockets_library():
    """uvicorn without [standard] needs `websockets` for the WebSocket: the config must resolve it."""
    pytest.importorskip("uvicorn")
    pytest.importorskip("websockets")
    import uvicorn
    from uvicorn.config import Config
    cfg = Config("server.app:app", ws="auto", ws_max_size=262144)
    cfg.load()
    assert "websockets" in cfg.ws_protocol_class.__module__
    assert cfg.ws_max_size == 262144 and uvicorn


# ---------- real uvicorn on loopback (legacy websockets implementation, ws_max_size as in the service) ----------

def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live(make_env):
    pytest.importorskip("uvicorn")
    pytest.importorskip("websockets")
    import threading
    import uvicorn
    e = make_env()
    try:
        port = _free_port()
    except OSError:
        pytest.skip("no loopback port available")
    server = uvicorn.Server(uvicorn.Config(e.app, host="127.0.0.1", port=port, ws="auto", ws_max_size=262144,
                                           log_level="critical", lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        pytest.skip("uvicorn did not start")
    yield e, f"ws://127.0.0.1:{port}/ws/agent"
    server.should_exit = True
    th.join(5)


def _real_connect(url, headers=None):
    from websockets.sync.client import connect
    return connect(url, additional_headers=headers or {}, open_timeout=5, close_timeout=1, max_size=None)


def test_real_uvicorn_denials_return_http_statuses(live):
    from websockets.exceptions import InvalidStatus
    e, url = live
    good = {"Authorization": "Bearer " + e.access}
    for headers, path, status in (({}, "", 401), ({"Authorization": "Bearer " + "x" * 43}, "", 401),
                                  (good | {"Origin": "https://evil.example"}, "", 403),
                                  ({}, "?access_token=" + e.access, 401)):
        with pytest.raises(InvalidStatus) as exc:
            _real_connect(url + path, headers)
        assert exc.value.response.status_code == status


def test_real_uvicorn_welcome_oversize_and_status(live):
    from websockets.exceptions import ConnectionClosed
    e, url = live
    ws = _real_connect(url, {"Authorization": "Bearer " + e.access})
    assert json.loads(ws.recv(5)) == wire.welcome_message()
    ws.send(json.dumps(HELLO))
    assert wait_for(e.connected)
    ws.send(json.dumps({"t": "ping"}))
    assert json.loads(ws.recv(5)) == {"t": "pong"}
    ws.send("x" * (wire.MAX_MSG + 1))
    with pytest.raises(ConnectionClosed):
        for _ in range(10):
            ws.recv(5)
    assert wait_for(e.connected, False)


# ---------- fix round: bounded closes and sends ----------

class _HangingWs:
    async def close(self, code=1000):
        import asyncio
        await asyncio.sleep(60)


def test_unresponsive_old_connection_does_not_stall_the_new_one(env):
    hub = env.app.state.hub
    uid, family = env.app.state.devices.verify_access(env.access)
    hub.register(uid, hub.new_conn(_HangingWs(), family))
    t0 = time.monotonic()
    with open_agent(env) as ws:
        ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(ws.receive_text()) == {"t": "pong"}
        assert time.monotonic() - t0 < 4.0  # the stuck close is abandoned after 2 s, never awaited forever
        assert env.connected() is True


def test_close_and_send_helpers_are_time_bounded():
    import asyncio
    from server import api_agent

    async def go():
        t0 = time.monotonic()
        await api_agent._close(_HangingWs(), 4001, 0.2)
        return time.monotonic() - t0
    assert asyncio.run(go()) < 1.0
    src = open(api_agent.__file__, encoding="utf-8").read()
    assert src.count("asyncio.wait_for(") >= 4 and api_agent.SEND_TIMEOUT == 5.0
    assert "await ws.send_text" not in src and "await websocket.send_text" not in src
