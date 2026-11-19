"""C1: the user's own linked programs (list, revoke, revoke all) and the immediate close of the
uplink. Same style as test_agent.py. Skipped when fastapi/httpx/argon2 are missing."""
from __future__ import annotations

import json
import logging
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from server import wire  # noqa: E402
from server.test_accounts import PW, PW2, hdr  # noqa: E402
from server.test_agent import (closed_code, make_env, open_agent,  # noqa: E402,F401
                               second_account, wait_for)

# a long re-check: a close that arrives quickly can only come from the immediate path
SLOW = dict(agent_family_recheck=30.0, agent_stale_after=60)


def mine(e, client=None):
    r = (client or e.web).get("/api/device/mine")
    assert r.status_code == 200, r.text
    return r.json()["devices"]


def revoke(e, handle, client=None, csrf=None):
    return (client or e.web).post("/api/device/mine/revoke", json={"id": handle},
                                  headers=hdr(csrf or e.csrf))


def family_of(e):
    return e.app.state.devices.verify_access(e.access)[1]


# ---------- list ----------

def test_list_empty_and_one_program(tmp_path, make_env):
    from server.test_device import Env
    (tmp_path / "none").mkdir()
    nobody = Env(tmp_path / "none")  # a signed-in account that never linked a program
    assert mine(nobody) == []
    e = make_env()
    (d,) = mine(e)
    assert set(d) == {"id", "name", "last_used_at", "expires_at", "online"}
    assert d["online"] is False and d["name"] and d["expires_at"] > e.clock()


def test_list_has_one_entry_after_rotation_and_no_family_id(make_env):
    e = make_env()
    fam = family_of(e)
    t2 = e.refresh(e.token["refresh_token"]).json()
    assert t2["refresh_token"]
    (d,) = mine(e)
    assert fam not in json.dumps(d) and d["id"] != fam
    assert d["id"] == e.app.state.devices.handle(fam) and len(d["id"]) == 24


def test_list_online_flag_and_no_ip(make_env):
    e = make_env(**SLOW)
    with open_agent(e):
        assert wait_for(e.connected)
        (d,) = mine(e)
        assert d["online"] is True
        assert "198.51.100.7" not in json.dumps(d)


def test_list_hides_expired(make_env):
    import sqlite3
    e = make_env()
    assert len(mine(e)) == 1
    c = sqlite3.connect(e.db)
    c.execute("UPDATE device_tokens SET expires_at=1")
    c.commit()
    c.close()
    assert mine(e) == []


def test_list_needs_session(make_env):
    e = make_env()
    from fastapi.testclient import TestClient
    anon = TestClient(e.app, base_url="https://testserver")
    assert anon.get("/api/device/mine").status_code == 401


# ---------- revoke ----------

def test_revoke_one_closes_the_socket_at_once(make_env):
    e = make_env(**SLOW)
    devices, fam = e.app.state.devices, family_of(e)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        (d,) = mine(e)
        t0 = time.monotonic()
        assert revoke(e, d["id"]).status_code == 200
        assert closed_code(ws) == wire.CLOSE_REVOKED
        assert time.monotonic() - t0 < 3.0  # far below the 30 s re-check
    assert wait_for(lambda: e.app.state.hub.count(), 0)
    assert e.refresh(e.token["refresh_token"]).status_code == 400
    assert devices.verify_access(e.access) is None and devices.family_active(fam) is False
    assert mine(e) == []


def test_revoke_all(make_env):
    e = make_env(**SLOW)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        r = e.web.post("/api/device/mine/revoke-all", json={}, headers=hdr(e.csrf))
        assert r.status_code == 200
        assert closed_code(ws) == wire.CLOSE_REVOKED
    assert e.app.state.devices.verify_access(e.access) is None
    assert mine(e) == []
    assert e.web.post("/api/device/mine/revoke-all", json={}, headers=hdr(e.csrf)).status_code == 200  # idempotent


def test_revoke_all_drops_an_approved_uncollected_code(make_env):
    e = make_env()
    s = e.start()
    assert e.approve(s["user_code"]).status_code == 200  # approved, not collected yet
    assert e.web.post("/api/device/mine/revoke-all", json={}, headers=hdr(e.csrf)).status_code == 200
    assert e.poll(s["device_code"]).status_code == 400
    assert mine(e) == []


def test_revoke_with_nobody_connected(make_env):
    e = make_env()
    (d,) = mine(e)
    assert revoke(e, d["id"]).status_code == 200
    assert e.app.state.devices.verify_access(e.access) is None


def test_revoke_isolation_between_accounts(make_env):
    e = make_env(**SLOW)
    web2, tok_b = second_account(e)
    (db,) = mine(e, web2)
    csrf2 = web2.get("/api/auth/me").json()["csrf_token"]
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        mine_a = mine(e)[0]["id"]
        theirs = revoke(e, db["id"])                    # A tries B's handle
        unknown = revoke(e, "0" * 24)
        assert (theirs.status_code, theirs.json()) == (unknown.status_code, unknown.json()) == (404, {"error": "not_found"})
        assert e.app.state.devices.verify_access(tok_b["access_token"]) is not None  # B untouched
        assert mine(e, web2)
        assert e.connected()                                                           # A still connected
        assert revoke(e, mine_a).status_code == 200
        assert closed_code(ws) == wire.CLOSE_REVOKED
    assert revoke(e, db["id"], client=web2, csrf=csrf2).status_code == 200


def test_unknown_handles_are_counted_and_lock(make_env):
    e = make_env()
    for _ in range(5):
        assert revoke(e, "x" * 24).status_code == 404
    assert revoke(e, "x" * 24).status_code == 429
    (d,) = mine(e)
    assert revoke(e, d["id"]).status_code == 429  # locked even for the right handle


def test_bad_handle_shapes_are_404(make_env):
    e = make_env()
    for bad in (None, 5, "", "a" * 500, ["x"]):
        assert revoke(e, bad).status_code == 404


def test_revoke_needs_session_csrf_and_origin(make_env):
    e = make_env()
    from fastapi.testclient import TestClient
    (d,) = mine(e)
    anon = TestClient(e.app, base_url="https://testserver")
    for path, body in (("/api/device/mine/revoke", {"id": d["id"]}), ("/api/device/mine/revoke-all", {})):
        assert anon.post(path, json=body).status_code == 401
        assert e.web.post(path, json=body).status_code == 403                           # no CSRF
        assert e.web.post(path, json=body, headers=hdr("x" * 20)).status_code == 403   # wrong CSRF
        h = dict(hdr(e.csrf), Origin="https://evil.example")
        assert e.web.post(path, json=body, headers=h).status_code == 403               # foreign Origin
    assert len(mine(e)) == 1  # nothing was revoked


def test_handle_is_not_derivable_from_the_family_id(make_env):
    import hashlib
    e = make_env()
    fam = family_of(e)
    (d,) = mine(e)
    for guess in (fam, fam.encode().hex(), hashlib.sha256(fam.encode()).hexdigest()[:24],
                  hashlib.sha256(("dev:" + fam).encode()).hexdigest()[:24]):
        assert d["id"] not in guess and guess[:24] != d["id"]
    # and the family id itself is not accepted as a handle
    assert revoke(e, fam).status_code == 404


def test_no_token_or_handle_in_the_logs(make_env, caplog):
    caplog.set_level(logging.DEBUG)
    e = make_env(**SLOW)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        (d,) = mine(e)
        assert revoke(e, d["id"]).status_code == 200
        closed_code(ws)
    text = caplog.text
    for secret in (d["id"], e.access, e.token["refresh_token"], family_of_safe(e)):
        assert not secret or secret not in text


def family_of_safe(e):
    with e.app.state.devices.db.read() as c:
        row = c.execute("SELECT family_id FROM device_tokens LIMIT 1").fetchone()
    return row["family_id"] if row else ""


# ---------- immediate close on password change, reset, deletion ----------

def _by_password(e):
    assert e.web.post("/api/auth/password", json={"current_password": PW, "new_password": PW2},
                      headers=hdr(e.csrf)).status_code == 200


def _by_reset(e):
    e.web.cookies.clear()
    e.web.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = e.mailer.token()
    assert e.web.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 200


def _by_deletion(e):
    assert e.web.post("/api/auth/delete", json={"password": PW}, headers=hdr(e.csrf)).status_code == 200


@pytest.mark.parametrize("action", [_by_password, _by_reset, _by_deletion])
def test_account_changes_close_the_socket_at_once(make_env, action):
    e = make_env(**SLOW)
    with open_agent(e) as ws:
        assert wait_for(e.connected)
        t0 = time.monotonic()
        action(e)
        assert closed_code(ws) == wire.CLOSE_REVOKED
        assert time.monotonic() - t0 < 3.0
    assert wait_for(lambda: e.app.state.hub.count(), 0)


def test_a_failing_on_revoke_callback_does_not_break_the_change(make_env):
    e = make_env()

    async def boom(_uid):
        raise RuntimeError("boom")
    # the callback lives on the Accounts instance built by create_app
    accounts = e.app.state.devices.acc
    accounts.on_revoke = boom
    _by_password(e)
    assert e.app.state.devices.verify_access(e.access) is None


# ---------- hub, no DB ----------

def test_hub_close_family_ignores_other_families_and_users():
    import asyncio
    from server.hub import Hub

    class WS:
        def __init__(self):
            self.codes = []

        async def close(self, code):
            self.codes.append(code)

    hub, ws = Hub(), WS()
    conn = hub.new_conn(ws, "fam1")
    hub.register(1, conn)

    async def go():
        await hub.close_family(1, "other", 4003)
        await hub.close_family(2, "fam1", 4003)
        await hub.close_user(2, 4003)
        assert ws.codes == []
        await hub.close_family(1, "fam1", 4003)
        await hub.close_user(1, 4003)
    asyncio.run(go())
    assert ws.codes == [4003, 4003]
    assert hub.is_online(1, "fam1") and not hub.is_online(1, "x")
