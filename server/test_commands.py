"""Remote commands (step U3, delivery 1): contract in wire.py, POST /api/flight/cmd/<name>, hub, isolation between
accounts, limits, nothing in logs. TestClient with a fake program (the WebSocket), real short timers, no network.

The POST of a command waits for the fake program: it runs in a thread on the SAME client (and event loop) as the
WebSocket, exactly like the production process (one loop)."""
from __future__ import annotations

import json
import logging
import re
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from server import api_agent, wire  # noqa: E402
from server.test_accounts import hdr  # noqa: E402
from server.test_agent import (_real_connect, closed_code, live, make_env, open_agent, second_account, wait_for)  # noqa: E402,F401

REPEAT = pytest.mark.parametrize("rep", range(3))
CMD_HELLO = {"t": "hello", "v": 1, "app": "virtual-captain/test", "platform": "test", "caps": ["cmd"], "cv": 1}
ID_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture
def env(make_env):
    return make_env()


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch):
    monkeypatch.setitem(wire.CMD_TIMEOUT, "default", 0.6)
    monkeypatch.setitem(wire.CMD_TIMEOUT, "load-plan", 0.9)


class Pc:
    """A fake program: connected with the cmd capability (or not)."""

    def __init__(self, env, hello=None, token=None):
        self.env, self.hello, self.token = env, CMD_HELLO if hello is None else hello, token

    def __enter__(self):
        self.cm = self.env.ws(token=self.token)
        self.ws = self.cm.__enter__()
        self.welcome = json.loads(self.ws.receive_text())
        self.ws.send_text(json.dumps(self.hello))
        assert wait_for(lambda: self.env.status().json()["connected"])
        return self

    def __exit__(self, *a):
        return self.cm.__exit__(*a)

    def recv_cmd(self):
        msg = json.loads(self.ws.receive_text())
        assert msg["t"] == "cmd", msg
        assert ID_RE.match(msg["id"])
        return msg

    def answer(self, cmd_id, **body):
        self.ws.send_text(json.dumps({"t": "result", "id": cmd_id, **body}))

    def ping(self):
        self.ws.send_text(json.dumps({"t": "ping"}))
        assert json.loads(self.ws.receive_text()) == {"t": "pong"}


def post(e, name, body=None, client=None, csrf=None, headers=None, **kw):
    h = {} if csrf is False else hdr(csrf or e.csrf)
    h.update(headers or {})
    return (client or e.web).post("/api/flight/cmd/" + name, json={} if body is None else body, headers=h, **kw)


def threaded_post(e, name, body, csrf=None, cookie_from=None):
    """POST on the program's client/loop in a thread: returns (thread, box)."""
    box = []
    src = cookie_from or e.web
    h = {"Cookie": "__Host-session=" + src.cookies.get("__Host-session"), "X-CSRF-Token": csrf or e.csrf}
    t = threading.Thread(target=lambda: box.append(e.prog.post("/api/flight/cmd/" + name, json=body, headers=h)), daemon=True)
    t.start()
    return t, box


def finish(t, box):
    t.join(8)
    assert box, "the POST did not answer"
    return box[0]


def roundtrip(e, pc, name, body, **answer):
    t, box = threaded_post(e, name, body)
    msg = pc.recv_cmd()
    pc.answer(msg["id"], **answer)
    return msg, finish(t, box)


# ---------------------------------------------------------------- the contract (wire.py)

def test_constants():
    assert wire.ENABLED_COMMANDS == ("fields", "clearance", "checklist-phase", "phase", "load-plan", "sim-retry")
    assert set(wire.ENABLED_COMMANDS) <= set(wire.REMOTE_COMMANDS)
    assert wire.LOCAL_ONLY == ("simdll-fetch", "thrust-cal")
    assert not set(wire.LOCAL_ONLY) & set(wire.REMOTE_COMMANDS)
    assert wire.CMD_TIMEOUT == {"default": 8.0, "load-plan": 20.0} or wire.CMD_TIMEOUT["default"] <= 8.0
    assert (wire.MAX_CMD_DATA, wire.MAX_CMD_BYTES, wire.MAX_RESULT_BYTES) == (2048, 4096, 1024)


def test_one_simbrief_pattern_shared_with_accounts():
    from server import accounts
    assert accounts.SIMBRIEF_RE is wire.SIMBRIEF_RE
    for ok in ("12345", "Mario_Rossi", "a b.c-d"):
        assert wire.validate_command("load-plan", {"simbrief": ok}) == {"simbrief": ok}
    for bad in ("", "   ", " \t", "x" * 41, "a;b", "é", "a\nb", "../x", 5, None, True):
        with pytest.raises(wire.CommandError):
            wire.validate_command("load-plan", {"simbrief": bad})


VALID = [
    ("fields", {}),
    ("fields", {"v1": "150", "origin": "LIMC/17R", "atis_qnh": "1013"}),
    ("fields", {"mode": "training", "checklist_mode": "simplified", "reset": ["v1", "zfw"]}),
    ("clearance", {"reset": True}),
    ("clearance", {"c": "EDDF", "w": "17R", "r": "A B C", "a": "FL100", "f": "121.800", "t": "1234"}),
    ("clearance", {"t": ""}),
    ("checklist-phase", {"phase": "taxi"}),
    ("checklist-phase", {"step": 1}),
    ("checklist-phase", {"step": -1}),
    ("phase", {"phase": "flying"}),
    ("phase", {"phase": "briefing", "confirm": True}),
    ("load-plan", {}),
    ("load-plan", {"simbrief": "123456"}),
    ("sim-retry", {}),
]
INVALID = [
    ("fields", {"simbrief": "123"}), ("fields", {"nope": "x"}), ("fields", {"v1": 150}), ("fields", {"v1": None}),
    ("fields", {"v1": True}), ("fields", {"v1": "x" * 41}), ("fields", {"v1": "a\nb"}), ("fields", {"v1": "a\x00"}),
    ("fields", {"mode": "silent"}), ("fields", {"mode": 1}), ("fields", {"checklist_mode": "x"}),
    ("fields", {"reset": "v1"}), ("fields", {"reset": ["origin"]}), ("fields", {"reset": ["v1"] * 9}), ("fields", {"reset": [1]}),
    ("clearance", {"reset": True, "c": "X"}), ("clearance", {"reset": 1}), ("clearance", {"reset": False}), ("clearance", {}),
    ("clearance", {"x": "1"}), ("clearance", {"t": "12345"}), ("clearance", {"t": "1238"}), ("clearance", {"t": 1234}),
    ("clearance", {"c": "x" * 41}), ("clearance", {"w": "x" * 13}), ("clearance", {"r": "x" * 401}), ("clearance", {"a": "x" * 21}),
    ("clearance", {"f": "x" * 21}), ("clearance", {"c": ["a"]}),
    ("checklist-phase", {}), ("checklist-phase", {"phase": "a", "step": 1}), ("checklist-phase", {"step": 2}),
    ("checklist-phase", {"step": 0}), ("checklist-phase", {"step": True}), ("checklist-phase", {"step": 1.0}),
    ("checklist-phase", {"step": "1"}), ("checklist-phase", {"phase": "x" * 97}), ("checklist-phase", {"phase": 1}),
    ("phase", {}), ("phase", {"phase": "briefing"}), ("phase", {"phase": "briefing", "confirm": False}),
    ("phase", {"phase": "briefing", "confirm": 1}), ("phase", {"phase": "flying", "confirm": True}),
    ("phase", {"phase": "other"}), ("phase", {"phase": ["flying"]}),
    ("load-plan", {"x": 1}), ("sim-retry", {"x": 1}),
    ("simdll-fetch", {"confirm": True}), ("thrust-cal", {"action": "start"}), ("unknown", {}), (None, {}), ("fields", []),
    ("fields", "x"), ("fields", None),
]


@pytest.mark.parametrize("name,data", VALID)
def test_validate_accepts_valid_commands_and_returns_a_copy(name, data):
    out = wire.validate_command(name, data)
    assert out == data and out is not data


@pytest.mark.parametrize("name,data", INVALID)
def test_validate_rejects_invalid_commands(name, data):
    with pytest.raises(wire.CommandError):
        wire.validate_command(name, data)


def test_validate_too_big_and_not_serialisable():
    with pytest.raises(wire.CommandError, match="too big"):
        wire.validate_command("fields", {"v1": "x" * 3000})
    with pytest.raises(wire.CommandError, match="serialisable"):
        wire.validate_command("fields", {"v1": object()})
    # the largest legal command stays well under the cap
    big = {k: "x" * 40 for k in wire.FIELD_KEYS}
    big["reset"] = ["v1"] * 8
    assert len(json.dumps(big)) < wire.MAX_CMD_DATA
    assert wire.validate_command("fields", big) == big


def test_validate_is_the_same_on_both_sides_and_state_constants_match():
    import state
    assert set(wire.FIELD_KEYS) == {k for k, _ in state.ALL_FIELDS} and len(wire.FIELD_KEYS) == len(state.ALL_FIELDS)
    assert "simbrief" not in wire.FIELD_KEYS
    assert tuple(wire.PLAN_RESET) == tuple(state.PLAN_RESET)
    assert tuple(wire.CLR_KEYS) == tuple(state.CLR_KEYS) and wire.CLR_MAX == state.CLR_MAX
    assert tuple(wire.MODES) == tuple(state.MODES) and tuple(wire.CL_MODES) == tuple(state.CL_MODES)


def test_sanitize_result():
    rid = "a" * 32
    ok = wire.sanitize_result({"t": "result", "id": rid, "ok": True, "junk": 1})
    assert ok == {"id": rid, "ok": True}
    assert wire.sanitize_result({"t": "result", "id": rid, "ok": False, "code": "invalid", "msg": "bad\x00x"}) == \
        {"id": rid, "ok": False, "code": "invalid", "msg": "bad x"}
    assert wire.sanitize_result({"t": "result", "id": rid, "ok": False, "code": "failed", "reason": "network"})["reason"] == "network"
    assert "reason" not in wire.sanitize_result({"t": "result", "id": rid, "ok": False, "code": "failed", "reason": "LEAK"})
    assert "msg" not in wire.sanitize_result({"t": "result", "id": rid, "ok": False, "code": "failed", "msg": "x" * 121})
    for bad in (None, [], {"t": "result"}, {"t": "result", "id": "short", "ok": True}, {"t": "result", "id": rid},
                {"t": "result", "id": rid, "ok": 1}, {"t": "result", "id": rid, "ok": False},
                {"t": "result", "id": rid, "ok": False, "code": "nope"}, {"t": "other", "id": rid, "ok": True},
                {"t": "result", "id": rid.upper(), "ok": True}, {"t": "result", "id": rid + "\n", "ok": True}):
        assert wire.sanitize_result(bad) is None
    assert wire.sanitize_result({"t": "result", "id": rid, "ok": True}, expected_id="b" * 32) is None


def test_parse_caps():
    assert wire.parse_caps({"caps": ["cmd"]}) == ("cmd",)
    for bad in ({}, {"caps": "cmd"}, {"caps": [1]}, {"caps": ["x" * 17]}, {"caps": ["a"] * 9}, {"caps": [""]}, None):
        assert wire.parse_caps(bad) == ()


# ---------------------------------------------------------------- the route: access, 404, 400

def test_needs_a_session_first_even_for_local_only_names(env):
    from fastapi.testclient import TestClient
    anon = TestClient(env.app, base_url="https://testserver")
    for name in ("fields", "simdll-fetch", "thrust-cal", "nonsense"):
        r = anon.post("/api/flight/cmd/" + name, json={}, headers=hdr(env.csrf))
        assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    assert anon.post("/api/flight/cmd/fields", json={}).status_code == 401


def test_csrf_and_origin_are_checked(env):
    assert post(env, "fields", csrf=False).status_code == 403
    assert post(env, "fields", csrf="x" * 40).status_code == 403
    assert post(env, "fields", headers={"Origin": "https://evil.example"}).status_code == 403
    assert post(env, "fields", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert post(env, "fields", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert post(env, "simdll-fetch", csrf=False).status_code == 403  # the CSRF check comes before the name check


@pytest.mark.parametrize("name", ["simdll-fetch", "thrust-cal", "nonsense", "fetch_dll", "calib_action", "state", "FIELDS", "fields%2F.."])
def test_local_only_and_unknown_names_are_404_and_never_reach_the_program(env, name):
    with Pc(env) as pc:
        r = post(env, name, {"confirm": True})
        assert r.status_code == 404
        assert "%" in name or r.json() == {"error": "not_found"}  # (a name with an escaped slash is not even routed)
        pc.ping()  # the program received nothing but the pong
    assert post(env, "").status_code in (404, 405)


def test_get_is_not_allowed(env):
    assert env.web.get("/api/flight/cmd/fields").status_code == 405


@pytest.mark.parametrize("name,body", [(n, d) for n, d in INVALID if n in wire.ENABLED_COMMANDS and isinstance(d, dict)])
def test_invalid_bodies_are_400_and_never_reach_the_program(env, name, body):
    with Pc(env) as pc:
        r = post(env, name, body)
        assert r.status_code == 400 and r.json() == {"error": "invalid_request"}
        pc.ping()


def test_malformed_bodies_are_400(env):
    with Pc(env) as pc:
        h = hdr(env.csrf)
        for kw in ({"content": b"{bad", "headers": {**h, "Content-Type": "application/json"}},
                   {"content": b"[1]", "headers": {**h, "Content-Type": "application/json"}},
                   {"content": b'{"phase":"flying"}', "headers": {**h, "Content-Type": "text/plain"}},
                   {"content": b'{"phase":"flying"}', "headers": h},
                   {"content": b'{"v1":"' + b"x" * 3000 + b'"}', "headers": {**h, "Content-Type": "application/json"}}):
            r = env.web.post("/api/flight/cmd/fields", **kw)
            assert r.status_code == 400 and r.json() == {"error": "invalid_request"}, kw["content"][:30]
        pc.ping()


# ---------------------------------------------------------------- 409: no program, no capability

def test_409_when_the_program_is_offline(env):
    r = post(env, "fields", {})
    assert r.status_code == 409 and r.json() == {"error": "program_offline"}
    assert r.headers["cache-control"] == "no-store" and r.headers["vary"] == "Cookie"


def test_409_when_the_program_does_not_know_commands(env):
    with Pc(env, hello={"t": "hello", "v": 1, "app": "old", "platform": "x"}) as pc:
        assert env.web.get("/api/flight/state").json()["cmd"] is False
        r = post(env, "fields", {})
        assert r.status_code == 409 and r.json() == {"error": "commands_unsupported"}
        pc.ping()


def test_409_when_the_program_has_remote_commands_switched_off(env):
    with Pc(env, hello=dict(CMD_HELLO, caps=[])) as pc:
        assert env.web.get("/api/flight/state").json()["cmd"] is False
        r = post(env, "fields", {})
        assert r.status_code == 409 and r.json() == {"error": "commands_disabled"}
        pc.ping()


def test_state_tells_whether_the_program_accepts_commands(env):
    with Pc(env):
        assert env.web.get("/api/flight/state").json()["cmd"] is True


def test_garbage_caps_count_as_none(env):
    with Pc(env, hello=dict(CMD_HELLO, caps="cmd", cv="1")):
        assert post(env, "fields", {}).json() == {"error": "commands_unsupported"}


def test_kill_switch_answers_503_after_the_auth_and_name_checks(make_env):
    e = make_env(remote_commands=False)
    with Pc(e) as pc:
        r = post(e, "fields", {})
        assert r.status_code == 503 and r.json() == {"error": "commands_off"}
        assert post(e, "simdll-fetch").status_code == 404
        assert post(e, "fields", csrf=False).status_code == 403
        pc.ping()


def test_kill_switch_from_the_environment():
    from server.config import load_app_config
    base = {"PUBLIC_BASE_URL": "https://x.example", "SECRET_KEY": "k" * 40}
    assert load_app_config(base).remote_commands is True
    assert load_app_config(dict(base, VC_REMOTE_COMMANDS="off")).remote_commands is False
    assert load_app_config(dict(base, VC_REMOTE_COMMANDS="OFF")).remote_commands is False
    assert load_app_config(dict(base, VC_REMOTE_COMMANDS="on")).remote_commands is True


# ---------------------------------------------------------------- the flow

@pytest.mark.parametrize("name,body", VALID)
def test_every_valid_command_reaches_the_program_and_returns_200(env, name, body):
    with Pc(env) as pc:
        msg, r = roundtrip(env, pc, name, body, ok=True)
        assert msg == {"t": "cmd", "id": msg["id"], "name": name, "d": body}
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert r.headers["cache-control"] == "no-store" and r.headers["vary"] == "Cookie"
        assert len(json.dumps(msg)) <= wire.MAX_CMD_BYTES


def test_ids_are_fresh_random_32_hex(env):
    seen = set()
    with Pc(env) as pc:
        for _ in range(4):
            msg, r = roundtrip(env, pc, "fields", {}, ok=True)
            assert r.status_code == 200
            seen.add(msg["id"])
    assert len(seen) == 4


def test_the_browser_cannot_choose_the_id_or_add_keys(env):
    with Pc(env) as pc:
        t, box = threaded_post(env, "fields", {"id": "a" * 32, "t": "cmd", "v1": "1"})
        pc.ping()
        assert finish(t, box).status_code == 400  # unknown key: rejected before reaching the program


@pytest.mark.parametrize("code,status,error", [("invalid", 400, "invalid_request"), ("busy", 409, "busy"),
                                               ("disabled", 409, "commands_disabled"), ("failed", 502, "command_failed")])
def test_program_error_codes_map_to_fixed_texts(env, code, status, error):
    with Pc(env) as pc:
        _msg, r = roundtrip(env, pc, "fields", {"v1": "1"}, ok=False, code=code, msg="LEAK-MSG", reason="LEAK-REASON")
        assert r.status_code == status and r.json() == {"error": error}
        assert "LEAK" not in r.text


def test_load_plan_failure_reasons_use_a_fixed_vocabulary(env, monkeypatch):
    monkeypatch.setattr(api_agent, "CMD_PER_NAME_SPECIAL", {})
    with Pc(env) as pc:
        for reason in wire.LOAD_REASONS:
            _m, r = roundtrip(env, pc, "load-plan", {}, ok=False, code="failed", reason=reason)
            assert r.status_code == 502 and r.json() == {"error": "command_failed", "reason": reason}
        _m, r = roundtrip(env, pc, "load-plan", {}, ok=False, code="failed", reason="because I said so")
        assert r.json() == {"error": "command_failed"}
        _m, r = roundtrip(env, pc, "fields", {}, ok=False, code="failed", reason="network")  # only load-plan has a reason
        assert r.json() == {"error": "command_failed"}


def test_malformed_results_are_ignored_and_end_in_504(env):
    with Pc(env) as pc:
        t, box = threaded_post(env, "sim-retry", {})
        msg = pc.recv_cmd()
        for bad in ({"ok": True}, {"id": msg["id"]}, {"id": msg["id"], "ok": "yes"}, {"id": msg["id"], "ok": False, "code": "boom"},
                    {"id": msg["id"].upper(), "ok": True}, {"id": "0" * 32, "ok": True}):
            pc.ws.send_text(json.dumps({"t": "result", **bad}))
        r = finish(t, box)
        assert r.status_code == 504 and r.json() == {"error": "timeout"}
        pc.ping()  # the connection survives garbage


def test_oversized_result_is_ignored(env):
    with Pc(env) as pc:
        t, box = threaded_post(env, "sim-retry", {})
        msg = pc.recv_cmd()
        pc.answer(msg["id"], ok=True, pad="x" * wire.MAX_RESULT_BYTES)
        assert finish(t, box).status_code == 504


@REPEAT
def test_504_with_a_silent_program_frees_the_slot_and_a_late_result_is_ignored(env, rep):
    with Pc(env) as pc:
        t, box = threaded_post(env, "fields", {"v1": "1"})
        first = pc.recv_cmd()
        r = finish(t, box)
        assert r.status_code == 504 and r.json() == {"error": "timeout"}
        # the slot is free: a new command goes through, even if the program is still "busy" with the first one
        t2, box2 = threaded_post(env, "fields", {"v1": "2"})
        second = pc.recv_cmd()
        assert second["id"] != first["id"]
        pc.answer(first["id"], ok=True)  # late result of the first: ignored, does not resolve the second
        time.sleep(0.1)
        assert not box2
        pc.answer(second["id"], ok=True)
        assert finish(t2, box2).status_code == 200


def test_load_plan_has_its_own_longer_timeout(env, monkeypatch):
    monkeypatch.setitem(wire.CMD_TIMEOUT, "default", 0.2)
    monkeypatch.setitem(wire.CMD_TIMEOUT, "load-plan", 1.2)
    with Pc(env) as pc:
        t0 = time.monotonic()
        t, box = threaded_post(env, "load-plan", {})
        msg = pc.recv_cmd()
        time.sleep(0.5)  # longer than the default timeout
        pc.answer(msg["id"], ok=True)
        assert finish(t, box).status_code == 200
        t, box = threaded_post(env, "fields", {})
        pc.recv_cmd()
        r = finish(t, box)
        assert r.status_code == 504 and time.monotonic() - t0 < 5


@REPEAT
def test_one_command_at_a_time_per_account(env, rep):
    with Pc(env) as pc:
        t, box = threaded_post(env, "fields", {"v1": "1"})
        msg = pc.recv_cmd()
        r2 = post(env, "sim-retry", {})
        assert r2.status_code == 409 and r2.json() == {"error": "busy"}
        pc.ping()  # the second command never reached the program
        pc.answer(msg["id"], ok=True)
        assert finish(t, box).status_code == 200
        _m, r3 = roundtrip(env, pc, "sim-retry", {}, ok=True)
        assert r3.status_code == 200


def test_replayed_and_repeated_results_are_ignored(env):
    with Pc(env) as pc:
        t, box = threaded_post(env, "sim-retry", {})
        msg = pc.recv_cmd()
        pc.answer(msg["id"], ok=True)
        pc.answer(msg["id"], ok=False, code="failed")  # repeated: ignored
        assert finish(t, box).status_code == 200
        pc.answer(msg["id"], ok=True)  # replay after completion: ignored, the connection lives on
        pc.ping()
        t, box = threaded_post(env, "sim-retry", {})
        msg2 = pc.recv_cmd()
        pc.answer(msg["id"], ok=True)  # the OLD id does not resolve the new command
        time.sleep(0.1)
        assert not box
        pc.answer(msg2["id"], ok=True)
        assert finish(t, box).status_code == 200


def test_connection_closing_during_the_wait_gives_409_and_frees_the_slot(env):
    with Pc(env) as pc:
        t, box = threaded_post(env, "fields", {})
        pc.recv_cmd()
        pc.ws.send_text(json.dumps({"t": "bye"}))
        r = finish(t, box)
        assert r.status_code == 409 and r.json() == {"error": "program_offline"}
    assert wait_for(env.connected, False)
    assert post(env, "fields", {}).json() == {"error": "program_offline"}
    with Pc(env) as pc2:
        _m, r = roundtrip(env, pc2, "fields", {}, ok=True)
        assert r.status_code == 200


def test_a_replaced_connection_gives_409_and_the_new_one_works(env):
    with Pc(env) as old:
        t, box = threaded_post(env, "fields", {})
        old.recv_cmd()
        with Pc(env) as new:
            assert closed_code(old.ws) == wire.CLOSE_REPLACED
            r = finish(t, box)
            assert r.status_code == 409 and r.json() == {"error": "program_offline"}
            _m, r2 = roundtrip(env, new, "fields", {}, ok=True)
            assert r2.status_code == 200


def test_a_cancelled_browser_request_does_not_leave_the_slot_busy(env):
    """The browser gives up (the handler is cancelled): busy/pending are freed in the finally."""
    import asyncio

    hub = env.app.state.hub

    async def scenario():
        uid, _ = env.app.state.devices.verify_access(env.access)
        conn = hub.new_conn(_Sink(), "fam")
        conn.caps, conn.cmd_version = ("cmd",), 1
        hub.register(uid, conn)
        task = asyncio.ensure_future(hub.send_command(uid, "fields", {}, 30.0))
        await asyncio.sleep(0.05)
        assert conn.busy and conn.pending is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert conn.busy is False and conn.pending is None
        assert await hub.send_command(uid, "fields", {}, 0.05) == ("timeout", None)
        assert conn.busy is False

    asyncio.run(scenario())


class _Sink:
    async def send_text(self, text):
        pass


# ---------------------------------------------------------------- isolation between accounts

def test_commands_go_only_to_the_program_of_the_same_account(env):
    web2, tok_b = second_account(env)
    csrf_b = web2.get("/api/auth/me").json()["csrf_token"]
    try:
        with Pc(env) as a, Pc(env, token=tok_b["access_token"]) as b:
            assert wait_for(lambda: env.connected(web2))
            # A's command goes to A's program only
            t, box = threaded_post(env, "fields", {"v1": "A"})
            ma = a.recv_cmd()
            b.ping()  # B got nothing but its pong
            # B cannot resolve A's pending command, not even with A's id
            b.answer(ma["id"], ok=True)
            time.sleep(0.1)
            assert not box
            # B's own command is independent of A's pending one
            tb, boxb = threaded_post(env, "fields", {"v1": "B"}, csrf=csrf_b, cookie_from=web2)
            mb = b.recv_cmd()
            assert mb["d"] == {"v1": "B"} and mb["id"] != ma["id"]
            a.answer(mb["id"], ok=True)  # and A cannot resolve B's
            time.sleep(0.1)
            assert not boxb
            b.answer(mb["id"], ok=True)
            assert finish(tb, boxb).status_code == 200
            a.answer(ma["id"], ok=True)
            assert finish(t, box).status_code == 200
            # B without program is offline for B, A's program is untouched
        assert wait_for(lambda: env.connected(web2), False)
        r = post(env, "fields", {}, client=web2, csrf=csrf_b)
        assert r.status_code == 409 and r.json() == {"error": "program_offline"}
    finally:
        web2.__exit__(None, None, None)


# ---------------------------------------------------------------- limits (RAM only)

def test_rate_limit_per_command(env, monkeypatch):
    monkeypatch.setattr(api_agent, "CMD_PER_NAME_SPECIAL", {"sim-retry": 3})
    with Pc(env) as pc:
        for _ in range(3):
            _m, r = roundtrip(env, pc, "sim-retry", {}, ok=True)
            assert r.status_code == 200
        r = post(env, "sim-retry", {})
        assert r.status_code == 429 and r.json() == {"error": "rate_limited"} and int(r.headers["retry-after"]) >= 1
        _m, r = roundtrip(env, pc, "fields", {}, ok=True)  # another command is not affected
        assert r.status_code == 200


def test_rate_limit_per_user_and_per_ip(env, monkeypatch):
    monkeypatch.setattr(api_agent, "CMD_PER_USER_MINUTE", 2)
    with Pc(env) as pc:
        for _ in range(2):
            roundtrip(env, pc, "fields", {}, ok=True)
        assert post(env, "fields", {}).status_code == 429
    monkeypatch.setattr(api_agent, "CMD_PER_USER_MINUTE", 100)
    monkeypatch.setattr(api_agent, "CMD_PER_IP_MINUTE", 2)
    assert post(env, "fields", {}).status_code == 409  # no program: offline, but it counts for the IP
    assert post(env, "fields", {}).status_code == 409
    r = post(env, "fields", {})
    assert r.status_code == 429 and r.headers["retry-after"]


def test_rate_limit_never_writes_to_the_db(env):
    import sqlite3
    c = sqlite3.connect(env.db)
    before = c.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    for _ in range(6):
        post(env, "fields", {})
    post(env, "nonsense")
    after = c.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    c.close()
    assert after == before


def test_the_limit_counts_even_when_the_body_is_invalid(env, monkeypatch):
    monkeypatch.setattr(api_agent, "CMD_PER_NAME_SPECIAL", {"fields": 2})
    assert post(env, "fields", {"nope": 1}).status_code == 400
    assert post(env, "fields", {"nope": 1}).status_code == 400
    assert post(env, "fields", {"nope": 1}).status_code == 429


# ---------------------------------------------------------------- nothing in the logs

def test_nothing_about_commands_in_the_logs(env, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "CMDMARK" + "Z" * 10
    with Pc(env) as pc:
        t, box = threaded_post(env, "fields", {"origin": marker})
        msg = pc.recv_cmd()
        pc.answer(msg["id"], ok=False, code="invalid", msg="PCMSG-LEAK")
        assert finish(t, box).status_code == 400
        post(env, "fields", {"zzz": marker})
        t, box = threaded_post(env, "fields", {"origin": marker})
        msg2 = pc.recv_cmd()
        finish(t, box)
    for needle in (marker, "PCMSG-LEAK", msg["id"], msg2["id"], "LIMC"):
        assert needle not in caplog.text


def test_a_hello_without_cv_and_caps_still_connects_as_before(env):
    with open_agent(env):
        assert wait_for(env.connected)
        assert env.web.get("/api/flight/state").json()["cmd"] is False


# ---------------------------------------------------------------- real uvicorn on loopback

def _http_post(e, url, name, body):
    import http.client
    port = int(url.split(":")[2].split("/")[0])
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.request("POST", "/api/flight/cmd/" + name, json.dumps(body),
                  {"Content-Type": "application/json", "X-CSRF-Token": e.csrf,
                   "Cookie": "__Host-session=" + e.web.cookies.get("__Host-session")})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}"), dict(r.getheaders())
    finally:
        c.close()


def test_real_uvicorn_command_roundtrip_404_and_timeout(live):
    e, url = live
    ws = _real_connect(url, {"Authorization": "Bearer " + e.access})
    try:
        json.loads(ws.recv(5))
        ws.send(json.dumps(CMD_HELLO))
        assert wait_for(e.connected)
        box = []
        t = threading.Thread(target=lambda: box.append(_http_post(e, url, "fields", {"v1": "150"})), daemon=True)
        t.start()
        msg = json.loads(ws.recv(5))
        assert msg["t"] == "cmd" and ID_RE.match(msg["id"]) and msg["name"] == "fields" and msg["d"] == {"v1": "150"}
        ws.send(json.dumps({"t": "result", "id": msg["id"], "ok": True}))
        t.join(8)
        assert box and box[0][0] == 200 and box[0][1] == {"ok": True}
        assert box[0][2].get("Cache-Control") == "no-store" or box[0][2].get("cache-control") == "no-store"
        # local-only names: 404 on the real server too, and the program receives nothing
        for name in ("simdll-fetch", "thrust-cal"):
            assert _http_post(e, url, name, {"confirm": True})[0] == 404
        ws.send(json.dumps({"t": "ping"}))
        assert json.loads(ws.recv(5)) == {"t": "pong"}
        # a silent program: 504, then the next command is accepted
        status, body, _h = _http_post(e, url, "sim-retry", {})
        assert (status, body) == (504, {"error": "timeout"})
        assert json.loads(ws.recv(5))["name"] == "sim-retry"
    finally:
        ws.close()
