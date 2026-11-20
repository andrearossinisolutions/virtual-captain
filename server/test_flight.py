"""Flight snapshot (step U2): snapshot over WS /ws/agent, GET /api/flight/state, isolation between
accounts, limits, nothing in logs, static files of the flight page. TestClient, real short timers, no network."""
from __future__ import annotations

import json
import logging
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import api_agent, wire  # noqa: E402
from server import app as srv  # noqa: E402
from server.hub import Hub  # noqa: E402
from server.ratelimit import MemoryRateLimiter  # noqa: E402
from server.test_agent import (closed_code, make_env, open_agent, second_account, wait_for)  # noqa: E402,F401

REPEAT = pytest.mark.parametrize("rep", range(3))
LAT, LON = "11.1111", "22.2222"


def view(marker="M1", **extra):
    d = {"phase": "flying", "log": [marker], "spoke_n": 1, "fields": {"origin": "LIMC/17R"}}
    d.update(extra)
    return d


def msg(seq=1, d=None, sv=1):
    return json.dumps({"t": "snapshot", "sv": sv, "seq": seq, "d": view() if d is None else d})


@pytest.fixture
def env(make_env):
    return make_env()


def state(e, client=None, query=""):
    r = (client or e.web).get("/api/flight/state" + query)
    assert r.status_code == 200, r.text
    return r.json()


def seq_is(e, n, client=None):
    return lambda: (client or e.web).get("/api/flight/state").json().get("seq") == n


# ---------- contract ----------

def test_wire_snapshot_constants():
    assert wire.SNAPSHOT_VERSION == 1 and wire.T_SNAPSHOT == "snapshot" and wire.MAX_SNAPSHOT_TARGET == 65536


def test_no_connection_is_200_connected_false(env):
    r = env.web.get("/api/flight/state")
    assert r.status_code == 200 and r.json() == {"connected": False}
    assert r.headers["cache-control"] == "no-store" and r.headers["vary"] == "Cookie"
    assert "content-security-policy" in r.headers


def test_needs_a_session_cookie_and_nothing_else(env):
    anon = TestClient(env.app, base_url="https://vc.example.test")
    r = anon.get("/api/flight/state")
    assert r.status_code == 401 and r.headers["cache-control"] == "no-store"
    assert anon.get("/api/flight/state", headers={"Authorization": "Bearer " + env.access}).status_code == 401
    assert anon.get("/api/flight/state?token=" + env.access).status_code == 401
    assert anon.get("/api/flight/state?user=1&uid=1").status_code == 401


def test_program_connected_without_snapshot_yet(env):
    with open_agent(env):
        assert wait_for(env.connected)
        d = state(env)
        assert d["connected"] is True and d["seq"] == 0 and d["snapshot"] is None and d["age"] is None


# ---------- snapshot flow ----------

@REPEAT
def test_snapshot_is_stored_sanitised_and_served(env, rep):
    d = view("MARK", simbrief="SECRET-ID", info="x", flight=[["Position", f"{LAT} N {LON} E"], ["GS", "1 kt"]],
             plan={"route": "A B", "waypoints": [{"ident": "X", "alt": 1, "lat": float(LAT), "lon": float(LON)}]})
    with open_agent(env) as ws:
        ws.send_text(msg(1, d))
        assert wait_for(seq_is(env, 1))
        r = env.web.get("/api/flight/state")
        out = r.json()
        assert out["connected"] is True and isinstance(out["age"], float) and out["spoke_ago"] is None
        snap = out["snapshot"]
        assert snap["log"] == ["MARK"] and snap["flight"] == [["GS", "1 kt"]]
        assert snap["plan"]["waypoints"] == [{"ident": "X", "alt": 1}]
        assert snap["simbrief"] == "SECRET-ID" and snap["info"] == "x"  # the user's own briefing data (U3)
        for s in (LAT, LON, "Position"):  # a malformed Position row is dropped, nothing numeric leaves
            assert s not in r.text


def test_same_seq_returns_unchanged_without_the_snapshot(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        ep = state(env)["epoch"]
        assert state(env, query="?seq=1")["snapshot"]["log"] == ["M1"]  # no epoch: never "unchanged"
        u = state(env, query=f"?seq=1&epoch={ep}")
        assert u["unchanged"] is True and u["snapshot"] is None and u["seq"] == 1
        full = state(env, query="?seq=0")
        assert full["snapshot"]["log"] == ["M1"] and "unchanged" not in full
        ws.send_text(msg(2, view("M2")))
        assert wait_for(seq_is(env, 2))
        assert state(env, query=f"?seq=1&epoch={ep}")["snapshot"]["log"] == ["M2"]


@pytest.mark.parametrize("rep", range(3))
def test_fast_reconnect_with_same_seq_is_not_unchanged(env, rep):
    with open_agent(env) as ws:
        ws.send_text(msg(1, view("OLD")))
        assert wait_for(seq_is(env, 1))
        old = state(env)
    with open_agent(env) as ws:  # the program came back at once: its seq restarts at 1
        ws.send_text(msg(1, view("NEW")))
        assert wait_for(lambda: (state(env)["snapshot"] or {}).get("log") == ["NEW"])
        new = state(env)
        assert new["seq"] == old["seq"] == 1 and new["epoch"] != old["epoch"]
        r = state(env, query=f"?seq=1&epoch={old['epoch']}")  # what the browser would send
        assert "unchanged" not in r and r["snapshot"]["log"] == ["NEW"]


@pytest.mark.parametrize("q", ["&epoch=abc", "&epoch=-1", "&epoch=", "&epoch=" + "9" * 40, "&epoch=1.5"])
def test_garbage_epoch_gives_full_snapshot(env, q):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        r = state(env, query="?seq=1" + q)
        assert "unchanged" not in r and r["snapshot"]["log"] == ["M1"]


@pytest.mark.parametrize("q", ["?seq=abc", "?seq=-1", "?seq=1.5", "?seq=", "?seq=" + "9" * 40, "?seq=%00", "?seq=1&seq=2"])
def test_garbage_seq_gives_the_full_snapshot_not_an_error(env, q):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        assert state(env, query=q)["snapshot"]["log"] == ["M1"]


def test_spoke_ago_is_rebuilt_on_the_server_when_spoke_n_changes(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1, view(spoke_n=1)))
        assert wait_for(seq_is(env, 1))
        assert state(env)["spoke_ago"] is None  # a first snapshot has no known age
        ws.send_text(msg(2, view(spoke_n=2)))
        assert wait_for(seq_is(env, 2))
        ago = state(env)["spoke_ago"]
        assert ago is not None and 0 <= ago < 3
        ws.send_text(msg(3, view("other", spoke_n=2)))
        assert wait_for(seq_is(env, 3))
        assert state(env)["spoke_ago"] >= ago


def test_snapshot_dies_with_the_connection(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
    assert wait_for(lambda: state(env) == {"connected": False})
    assert env.app.state.hub.count() == 0


def test_a_replaced_connection_empties_the_view(env):
    with open_agent(env) as ws1:
        ws1.send_text(msg(1, view("OLD")))
        assert wait_for(seq_is(env, 1))
        with open_agent(env) as ws2:
            assert closed_code(ws1) == 4001
            assert wait_for(lambda: state(env).get("connected") is True and state(env)["snapshot"] is None)
            assert state(env)["seq"] == 0
            ws2.send_text(msg(1, view("NEW")))
            assert wait_for(lambda: (state(env)["snapshot"] or {}).get("log") == ["NEW"])


def test_stale_program_is_not_connected_and_age_follows_the_heartbeat():
    t = {"now": 100.0}
    hub = Hub(stale_after=12, monotonic=lambda: t["now"])
    conn = hub.new_conn(object(), "fam")
    hub.register(7, conn)
    assert hub.flight_state(7) == {"seq": 0, "epoch": conn.epoch, "age": None, "spoke_ago": None, "text": None, "cmd": False}
    t["now"] = 103.0
    hub.store_snapshot(conn, 4, {"phase": "flying", "spoke_n": 1})
    t["now"] = 110.0
    assert hub.flight_state(7)["age"] == 7.0 and hub.flight_state(7)["seq"] == 4
    hub.touch(conn)  # a heartbeat: the program is alive although nothing changed
    t["now"] = 112.0
    assert hub.flight_state(7)["age"] == 2.0
    t["now"] = 125.0
    assert hub.flight_state(7) is None
    assert hub.flight_state(99) is None


def test_hub_epoch_is_unique_per_connection():
    hub = Hub()
    assert len({hub.new_conn(object(), "f").epoch for _ in range(5)}) == 5


def test_hub_serialises_once_and_keeps_text_only_in_the_connection():
    hub = Hub()
    conn = hub.new_conn(object(), "fam")
    hub.register(1, conn)
    hub.store_snapshot(conn, 1, {"phase": "flying", "log": ["é"]})
    a, b = hub.flight_state(1)["text"], hub.flight_state(1)["text"]
    assert a is b and json.loads(a)["log"] == ["é"] and a.isascii()
    hub.unregister(1, conn)
    assert hub.flight_state(1) is None


# ---------- isolation between accounts ----------

def test_accounts_see_only_their_own_program(env):
    web2, tok_b = second_account(env)
    try:
        with open_agent(env) as ws_a:
            ws_a.send_text(msg(1, view("MARK-A")))
            assert wait_for(seq_is(env, 1))
            d = state(env, web2)  # B has no program: A's data never shows up, A is online
            assert d == {"connected": False}
            with open_agent(env, token=tok_b["access_token"]) as ws_b:
                ws_b.send_text(msg(1, view("MARK-B")))
                assert wait_for(seq_is(env, 1, web2))
                ra, rb = env.web.get("/api/flight/state"), web2.get("/api/flight/state")
                assert "MARK-A" in ra.text and "MARK-B" not in ra.text
                assert "MARK-B" in rb.text and "MARK-A" not in rb.text
                ws_a.send_text(msg(2, view("A-AGAIN")))
                assert wait_for(seq_is(env, 2))
                assert "A-AGAIN" not in web2.get("/api/flight/state").text
            assert wait_for(lambda: state(env, web2) == {"connected": False})
            assert state(env)["snapshot"]["log"] == ["A-AGAIN"] if state(env)["seq"] == 2 else True
    finally:
        web2.__exit__(None, None, None)


def test_no_id_in_the_url_selects_the_account(env):
    web2, tok_b = second_account(env)
    try:
        with open_agent(env, token=tok_b["access_token"]) as ws_b:
            ws_b.send_text(msg(1, view("MARK-B")))
            assert wait_for(seq_is(env, 1, web2))
            for q in ("?user=2", "?uid=2", "?account=b@example.test", "?id=2"):
                assert "MARK-B" not in env.web.get("/api/flight/state" + q).text
            assert env.web.get("/api/flight/state/2").status_code in (404, 405)
    finally:
        web2.__exit__(None, None, None)


# ---------- limits ----------

def test_invalid_snapshots_count_and_valid_resets(env):
    with open_agent(env) as ws:
        for _ in range(api_agent.SNAP_MAX_INVALID - 1):
            ws.send_text(msg(1, {"phase": 5}))
            time.sleep(0.6)
        ws.send_text(msg(1))  # valid: counter back to zero, connection alive
        assert wait_for(seq_is(env, 1))
        assert env.connected()


def test_five_consecutive_invalid_snapshots_close_1008(env, monkeypatch):
    monkeypatch.setattr(api_agent, "SNAP_MAX_PER_WINDOW", 100)
    with open_agent(env) as ws:
        for i in range(api_agent.SNAP_MAX_INVALID):
            ws.send_text(msg(i, {"log": "x"}))
        assert closed_code(ws) == 1008
    assert wait_for(lambda: state(env) == {"connected": False})


@pytest.mark.parametrize("bad", [
    lambda: json.dumps({"t": "snapshot", "sv": 1, "d": view()}),                # no seq
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": "1", "d": view()}),
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": True, "d": view()}),
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": -1, "d": view()}),
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": 1}),                   # no d
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": 1, "d": []}),
    lambda: json.dumps({"t": "snapshot", "sv": 1, "seq": 1, "d": {"log": ["x"]}}),  # no phase
])
def test_malformed_snapshot_is_ignored_and_counted_not_stored(env, bad):
    with open_agent(env) as ws:
        ws.send_text(bad())
        time.sleep(0.3)
        ws.send_text(msg(5))
        assert wait_for(seq_is(env, 5))


@pytest.mark.parametrize("sv", [2, 0, "1", None, True])
def test_unsupported_snapshot_version_closes_4002(env, sv):
    with open_agent(env) as ws:
        ws.send_text(msg(1, sv=sv))
        assert closed_code(ws) == 4002


def test_more_than_four_snapshots_in_two_seconds_closes_4004(env):
    with open_agent(env) as ws:
        for i in range(8):
            ws.send_text(msg(i + 1))
        assert closed_code(ws) == 4004


def test_one_snapshot_per_second_is_fine_and_byte_budget_is_enforced(env, monkeypatch):
    with open_agent(env) as ws:
        for i in range(3):
            ws.send_text(msg(i + 1))
            time.sleep(0.55)
        assert wait_for(seq_is(env, 3))
    monkeypatch.setattr(api_agent, "SNAP_MAX_BYTES", 2500)
    with open_agent(env) as ws:
        for i in range(4):
            ws.send_text(msg(i + 1, view(log=["x" * 290] * 3)))
            time.sleep(0.55)
        assert closed_code(ws) == 4004


def test_oversized_snapshot_message_closes_4004(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1, view(log=["x" * 300] * 30)).ljust(wire.MAX_MSG + 1, " "))
        assert closed_code(ws) == 4004


def test_deeply_nested_snapshot_closes_that_connection_only(env):
    deep = "[" * 5000 + "]" * 5000
    with open_agent(env) as ws:
        ws.send_text('{"t":"snapshot","sv":1,"seq":1,"d":' + deep + "}")
        assert closed_code(ws) == 1008  # not JSON the server can parse: same as any garbage
    with open_agent(env) as ws:  # the server is fine
        ws.send_text(msg(2))
        assert wait_for(seq_is(env, 2))
    deep_ok = {"phase": "flying", "fields": {"a": [[[[[[[[[1]]]]]]]]]}}
    with open_agent(env) as ws:  # deep but parseable: invalid, ignored, counted
        ws.send_text(msg(3, deep_ok))
        time.sleep(0.3)
        ws.send_text(msg(4))
        assert wait_for(seq_is(env, 4))


def test_polling_rate_limit_is_per_user_in_ram_and_gives_429(env, monkeypatch):
    monkeypatch.setattr(api_agent, "FLIGHT_PER_USER_MINUTE", 5)
    web2, _ = second_account(env)
    try:
        codes = [env.web.get("/api/flight/state").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
        r = env.web.get("/api/flight/state")
        assert r.status_code == 429 and r.headers["retry-after"] == "5" and r.headers["cache-control"] == "no-store"
        assert web2.get("/api/flight/state").status_code == 200  # another account is not affected
    finally:
        web2.__exit__(None, None, None)


def test_polling_rate_limit_per_ip(env, monkeypatch):
    monkeypatch.setattr(api_agent, "FLIGHT_PER_IP_MINUTE", 4)
    assert [env.web.get("/api/flight/state").status_code for _ in range(5)] == [200] * 4 + [429]


def test_polling_never_writes_rate_events_to_the_db(env):
    import sqlite3
    c = sqlite3.connect(env.db)
    before = c.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0]
    for _ in range(10):
        env.web.get("/api/flight/state")
    assert c.execute("SELECT COUNT(*) FROM rate_events").fetchone()[0] == before
    c.close()


def test_memory_limiter_window_and_pruning():
    t = {"now": 0.0}
    m = MemoryRateLimiter(lambda: t["now"])
    assert [m.allow("b", "k", 2, 10.0) for _ in range(3)] == [True, True, False]
    assert m.allow("b", "other", 2, 10.0)
    t["now"] = 11.0
    assert m.allow("b", "k", 2, 10.0)
    m.PRUNE_ABOVE = 1
    t["now"] = 100.0
    assert m.allow("b", "new", 2, 10.0) and len(m._events) == 1


def test_session_last_seen_is_refreshed_at_most_once_a_minute(env):
    import sqlite3
    c = sqlite3.connect(env.db)
    last = lambda: c.execute("SELECT MAX(last_seen) FROM sessions").fetchone()[0]
    env.web.get("/api/flight/state")
    first = last()
    env.clock.advance(30)
    env.web.get("/api/flight/state")
    assert last() == first
    env.clock.advance(31)
    env.web.get("/api/flight/state")
    assert last() > first
    c.close()


# ---------- logs ----------

def test_logs_never_hold_the_snapshot_content(env, caplog):
    with caplog.at_level(logging.DEBUG):
        with open_agent(env) as ws:
            ws.send_text(msg(1, view("SECRET-MARK-XYZ", fields={"origin": "SECRETORIGIN"})))
            assert wait_for(seq_is(env, 1))
            ws.send_text(msg(2, {"phase": 7, "log": ["SECRET-MARK-XYZ"]}))
            ws.send_text('{"t":"snapshot","sv":9,"seq":1,"d":{"log":["SECRET-MARK-XYZ"]}}')
            closed_code(ws)
        env.web.get("/api/flight/state")
    assert "SECRET-MARK-XYZ" not in caplog.text and "SECRETORIGIN" not in caplog.text


# ---------- pages and static files ----------

def test_flight_page_and_web_files_are_served_with_headers(env):
    web_dir = srv.WEB_DIR
    for path, name in (("/app.js", "app.js"), ("/app.css", "app.css")):
        r = env.web.get(path)
        assert r.status_code == 200 and r.content == (web_dir / name).read_bytes()
        assert r.headers["content-security-policy"] == srv.CSP and r.headers["x-content-type-options"] == "nosniff"
        assert env.web.head(path).status_code == 200
    assert env.web.get("/app.js").headers["content-type"].startswith("text/javascript")
    assert env.web.get("/app.css").headers["content-type"].startswith("text/css")
    r = env.web.get("/flight")
    assert r.status_code == 200 and 'data-mode="remote"' in r.text and r.headers["content-security-policy"] == srv.CSP


@pytest.mark.parametrize("path", ["/web/app.js", "/app.js/../server/app.py", "/../web/index.html", "/index.html",
                                  "/app.js.map", "/APP.JS", "/web/", "/app.css%2f..%2fapp.py", "/flight.html", "/%2e%2e/server/app.py"])
def test_only_the_two_web_files_are_served(env, path):
    r = env.web.get(path)
    assert r.status_code in (404, 405) or r.text.lstrip().startswith("<!doctype html")  # home.html shell is not a web/ file
    assert "create_app" not in r.text and "const REMOTE" not in r.text or path == "/app.js"


def test_web_files_not_served_without_the_api(tmp_path):
    from server.config import AppConfig
    c = TestClient(srv.create_app(AppConfig(db_path=str(tmp_path / "x.db"))))
    assert c.get("/app.js").status_code == 404 and c.get("/app.css").status_code == 404
    assert c.get("/api/flight/state").status_code == 404 and c.get("/flight").status_code == 404


# ---------- deltas (U4, C3) ----------

def dmsg(seq, base, d=None, rm=None, dv=1):
    body = {"t": "delta", "dv": dv, "seq": seq, "base": base, "d": {} if d is None else d}
    if rm is not None:
        body["rm"] = rm
    return json.dumps(body)


def ping_then(ws):
    """Everything the server sent before the pong (a resync comes before it), in order."""
    ws.send_text(json.dumps({"t": "ping"}))
    out = []
    while True:
        m = json.loads(ws.receive_text())
        if m == {"t": "pong"}:
            return out
        out.append(m)


def snap_of(e, client=None):
    return state(e, client)["snapshot"]


def test_welcome_announces_delta_and_the_kill_switch_removes_it(make_env):
    e = make_env()
    with open_agent(e) as ws:
        pass
    with e.ws() as ws:
        assert json.loads(ws.receive_text())["feat"] == ["delta"]
    off = make_env(delta=False)
    with off.ws() as ws:
        assert json.loads(ws.receive_text()) == {"t": "welcome", "v": 1, "heartbeat": 5, "max_msg": 262144}


def test_config_reads_vc_delta():
    import pathlib
    import re
    src = (pathlib.Path(__file__).parent / "config.py").read_text(encoding="utf-8")
    assert re.search(r'delta=\(env\.get\("VC_DELTA"\)', src)
    assert "VC_DELTA" in (pathlib.Path(__file__).parent / "env.example").read_text(encoding="utf-8")


def body_snapshot(e):
    """The snapshot part of the polling response, as served (text, not parsed)."""
    return e.web.get("/api/flight/state").text.split('"snapshot":')[1]


@pytest.fixture
def short_window(monkeypatch):
    """The 4-messages-in-2-s limit shrunk to 0.3 s so the tests can pace their messages with short sleeps."""
    monkeypatch.setattr(api_agent, "SNAP_WINDOW", 0.3)


def slow():
    time.sleep(0.35)  # longer than the shrunk window of `short_window`


def test_delta_is_applied_and_polling_matches_a_full_snapshot(env):
    full2 = view("M2", pills={"a": "x"})
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        ws.send_text(dmsg(2, 1, {"log": ["M2"], "pills": {"a": "x"}}))
        assert wait_for(seq_is(env, 2))
        assert ping_then(ws) == []  # no resync
        by_delta = body_snapshot(env)
        ws.send_text(msg(3, full2))
        assert wait_for(seq_is(env, 3))
        assert body_snapshot(env) == by_delta  # same bytes as the full snapshot (join of the sections)
        assert snap_of(env) == full2


def test_removal_by_delta_and_spoke_n_tracking(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1, view("M1", pills={"a": "x"})))
        assert wait_for(seq_is(env, 1))
        ws.send_text(dmsg(2, 1, {"spoke_n": 2}, ["pills"]))
        assert wait_for(seq_is(env, 2))
        s = snap_of(env)
        assert "pills" not in s and s["spoke_n"] == 2 and s["log"] == ["M1"]
        assert state(env)["spoke_ago"] is not None  # spoke_n changed by a delta


def test_hub_joined_text_equals_the_direct_dump_and_tombstones_stay():
    hub = Hub()
    conn = hub.new_conn(object(), "f")
    hub.register(1, conn)
    v = {"phase": "flying", "log": ["\u00e9"], "fields": {"b": "1", "a": "2"}, "pills": {"z": "y"}}
    hub.store_snapshot(conn, 1, v)
    assert conn.snapshot_text == json.dumps(v, separators=(",", ":"), sort_keys=True)
    new = wire.apply_delta(conn.view, {"log": ["x"]}, ["pills"])
    assert hub.store_delta(conn, 2, new, ["log"], ["pills"]) is True
    assert conn.snapshot_text == json.dumps(new, separators=(",", ":"), sort_keys=True)
    assert conn.sections["pills"] == (None, 2) and conn.sections["log"][1] == 2 and conn.sections["phase"][1] == 1
    hub.store_snapshot(conn, 3, {"phase": "flying"})  # a full one: whatever disappeared becomes a tombstone
    assert conn.sections["log"] == (None, 3) and conn.sections["phase"][1] == 3


def test_hub_epoch_is_random_per_process():
    starts = {Hub().new_conn(object(), "f").epoch for _ in range(6)}
    assert len(starts) > 1  # 30 random bits: six hubs never all start at the same number
    hub = Hub()
    a, b = hub.new_conn(object(), "f").epoch, hub.new_conn(object(), "f").epoch
    assert b == a + 1 and 0 < a < 2 ** 31


def test_hub_subscribers_are_notified_and_can_leave():
    async def go():
        hub = Hub()
        ev, other = hub.subscribe(1), hub.subscribe(2)
        conn = hub.new_conn(object(), "f")
        hub.register(1, conn)
        assert ev.is_set() and not other.is_set()
        ev.clear()
        hub.store_snapshot(conn, 1, {"phase": "flying"})
        assert ev.is_set() and not other.is_set()
        ev.clear()
        hub.unsubscribe(1, ev)
        hub.store_snapshot(conn, 2, {"phase": "flying"})
        assert not ev.is_set() and 1 not in hub._subs
        hub.unsubscribe(1, ev)  # twice: harmless
    import asyncio
    asyncio.run(go())


def test_hub_refuses_a_state_over_the_cap_for_full_and_delta(monkeypatch):
    hub = Hub()
    conn = hub.new_conn(object(), "f")
    hub.register(1, conn)
    hub.store_snapshot(conn, 1, {"phase": "flying"})
    big = {"phase": "flying", "log": ["x" * 300] * 30}
    before = (conn.snapshot_text, dict(conn.sections), conn.seq)
    monkeypatch.setattr(wire, "MAX_STATE_BYTES", 100)
    assert hub.store_delta(conn, 2, big, ["log"], []) is False
    assert hub.store_snapshot(conn, 2, big) is False
    assert (conn.snapshot_text, conn.sections, conn.seq) == before


@pytest.mark.parametrize("bad_delta", [
    dmsg(3, 2, {"log": ["X"]}),                  # gap: base is not the previous seq
    dmsg(1, 0, {"log": ["X"]}),                  # wrong base (seq = base + 1, but the state is at seq 1)
])
def test_gap_and_wrong_base_give_resync_and_keep_the_connection(env, bad_delta):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        ws.send_text(bad_delta)
        assert ping_then(ws) == [{"t": "resync"}]
        assert snap_of(env)["log"] == ["M1"] and state(env)["seq"] == 1  # nothing applied
        assert env.connected()


def test_deltas_are_ignored_until_a_full_snapshot_then_all_is_normal(env, short_window):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        ws.send_text(dmsg(5, 4, {"log": ["X"]}))   # gap -> resync
        assert ping_then(ws) == [{"t": "resync"}]
        slow()
        ws.send_text(dmsg(2, 1, {"log": ["OK-BUT-IGNORED"]}))  # right base, yet ignored: no new resync either
        assert ping_then(ws) == []
        assert snap_of(env)["log"] == ["M1"]
        slow()
        ws.send_text(msg(6, view("FULL")))
        assert wait_for(seq_is(env, 6))
        slow()
        ws.send_text(dmsg(7, 6, {"log": ["AFTER"]}))
        assert wait_for(seq_is(env, 7))
        assert ping_then(ws) == [] and snap_of(env)["log"] == ["AFTER"]


def test_delta_before_any_full_snapshot_gives_resync(env):
    with open_agent(env) as ws:
        ws.send_text(dmsg(1, 0, {"phase": "flying"}))
        assert ping_then(ws) == [{"t": "resync"}]
        assert state(env)["snapshot"] is None and env.connected()


@pytest.mark.parametrize("bad", [
    dmsg(2, 1, {"log": ["X"]}, dv=2), dmsg(2, 1, {"log": ["X"]}, dv=True),
    json.dumps({"t": "delta", "dv": 1, "seq": "2", "base": 1, "d": {}}),
    json.dumps({"t": "delta", "dv": 1, "seq": True, "base": 1, "d": {}}),
    json.dumps({"t": "delta", "dv": 1, "seq": 2, "base": True, "d": {}}),
    json.dumps({"t": "delta", "dv": 1, "seq": 2, "base": 1.0, "d": {}}),
    json.dumps({"t": "delta", "dv": 1, "seq": 2, "base": 1, "d": []}),
    json.dumps({"t": "delta", "dv": 1, "seq": 2, "base": 1, "d": {}, "rm": "log"}),
    dmsg(2, 1, {"log": "not a list"}),            # rejected by the allowlist
    dmsg(2, 1, None, ["phase"]),                  # phase can never be removed
])
def test_malformed_delta_gives_resync_without_closing(env, bad):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(env, 1))
        ws.send_text(bad)
        assert ping_then(ws) == [{"t": "resync"}]
        assert snap_of(env)["log"] == ["M1"] and env.connected()


def test_delta_over_the_size_cap_gives_resync_not_a_fault(make_env, monkeypatch, short_window):
    e = make_env()
    monkeypatch.setattr(wire, "MAX_STATE_BYTES", 400)
    with open_agent(e) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(e, 1))
        ws.send_text(dmsg(2, 1, {"log": ["x" * 290] * 3}))
        assert ping_then(ws) == [{"t": "resync"}]
        assert snap_of(e)["log"] == ["M1"]
        slow()
        ws.send_text(msg(3, view("F")))  # a full snapshot after the resync: back to normal
        assert wait_for(seq_is(e, 3))
        slow()
        ws.send_text(dmsg(4, 3, {"log": ["small"]}))
        assert wait_for(seq_is(e, 4)) and snap_of(e)["log"] == ["small"]
        assert e.connected()


def test_invalid_delta_counts_toward_the_five_in_a_row_that_close_1008(env, short_window):
    bad_full = json.dumps({"t": "snapshot", "sv": 1, "seq": 9, "d": {"log": ["no phase"]}})
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        slow()
        ws.send_text(dmsg(2, 1, {"log": "bad"}))   # invalid #1 (resync sent)
        assert ping_then(ws) == [{"t": "resync"}]
        for _ in range(3):                          # invalid #2..#4: still open
            slow()
            ws.send_text(bad_full)
        slow()
        assert ping_then(ws) == [] and env.connected()
        ws.send_text(bad_full)                      # #5
        assert closed_code(ws) == 1008


def test_delta_rate_limits_apply_like_snapshots(env):
    with open_agent(env) as ws:
        ws.send_text(msg(1))
        for n in range(2, 9):
            ws.send_text(dmsg(n, n - 1, {"log": [str(n)]}))
        assert closed_code(ws) == 4004


def test_kill_switch_answers_resync_and_keeps_the_connection(make_env):
    e = make_env(delta=False)
    with open_agent(e) as ws:
        ws.send_text(msg(1))
        assert wait_for(seq_is(e, 1))
        ws.send_text(dmsg(2, 1, {"log": ["X"]}))
        assert ping_then(ws) == [{"t": "resync"}]
        ws.send_text(msg(3, view("FULL")))
        assert wait_for(seq_is(e, 3)) and snap_of(e)["log"] == ["FULL"]


def test_deltas_of_two_accounts_never_mix(env):
    web2, tok_b = second_account(env)
    try:
        with open_agent(env) as ws_a, open_agent(env, token=tok_b["access_token"]) as ws_b:
            ws_a.send_text(msg(1, view("A0")))
            ws_b.send_text(msg(1, view("B0")))
            assert wait_for(seq_is(env, 1)) and wait_for(seq_is(env, 1, web2))
            ws_a.send_text(dmsg(2, 1, {"log": ["MARK-A"]}))
            ws_b.send_text(dmsg(2, 1, {"log": ["MARK-B"]}))
            assert wait_for(seq_is(env, 2)) and wait_for(seq_is(env, 2, web2))
            ra, rb = env.web.get("/api/flight/state"), web2.get("/api/flight/state")
            assert "MARK-A" in ra.text and "MARK-B" not in ra.text
            assert "MARK-B" in rb.text and "MARK-A" not in rb.text
            ws_a.send_text(dmsg(8, 7, {"log": ["X"]}))  # A's resync does not touch B
            assert ping_then(ws_a) == [{"t": "resync"}]
            assert ping_then(ws_b) == []
    finally:
        web2.close()


def test_logs_never_hold_the_delta_content(env, caplog):
    with caplog.at_level(logging.DEBUG):
        with open_agent(env) as ws:
            ws.send_text(msg(1))
            assert wait_for(seq_is(env, 1))
            ws.send_text(dmsg(2, 1, {"log": ["SECRET-DELTA-XYZ"]}))
            ws.send_text(dmsg(9, 7, {"log": ["SECRET-DELTA-XYZ"]}))
            ws.send_text(dmsg(3, 2, {"log": {"SECRET-DELTA-XYZ": 1}}))
            ping_then(ws)
    assert "SECRET-DELTA-XYZ" not in caplog.text


def test_a_70kb_full_snapshot_is_followed_by_a_small_delta_without_resync(env):
    items = [{"id": "i%d" % n, "text": "t" * 290, "response": "r" * 290, "detail": "d" * 290} for n in range(80)]
    big = view("BIG", checklist={"bad": 0, "phase": {"id": "p", "name": "P"},
                                 "subphases": [{"id": "s", "name": "S", "items": items}]})
    size = len(json.dumps(big))
    assert 65536 < size < 96 * 1024, size  # between the old delta cap and the program's own trim limit
    with open_agent(env) as ws:
        ws.send_text(msg(1, big))
        assert wait_for(seq_is(env, 1))
        ws.send_text(dmsg(2, 1, {"spoke_n": 2}))
        assert wait_for(seq_is(env, 2))
        assert ping_then(ws) == [] and snap_of(env)["spoke_n"] == 2


def test_hub_tombstone_removed_again_keeps_its_first_ver():
    hub = Hub()
    conn = hub.new_conn(object(), "f")
    hub.register(1, conn)
    hub.store_snapshot(conn, 1, {"phase": "p", "log": ["a"]})
    hub.store_delta(conn, 2, {"phase": "p"}, [], ["log"])
    hub.store_delta(conn, 3, {"phase": "p"}, [], ["log"])
    assert conn.sections["log"] == (None, 2)
    hub.store_snapshot(conn, 4, {"phase": "p"})
    assert conn.sections["log"] == (None, 2)


def test_delta_seq_must_be_base_plus_one_and_dv_a_real_int(env):
    for bad in (dmsg(5, 1, {"log": ["X"]}), dmsg(0, 1, {"log": ["X"]}), dmsg(2, 1, {"log": ["X"]}, dv=1.0)):
        with open_agent(env) as ws:
            ws.send_text(msg(1))
            assert wait_for(seq_is(env, 1))
            ws.send_text(bad)
            assert ping_then(ws) == [{"t": "resync"}] and snap_of(env)["log"] == ["M1"]
