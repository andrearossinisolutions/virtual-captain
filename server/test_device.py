"""Device-code login tests (step 2c): TestClient, injectable clock, no network.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import devicecode  # noqa: E402
from server.app import CSP, create_app  # noqa: E402
from server.security import token_hash  # noqa: E402
from server.test_accounts import (BASE, PW, PW2, Clock, Mailer, cfg, hdr, logged,  # noqa: E402
                                  login, rows)


class Env:
    def __init__(self, tmp_path, **kw):
        self.clock, self.mailer, self.tmp = Clock(), Mailer(), tmp_path
        self.db = str(tmp_path / "t.db")
        self.app = create_app(cfg(tmp_path, **kw), mailer=self.mailer, clock=self.clock)
        self.prog = TestClient(self.app, base_url="https://testserver", client=("198.51.100.7", 5000))
        self.web = TestClient(self.app, base_url="https://testserver", client=("203.0.113.9", 4000))
        self.csrf = logged(self.web, self.mailer)

    def start(self, **body):
        r = self.prog.post("/api/device/start", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def poll(self, device_code):
        return self.prog.post("/api/device/poll", json={"device_code": device_code})

    def lookup(self, code, client=None, csrf=None):
        return (client or self.web).post("/api/device/lookup", json={"user_code": code},
                                         headers=hdr(csrf or self.csrf))

    def approve(self, code, lookup=True, confirm=None):
        """Like the page: look the code up, then approve with the last 4 characters typed."""
        if lookup:
            self.lookup(code)
        body = {"user_code": code, "confirm": code[-4:] if confirm is None else confirm}
        return self.web.post("/api/device/approve", json=body, headers=hdr(self.csrf))

    def pair(self):
        """start -> approve -> poll: returns the token response."""
        s = self.start()
        assert self.approve(s["user_code"]).status_code == 200
        r = self.poll(s["device_code"])
        assert r.status_code == 200, r.text
        return r.json()

    def refresh(self, token):
        return self.prog.post("/api/device/token", json={"grant_type": "refresh_token",
                                                          "refresh_token": token})


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def sql(env, query, args=()):
    c = sqlite3.connect(env.db)
    try:
        return c.execute(query, args).fetchall()
    finally:
        c.close()


# ---------- full cycle ----------

def test_full_cycle_and_second_poll_rejected(env):
    s = env.start(device_name="My PC")
    assert s["expires_in"] == 600 and s["interval"] == 5
    assert s["verification_uri"] == BASE + "/link"
    assert s["verification_uri_complete"] == BASE + "/link#" + s["user_code"]
    assert "?" not in s["verification_uri_complete"]
    assert len(s["user_code"]) == 9 and s["user_code"][4] == "-"
    assert not set(s["user_code"].replace("-", "")) & set("01ILO")
    p = env.poll(s["device_code"])
    assert p.status_code == 400 and p.json()["error"] == "authorization_pending"
    info = env.lookup(s["user_code"].lower()).json()
    assert info["status"] == "pending" and info["device_name"] == "My PC"
    assert info["ip"] == "198.51.100.7" and info["requested_at"] == env.clock.now
    assert env.approve(s["user_code"]).status_code == 200
    env.clock.advance(6)
    r = env.poll(s["device_code"])
    assert r.status_code == 200
    t = r.json()
    assert t["token_type"] == "Bearer" and t["expires_in"] == 600
    assert t["access_token"] and t["refresh_token"] and t["access_token"] != t["refresh_token"]
    again = env.poll(s["device_code"])
    assert again.status_code == 400 and again.json()["error"] == "expired_token"
    assert "access_token" not in again.text
    uid = env.app.state.devices.verify_access_token(t["access_token"])
    assert uid == sql(env, "SELECT id FROM users")[0][0]
    assert env.app.state.devices.verify_access_token(t["refresh_token"]) is None
    assert env.app.state.devices.verify_access_token("x" * 40) is None


def test_access_token_expires_after_ten_minutes(env):
    t = env.pair()
    env.clock.advance(599)
    assert env.app.state.devices.verify_access_token(t["access_token"]) is not None
    env.clock.advance(2)
    assert env.app.state.devices.verify_access_token(t["access_token"]) is None


def test_slow_down_raises_the_interval(env):
    s = env.start()
    assert env.poll(s["device_code"]).json()["error"] == "authorization_pending"
    env.clock.advance(1)
    r = env.poll(s["device_code"]).json()
    assert r == {"error": "slow_down", "interval": 10}
    env.clock.advance(5)  # below the new interval of 10
    assert env.poll(s["device_code"]).json()["error"] == "slow_down"
    env.clock.advance(16)
    assert env.poll(s["device_code"]).json()["error"] == "authorization_pending"


def test_polling_at_the_interval_is_fine(env):
    s = env.start()
    for _ in range(4):
        assert env.poll(s["device_code"]).json()["error"] == "authorization_pending"
        env.clock.advance(5)


def test_code_expires_at_600_seconds(env):
    s = env.start()
    env.clock.advance(599)
    assert env.poll(s["device_code"]).json()["error"] == "authorization_pending"
    env.clock.advance(1)
    assert env.poll(s["device_code"]).json()["error"] == "expired_token"
    assert env.lookup(s["user_code"]).json() == {"error": "invalid_code"}
    assert env.approve(s["user_code"]).status_code == 404


def test_deny(env):
    s = env.start()
    r = env.web.post("/api/device/deny", json={"user_code": s["user_code"]}, headers=hdr(env.csrf))
    assert r.status_code == 200
    p = env.poll(s["device_code"])
    assert p.status_code == 400 and p.json()["error"] == "access_denied"
    assert env.approve(s["user_code"]).status_code == 404  # already decided
    assert sql(env, "SELECT COUNT(*) FROM device_tokens")[0][0] == 0


def test_approve_only_once_and_unknown_poll(env):
    s = env.start()
    assert env.approve(s["user_code"]).status_code == 200
    assert env.approve(s["user_code"]).status_code == 404
    assert env.poll("z" * 43).json()["error"] == "expired_token"
    assert env.poll(12345).json()["error"] == "expired_token"


# ---------- user_code ----------

def test_user_code_collision_retries(env, monkeypatch):
    first = env.start()
    codes = iter([first["user_code"], first["user_code"], "ZZZZ-ZZZZ"])
    monkeypatch.setattr(devicecode, "new_user_code", lambda: next(codes))
    second = env.start()
    assert second["user_code"] == "ZZZZ-ZZZZ" and second["device_code"] != first["device_code"]


def test_expired_code_can_be_reused_by_a_new_start(env, monkeypatch):
    first = env.start()
    env.clock.advance(700)
    monkeypatch.setattr(devicecode, "new_user_code", lambda: first["user_code"])
    assert env.start()["user_code"] == first["user_code"]


def test_normalize_user_code():
    n = devicecode.normalize_user_code
    assert n("abcd efgh") == "ABCD-EFGH" and n("abcdefgh") == "ABCD-EFGH"
    for bad in ("ABCD-EFG", "ABCD-EFGHI", "ABCD-EFG0", "ABCD-EFGO", "ABCD-EFG1", "ABCD-EFGL",
                "", None, 5, "A" * 100):
        assert n(bad) is None


# ---------- attempts, lock, enumeration ----------

def test_five_wrong_codes_lock_the_session(env):
    for i in range(5):
        assert env.lookup(f"AAAA-BBB{i + 2}").status_code == 404
    s = env.start()
    r = env.lookup(s["user_code"])  # even a valid code is refused while locked
    assert r.status_code == 429 and r.json() == {"error": "too_many_attempts"}
    assert env.approve(s["user_code"]).status_code == 429
    env.clock.advance(901)
    assert env.lookup(env.start()["user_code"]).status_code == 200


def test_lock_is_per_ip_too(env):
    for i in range(5):
        env.lookup(f"AAAA-BBB{i + 2}")
    other = TestClient(env.app, base_url="https://testserver", client=("203.0.113.9", 4001))
    csrf = login(other).json()["csrf_token"]  # a fresh session from the same IP
    s = env.start()
    assert env.lookup(s["user_code"], other, csrf).status_code == 429


def test_own_valid_code_does_not_reset_the_failures(env):
    for i in range(4):
        env.lookup(f"AAAA-BBB{i + 2}")
    assert env.lookup(env.start()["user_code"]).status_code == 200  # a valid one in between
    env.lookup("AAAA-BBB9")
    assert env.lookup(env.start()["user_code"]).status_code == 429


def test_no_enumeration_same_answer(tmp_path):
    e = Env(tmp_path, device_lookup_max_failures=1000)
    used = e.start()
    e.approve(used["user_code"])
    expired = e.start()
    e.clock.advance(601)
    answers = {(r.status_code, r.text) for r in (
        e.lookup(c) for c in ("AAAA-BBBB", expired["user_code"], used["user_code"],
                              "bad", "ABCD-EFG0"))}
    assert answers == {(404, '{"error":"invalid_code"}')}


# ---------- website side protections ----------

def test_lookup_approve_deny_need_session_csrf_and_origin(env):
    s = env.start()
    anon = TestClient(env.app, base_url="https://testserver")
    for path in ("lookup", "approve", "deny"):
        url = f"/api/device/{path}"
        body = {"user_code": s["user_code"]}
        assert anon.post(url, json=body).status_code == 401
        assert env.web.post(url, json=body).status_code == 403  # no CSRF
        assert env.web.post(url, json=body, headers=hdr("wrong")).status_code == 403
        bad = {**hdr(env.csrf), "Origin": "https://evil.example"}
        assert env.web.post(url, json=body, headers=bad).status_code == 403
        cross = {**hdr(env.csrf), "Sec-Fetch-Site": "cross-site"}
        assert env.web.post(url, json=body, headers=cross).status_code == 403
    assert sql(env, "SELECT status FROM device_codes") == [("pending",)]  # nothing changed
    env.lookup(s["user_code"])
    good = {**hdr(env.csrf), "Origin": BASE}
    assert env.web.post("/api/device/approve", headers=good,
                        json={"user_code": s["user_code"], "confirm": s["user_code"][-4:]}
                        ).status_code == 200


def test_program_endpoints_reject_foreign_origin(env):
    r = env.prog.post("/api/device/start", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_approver_owns_the_token_and_it_is_not_valid_for_b(tmp_path):
    e = Env(tmp_path)
    web_b = TestClient(e.app, base_url="https://testserver", client=("203.0.113.50", 1))
    csrf_b = logged(web_b, e.mailer, email="b@example.test")
    s = e.start()
    web_b.post("/api/device/lookup", json={"user_code": s["user_code"]}, headers=hdr(csrf_b))
    web_b.post("/api/device/approve", headers=hdr(csrf_b),
               json={"user_code": s["user_code"], "confirm": s["user_code"][-4:]})
    tb = e.poll(s["device_code"]).json()
    ta = e.pair()
    ids = dict(sql(e, "SELECT email, id FROM users"))
    dev = e.app.state.devices
    assert dev.verify_access_token(tb["access_token"]) == ids["b@example.test"]
    assert dev.verify_access_token(ta["access_token"]) == ids["a@example.test"]
    # the refresh token of A only ever produces tokens of A
    new_a = e.refresh(ta["refresh_token"]).json()
    assert dev.verify_access_token(new_a["access_token"]) == ids["a@example.test"]
    # revoking with A's token does not touch B
    e.prog.post("/api/device/revoke", json={"token": ta["refresh_token"]})
    assert dev.verify_access_token(tb["access_token"]) == ids["b@example.test"]


def test_one_program_per_account_last_one_replaces(env):
    t1 = env.pair()
    t2 = env.pair()
    dev = env.app.state.devices
    assert dev.verify_access_token(t1["access_token"]) is None
    assert dev.verify_access_token(t2["access_token"]) is not None
    assert env.refresh(t1["refresh_token"]).status_code == 400
    assert env.refresh(t2["refresh_token"]).status_code == 200


# ---------- refresh rotation and reuse ----------

def test_refresh_rotation_and_reuse_revokes_the_family(env):
    t0 = env.pair()
    dev = env.app.state.devices
    r1 = env.refresh(t0["refresh_token"])
    assert r1.status_code == 200
    t1 = r1.json()
    assert t1["refresh_token"] != t0["refresh_token"] and t1["access_token"] != t0["access_token"]
    assert dev.verify_access_token(t0["access_token"]) is None  # the old access token is gone
    assert dev.verify_access_token(t1["access_token"]) is not None
    r2 = env.refresh(t1["refresh_token"]).json()
    # replay of an already rotated token: the whole family dies, including the newest pair
    replay = env.refresh(t0["refresh_token"])
    assert replay.status_code == 400 and replay.json() == {"error": "invalid_grant"}
    assert dev.verify_access_token(r2["access_token"]) is None
    assert env.refresh(r2["refresh_token"]).status_code == 400


def test_refresh_expires_after_90_days_and_bad_input(env):
    t = env.pair()
    for bad in ({"grant_type": "refresh_token"}, {"grant_type": "x", "refresh_token": t["refresh_token"]},
                {"grant_type": "refresh_token", "refresh_token": "short"},
                {"grant_type": "refresh_token", "refresh_token": t["access_token"]}):
        assert env.prog.post("/api/device/token", json=bad).status_code == 400
    env.clock.advance(90 * 86400 + 1)
    assert env.refresh(t["refresh_token"]).json() == {"error": "invalid_grant"}


def test_revoke_is_idempotent_and_generic(env):
    t = env.pair()
    for token in (t["access_token"], t["refresh_token"], t["refresh_token"], "unknown" * 6, "x", 5, None):
        r = env.prog.post("/api/device/revoke", json={"token": token})
        assert r.status_code == 200 and r.json() == {"ok": True}
    assert env.app.state.devices.verify_access_token(t["access_token"]) is None
    assert env.refresh(t["refresh_token"]).status_code == 400


# ---------- account events revoke the tokens ----------

def test_password_change_revokes_device_tokens(env):
    t = env.pair()
    pending = env.start()
    env.approve(pending["user_code"])  # approved, not collected yet
    r = env.web.post("/api/auth/password", headers=hdr(env.csrf),
                     json={"current_password": PW, "new_password": PW2})
    assert r.status_code == 200
    assert env.app.state.devices.verify_access_token(t["access_token"]) is None
    assert env.refresh(t["refresh_token"]).status_code == 400
    assert env.poll(pending["device_code"]).json()["error"] == "expired_token"


def test_reset_revokes_device_tokens(env):
    t = env.pair()
    env.web.cookies.clear()
    env.web.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = env.mailer.token()
    assert env.web.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 200
    assert env.app.state.devices.verify_access_token(t["access_token"]) is None
    assert env.refresh(t["refresh_token"]).status_code == 400


def test_account_deletion_removes_device_tokens(env):
    t = env.pair()
    r = env.web.post("/api/auth/delete", headers=hdr(env.csrf), json={"password": PW})
    assert r.status_code == 200
    assert env.app.state.devices.verify_access_token(t["access_token"]) is None
    assert rows(env.db, "device_tokens") == 0 and rows(env.db, "device_codes") == 0


# ---------- secrets: hashes only, never in logs ----------

def test_tokens_and_codes_only_as_hashes_and_never_logged(env, caplog):
    caplog.set_level(logging.DEBUG)
    s = env.start()
    env.lookup(s["user_code"])
    env.approve(s["user_code"])
    t = env.poll(s["device_code"]).json()
    env.refresh(t["refresh_token"])
    env.prog.post("/api/device/revoke", json={"token": t["access_token"]})
    secrets_ = [s["device_code"], t["access_token"], t["refresh_token"]]
    dump = " ".join(str(v) for row in sql(env, "SELECT * FROM device_codes") for v in row)
    dump += " ".join(str(v) for row in sql(env, "SELECT * FROM device_tokens") for v in row)
    for secret in secrets_:
        assert secret not in dump and secret not in caplog.text
    assert token_hash(s["device_code"]) in dump
    assert s["user_code"] not in caplog.text and "198.51.100.7" not in caplog.text


def test_request_ip_and_ua_are_dropped_when_decided_or_expired(env):
    s = env.start()
    assert sql(env, "SELECT request_ip FROM device_codes") == [("198.51.100.7",)]
    env.approve(s["user_code"])
    assert sql(env, "SELECT request_ip, request_ua FROM device_codes") == [("", "")]
    s2 = env.start()
    env.clock.advance(601)
    env.poll(s2["device_code"])
    assert sql(env, "SELECT request_ip FROM device_codes WHERE status='expired'") == [("",)]


def test_user_agent_is_truncated_and_cleaned(env):
    r = env.prog.post("/api/device/start", json={"device_name": "<b>x</b>"},
                      headers={"User-Agent": "VC/1 " + "a" * 500})
    s = r.json()
    info = env.lookup(s["user_code"]).json()
    assert len(info["user_agent"]) == 200
    assert info["device_name"] == "Virtual Captain program"


# ---------- limits, cleanup, headers ----------

def test_start_rate_limit_per_ip(tmp_path):
    e = Env(tmp_path, device_start_hourly=3)
    for _ in range(3):
        e.start()
    assert e.prog.post("/api/device/start", json={}).status_code == 429
    other = TestClient(e.app, base_url="https://testserver", client=("198.51.100.99", 1))
    assert other.post("/api/device/start", json={}).status_code == 200


def test_poll_rate_limit_per_ip(tmp_path):
    e = Env(tmp_path, device_poll_ip_minute=3)
    s = e.start()
    for _ in range(3):
        e.poll(s["device_code"])
    assert e.poll(s["device_code"]).status_code == 429


def test_cleanup_removes_expired_codes_and_old_tokens(env):
    t = env.pair()
    env.refresh(t["refresh_token"])  # leaves one rotated row
    env.start()
    assert rows(env.db, "device_tokens") == 2 and rows(env.db, "device_codes") == 2
    env.clock.advance(8 * 86400)
    env.start()  # triggers maybe_cleanup
    assert rows(env.db, "device_tokens") == 1  # the rotated one is gone after 7 days
    assert rows(env.db, "device_codes") == 1
    env.clock.advance(90 * 86400)
    env.start()
    assert rows(env.db, "device_tokens") == 0


def test_headers_no_store_and_csp_unchanged(env):
    for r in (env.prog.post("/api/device/start", json={}), env.poll("x" * 43),
              env.prog.post("/api/device/token", json={})):
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["content-security-policy"] == CSP
        assert r.headers["x-content-type-options"] == "nosniff"
    link = env.web.get("/link")
    assert link.status_code == 200 and link.headers["content-security-policy"] == CSP


def test_not_mounted_without_public_base_url(tmp_path):
    from server.config import AppConfig
    client = TestClient(create_app(AppConfig(db_path=str(tmp_path / "n.db"))))
    assert client.post("/api/device/start", json={}).status_code == 404
    assert client.get("/link").status_code == 404


# ---------- M1: approve needs the lookup and the typed last 4 characters ----------

def test_approve_without_lookup_is_refused_and_counted(env):
    s = env.start()
    r = env.approve(s["user_code"], lookup=False)
    assert r.status_code == 404 and r.json() == {"error": "invalid_code"}
    assert sql(env, "SELECT status FROM device_codes") == [("pending",)]
    assert sql(env, "SELECT COUNT(*) FROM login_failures")[0][0] == 3  # session, IP, account
    assert env.approve(s["user_code"]).status_code == 200  # lookup first: now fine


def test_approve_with_wrong_or_missing_confirm_is_refused_and_counted(env):
    s = env.start()
    env.lookup(s["user_code"])
    body = {"user_code": s["user_code"]}
    for extra in ({}, {"confirm": "AAAA"}, {"confirm": s["user_code"]}, {"confirm": 5},
                  {"confirm": s["user_code"][-3:]}):
        r = env.web.post("/api/device/approve", json={**body, **extra}, headers=hdr(env.csrf))
        if r.status_code == 429:
            break
        assert r.status_code == 400 and r.json() == {"error": "confirm_mismatch"}, extra
    assert sql(env, "SELECT status FROM device_codes") == [("pending",)]
    # the wrong guesses lock the account like wrong codes do (5 in a row)
    r = env.web.post("/api/device/approve", headers=hdr(env.csrf),
                     json={**body, "confirm": s["user_code"][-4:]})
    assert r.status_code == 429


def test_approve_with_right_confirm_is_ok_and_case_insensitive(env):
    s = env.start()
    env.lookup(s["user_code"])
    r = env.web.post("/api/device/approve", headers=hdr(env.csrf),
                     json={"user_code": s["user_code"], "confirm": s["user_code"][-4:].lower()})
    assert r.status_code == 200
    assert env.poll(s["device_code"]).status_code == 200


def test_lookup_by_another_user_does_not_enable_approve(tmp_path):
    e = Env(tmp_path)
    web_b = TestClient(e.app, base_url="https://testserver", client=("203.0.113.50", 1))
    csrf_b = logged(web_b, e.mailer, email="b@example.test")
    s = e.start()
    web_b.post("/api/device/lookup", json={"user_code": s["user_code"]}, headers=hdr(csrf_b))
    assert e.approve(s["user_code"], lookup=False).status_code == 404


def test_device_name_is_sanitised(env):
    for raw in ("Virtual Captain (official)", "a_b", "x" * 41, "caf\u00e9"):
        s = env.start(device_name=raw)
        assert env.lookup(s["user_code"]).json()["device_name"] == "Virtual Captain program"
    s = env.start(device_name="Home PC v1.2-b")
    assert env.lookup(s["user_code"]).json()["device_name"] == "Home PC v1.2-b"


# ---------- M2: real concurrency (threads, barrier, distinct DB connections) ----------

def race(n, fn):
    barrier, out = threading.Barrier(n), [None] * n

    def run(i):
        barrier.wait()
        try:
            out[i] = ("ok", fn())
        except Exception as exc:  # AccountError and friends
            out[i] = ("err", getattr(exc, "code", repr(exc)))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    return out


def test_two_simultaneous_polls_one_gets_the_tokens(env):
    dev = env.app.state.devices
    for _ in range(5):  # repeat: the race is timing dependent
        s = env.start()
        env.approve(s["user_code"])
        out = race(4, lambda: dev.poll(s["device_code"], "198.51.100.7"))
        assert [o[0] for o in out].count("ok") == 1, out
        assert all(o == ("err", "expired_token") for o in out if o[0] == "err"), out


def test_two_simultaneous_refreshes_revoke_the_family(env):
    """Chosen logic: the first rotates, the second one presents an already rotated token, so it is
    treated as a reuse and the whole family is revoked (a legitimate client that retries at the
    same moment has to link again)."""
    dev = env.app.state.devices
    t = env.pair()
    out = race(2, lambda: dev.refresh(t["refresh_token"], "198.51.100.7"))
    assert sorted(o[0] for o in out) == ["err", "ok"], out
    assert [o[1] for o in out if o[0] == "err"] == ["invalid_grant"]
    new = next(o[1] for o in out if o[0] == "ok")
    assert dev.verify_access_token(new["access_token"]) is None
    assert env.refresh(new["refresh_token"]).status_code == 400
    assert env.refresh(t["refresh_token"]).status_code == 400


def test_two_simultaneous_approves_only_one_wins(tmp_path):
    env = Env(tmp_path, device_lookup_max_failures=1000)  # the losers count as wrong attempts
    dev = env.app.state.devices
    uid = sql(env, "SELECT id FROM users")[0][0]
    sess = {"sid": 1, "uid": uid, "email": "a@example.test", "csrf": "x"}
    for _ in range(3):
        s = env.start()
        dev.lookup(sess, s["user_code"], "203.0.113.9")
        out = race(4, lambda: dev.decide(sess, s["user_code"], "203.0.113.9", True,
                                         s["user_code"][-4:]))
        assert [o[0] for o in out].count("ok") == 1, out
        assert sql(env, "SELECT status FROM device_codes WHERE user_code=?",
                   (s["user_code"],)) == [("approved",)]
        env.poll(s["device_code"])  # collect, so the next round starts clean


# ---------- m2, m3 ----------

def test_cleanup_clears_expired_pending_rows(env):
    env.start()
    env.clock.advance(700)
    env.app.state.devices.acc.maybe_cleanup()
    assert sql(env, "SELECT COUNT(*) FROM device_codes")[0][0] == 0


def test_verify_access_token_writes_last_used_at_most_once_a_minute(env):
    t = env.pair()
    dev = env.app.state.devices
    first = sql(env, "SELECT last_used_at FROM device_tokens")[0][0]
    env.clock.advance(30)
    assert dev.verify_access_token(t["access_token"]) is not None
    assert sql(env, "SELECT last_used_at FROM device_tokens")[0][0] == first
    env.clock.advance(31)
    assert dev.verify_access_token(t["access_token"]) is not None
    assert sql(env, "SELECT last_used_at FROM device_tokens")[0][0] == first + 61
