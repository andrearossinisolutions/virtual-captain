"""Account API tests: TestClient, fake mailer, temporary DB, injectable clock, no network.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import logging
import re
import sqlite3
import statistics
import stat
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import security  # noqa: E402
from server.app import create_app  # noqa: E402
from server.config import AppConfig, AppConfigError, load_app_config  # noqa: E402
from server.db import Database  # noqa: E402

BASE = "https://vc.example.test"
PW = "correct horse battery"
PW2 = "another long passphrase"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Mailer:
    def __init__(self):
        self.sent = []
        self.fail = False

    def __call__(self, to, subject, body):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((to, subject, body))

    def token(self, index=-1):
        return re.search(r"#([A-Za-z0-9_-]+)", self.sent[index][2]).group(1)


def cfg(tmp_path, **kw):
    kw.setdefault("ip_hourly", 1000)
    kw.setdefault("preview_ip_hourly", 1000)
    kw.setdefault("ip_daily", 1000)
    return AppConfig(public_base_url=BASE, db_path=str(tmp_path / "t.db"),
                     secret_key="k" * 40, **kw)


@pytest.fixture
def env(tmp_path):
    clock, mailer = Clock(), Mailer()
    app = create_app(cfg(tmp_path), mailer=mailer, clock=clock)
    client = TestClient(app, base_url="https://vc.example.test")
    return client, mailer, clock, tmp_path


def make_client(tmp_path, clock, mailer, peer=("203.0.113.9", 4000), **kw):
    app = create_app(cfg(tmp_path, **kw), mailer=mailer, clock=clock)
    return TestClient(app, base_url="https://vc.example.test", client=peer)


def register(client, email="a@example.test", simbrief_id=None):
    body = {"email": email}
    if simbrief_id is not None:
        body["simbrief_id"] = simbrief_id
    return client.post("/api/auth/register", json=body)


def verified(client, mailer, email="a@example.test", password=PW):
    """Register and verify choosing the password (this also opens a session, dropped here)."""
    register(client, email)
    r = client.post("/api/auth/verify", json={"token": mailer.token(), "password": password})
    assert r.status_code == 200
    client.cookies.clear()


def login(client, email="a@example.test", password=PW):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    return r


def logged(client, mailer, email="a@example.test", password=PW):
    verified(client, mailer, email, password)
    r = login(client, email, password)
    assert r.status_code == 200
    return r.json()["csrf_token"]


def hdr(csrf):
    return {"X-CSRF-Token": csrf}


def rows(path, table):
    c = sqlite3.connect(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


# ---------- docs, config, DB ----------

def test_docs_and_openapi_are_404(env):
    client = env[0]
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404


def test_security_headers_on_api(env):
    r = env[0].get("/api/auth/me")
    assert r.status_code == 401 and "default-src 'none'" in r.headers["Content-Security-Policy"]


def test_api_off_without_public_base_url(tmp_path, caplog):
    app = create_app(AppConfig(db_path=str(tmp_path / "x.db")))
    assert "NOT mounted" in caplog.text
    assert TestClient(app).post("/api/auth/login", json={}).status_code == 404
    assert not (tmp_path / "x.db").exists()  # nothing touched the disk


def test_config_validation():
    key = "k" * 40
    ok = load_app_config({"PUBLIC_BASE_URL": "https://a.example/", "SECRET_KEY": key})
    assert ok.public_base_url == "https://a.example"
    assert load_app_config({"PUBLIC_BASE_URL": "http://localhost:8000", "SECRET_KEY": key})
    for bad in ("http://a.example", "https://a.example/path", "https://a.example#x",
                "http://localhost.evil.example", "http://127.0.0.1.evil.example", "https://"):
        with pytest.raises(AppConfigError):
            load_app_config({"PUBLIC_BASE_URL": bad, "SECRET_KEY": key})
    with pytest.raises(AppConfigError) as e:  # base URL without secret: refuse to start
        load_app_config({"PUBLIC_BASE_URL": "https://a.example"})
    assert "SECRET_KEY" in str(e.value)
    with pytest.raises(AppConfigError):
        create_app(AppConfig(public_base_url="https://a.example"))
    assert load_app_config({}).public_base_url == ""
    with pytest.raises(AppConfigError):
        load_app_config({"SECRET_KEY": "short"})
    with pytest.raises(AppConfigError):
        load_app_config({"RATE_IP_HOURLY": "x"})


def test_db_file_mode_wal_and_migrations(env):
    client, _, _, tmp = env
    register(client)
    path = tmp / "t.db"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    c = sqlite3.connect(path)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        c.close()
    Database(str(path))._prepare()  # re-opening an migrated DB is a no-op


# ---------- registration ----------

def vpost(client, token, password=PW):
    return client.post("/api/auth/verify", json={"token": token, "password": password})


def test_register_identical_response_new_and_existing(env):
    client, mailer, _, _ = env
    r1 = register(client)
    r2 = register(client)  # exists, unverified
    assert vpost(client, mailer.token()).status_code == 200
    client.cookies.clear()
    r3 = register(client)  # exists, verified
    assert r1.status_code == r2.status_code == r3.status_code == 200
    assert r1.content == r2.content == r3.content
    subjects = [m[1] for m in mailer.sent]
    assert subjects[0].startswith("Confirm") and subjects[1].startswith("Confirm")
    assert "sign-up attempt" in subjects[2]
    assert "#" not in mailer.sent[2][2]  # the "someone tried" mail has no link


def test_register_never_hashes(env, monkeypatch):
    client, mailer, _, _ = env
    calls = []
    real = security._hash
    monkeypatch.setattr(security, "_hash", lambda p: calls.append(1) or real(p))
    register(client)
    register(client)
    assert calls == []  # the password is chosen at verification: one hash, there


def test_register_timing_similar(env):
    client, _, _, _ = env
    register(client, "base@example.test")
    new, old = [], []
    for i in range(10):
        t = time.perf_counter()
        register(client, f"new{i}@example.test")
        new.append(time.perf_counter() - t)
        t = time.perf_counter()
        register(client, "base@example.test")
        old.append(time.perf_counter() - t)
    a, b = statistics.median(new), statistics.median(old)
    assert max(a, b) / min(a, b) < 4


def test_register_validation(env):
    client = env[0]
    for body in ({"email": "bad"}, {"email": 1}, {}, {"email": "a@example.test", "simbrief_id": 5},
                 {"email": "a@example.test", "simbrief_id": "x" * 41},
                 {"email": "a@example.test", "simbrief_id": "bad;name"}):
        assert client.post("/api/auth/register", json=body).status_code == 400
    assert client.post("/api/auth/register", content=b"nope",
                       headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/auth/register", content=b"{}",
                       headers={"content-type": "text/plain"}).status_code == 400


def test_register_ignores_a_password_field(env):
    client, mailer, _, tmp = env
    client.post("/api/auth/register", json={"email": "a@example.test", "password": PW})
    assert login(client, password=PW).status_code == 401
    assert stored_hash(tmp) == ""


def stored_hash(tmp, email="a@example.test"):
    c = sqlite3.connect(tmp / "t.db")
    try:
        return c.execute("SELECT pw_hash FROM users WHERE email=?", (email,)).fetchone()[0]
    finally:
        c.close()


def test_email_is_normalised(env):
    client, mailer, _, _ = env
    register(client, "  Mixed.Case@Example.TEST ")
    assert mailer.sent[0][0] == "mixed.case@example.test"
    vpost(client, mailer.token())
    assert login(client, "MIXED.case@example.test").status_code == 200


def test_password_nfkc_and_hash_is_argon2id(env):
    client, mailer, _, tmp = env
    verified(client, mailer, password="ｐａｓｓｗｏｒｄ-wide-1")  # full-width letters
    assert login(client, password="password-wide-1").status_code == 200
    c = sqlite3.connect(tmp / "t.db")
    try:
        h = c.execute("SELECT pw_hash FROM users").fetchone()[0]
    finally:
        c.close()
    assert h.startswith("$argon2id$") and "m=32768" in h and "p=1" in h


def test_mail_failure_does_not_change_response(env):
    client, mailer, _, _ = env
    mailer.fail = True
    assert register(client).status_code == 200


# ---------- verification ----------

def test_verify_sets_password_opens_session_single_use(env):
    client, mailer, _, tmp = env
    register(client)
    token = mailer.token()
    assert BASE + "/verify#" in mailer.sent[0][2]
    for _ in range(3):  # preview never consumes
        r = client.post("/api/auth/verify/preview", json={"token": token})
        assert r.json() == {"valid": True, "kind": "verify"}
    assert client.get("/api/auth/verify", params={"token": token}).status_code in (404, 405)
    assert login(client).status_code == 401  # still unverified, no password yet
    r = vpost(client, token)
    assert r.status_code == 200 and r.json()["kind"] == "verify" and r.json()["csrf_token"]
    assert "__Host-session" in r.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200  # session opened by the verification
    assert vpost(client, token).status_code == 400  # used
    assert client.post("/api/auth/verify/preview", json={"token": token}).json()["valid"] is False
    assert login(client).status_code == 200
    assert stored_hash(tmp).startswith("$argon2id$")


def test_verify_short_or_missing_password_does_not_consume_token(env):
    client, mailer, _, _ = env
    register(client)
    token = mailer.token()
    for pw in ("short", "x" * 129, None, 5):
        body = {"token": token} if pw is None else {"token": token, "password": pw}
        assert client.post("/api/auth/verify", json=body).status_code == 400
    assert client.post("/api/auth/verify/preview", json={"token": token}).json()["valid"] is True
    assert login(client).status_code == 401
    assert vpost(client, token).status_code == 200


def test_verify_one_hash_and_none_for_bad_token(env, monkeypatch):
    client, mailer, _, _ = env
    register(client)
    calls = []
    real = security._hash
    monkeypatch.setattr(security, "_hash", lambda p: calls.append(1) or real(p))
    assert vpost(client, "x" * 43).status_code == 400
    assert calls == []
    assert vpost(client, mailer.token()).status_code == 200
    assert len(calls) == 1


def test_verify_wrong_and_expired(env):
    client, mailer, clock, _ = env
    register(client)
    assert vpost(client, "x" * 43).status_code == 400
    assert client.post("/api/auth/verify", json={"token": 5, "password": PW}).status_code == 400
    assert client.post("/api/auth/verify", json={}).status_code == 400
    clock.advance(24 * 3600 + 1)
    assert vpost(client, mailer.token()).status_code == 400


def test_tokens_stored_hashed_only(env):
    client, mailer, _, tmp = env
    register(client)
    token = mailer.token()
    raw = (tmp / "t.db").read_bytes()
    assert token.encode() not in raw


def test_no_login_before_password_is_set_and_prehijack_is_impossible(env):
    client, mailer, _, tmp = env
    # the attacker registers the victim's address (and cannot choose a password)
    register(client, "victim@example.test")
    assert stored_hash(tmp, "victim@example.test") == ""
    for guess in ("", PW, "attacker chosen password"):
        assert login(client, "victim@example.test", guess).status_code == 401
    # the link goes to the victim's mailbox only; a second registration changes nothing
    first = mailer.token()
    register(client, "victim@example.test", "999")
    assert stored_hash(tmp, "victim@example.test") == ""
    assert vpost(client, first).status_code == 400  # replaced by the newest link
    # the victim chooses the password: only that one works
    assert vpost(client, mailer.token(), "victim chosen password").status_code == 200
    client.cookies.clear()
    assert login(client, "victim@example.test", "attacker chosen password").status_code == 401
    assert login(client, "victim@example.test", "victim chosen password").status_code == 200


def test_register_on_verified_account_changes_nothing(env):
    client, mailer, _, _ = env
    verified(client, mailer)
    register(client, simbrief_id="evil")
    assert login(client, password=PW).status_code == 200
    assert client.get("/api/auth/me").json()["simbrief_id"] == ""


def test_register_again_unverified_resends_link_last_simbrief_wins(env):
    client, mailer, _, _ = env
    register(client, simbrief_id="alice")
    register(client, simbrief_id="mallory")
    register(client)  # empty value: keeps the stored one
    assert len(mailer.sent) == 3
    vpost(client, mailer.token())
    assert client.get("/api/auth/me").json()["simbrief_id"] == "mallory"


def test_api_responses_are_no_store(env):
    client, mailer, _, _ = env
    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"
    assert register(client).headers["cache-control"] == "no-store"
    r = vpost(client, mailer.token())
    assert r.headers["cache-control"] == "no-store"
    assert login(client).headers["cache-control"] == "no-store"
    assert client.get("/healthz").headers["cache-control"] == "no-cache"


def test_reset_token_on_verify_is_not_consumed(env):
    client, mailer, _, _ = env
    verified(client, mailer)
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = mailer.token()
    assert vpost(client, token, PW2).status_code == 400
    assert client.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 200


def test_resend_for_unverified_replaces_old_token(env):
    client, mailer, _, _ = env
    register(client)
    first = mailer.token()
    register(client)
    assert vpost(client, first).status_code == 400
    assert vpost(client, mailer.token()).status_code == 200


def test_verify_consumes_other_tokens_of_the_user(env):
    client, mailer, _, tmp = env
    register(client)
    vpost(client, mailer.token())
    assert rows(tmp / "t.db", "email_tokens") == 0


def test_reset_before_verification_sends_nothing_and_cannot_set_password(env):
    client, mailer, _, _ = env
    register(client)
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    assert len(mailer.sent) == 1
    assert login(client).status_code == 401


# ---------- login, cookie, sessions ----------

def test_login_before_verification_rejected_and_generic(env):
    client, mailer, _, _ = env
    register(client)
    a = login(client)
    b = login(client, "nobody@example.test")
    c = login(client, password="wrong password!")
    assert a.status_code == b.status_code == c.status_code == 401
    assert a.content == b.content == c.content
    assert "set-cookie" not in a.headers


def test_cookie_attributes(env):
    client, mailer, _, _ = env
    verified(client, mailer)
    r = login(client)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-session=")
    low = cookie.lower()
    assert "secure" in low and "httponly" in low and "samesite=lax" in low and "path=/" in low
    assert "domain" not in low
    value = cookie.split(";")[0].split("=", 1)[1]
    assert len(value) >= 40 and value not in open(env[3] / "t.db", "rb").read().decode("latin1")


def test_me_and_logout(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    me = client.get("/api/auth/me").json()
    assert me == {"email": "a@example.test", "verified": True, "simbrief_id": "", "csrf_token": csrf}
    assert client.post("/api/auth/logout").status_code == 403  # no CSRF
    assert client.post("/api/auth/logout", headers=hdr(csrf)).status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_session_expiry_absolute_and_idle(env):
    client, mailer, clock, _ = env
    logged(client, mailer)
    clock.advance(6 * 86400)
    assert client.get("/api/auth/me").status_code == 200  # activity renews idle timer
    clock.advance(6 * 86400)
    assert client.get("/api/auth/me").status_code == 200
    clock.advance(7 * 86400 + 1)  # idle > 7 days
    assert client.get("/api/auth/me").status_code == 401
    login(client)
    for _ in range(6):  # keep active: 6 day steps until past 30 days absolute
        clock.advance(6 * 86400)
        status = client.get("/api/auth/me").status_code
    assert status == 401


# ---------- CSRF and Origin ----------

def test_csrf_missing_wrong_and_ok(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    body = {"simbrief_id": "12345"}
    assert client.post("/api/auth/settings", json=body).status_code == 403
    assert client.post("/api/auth/settings", json=body, headers=hdr("wrong")).status_code == 403
    assert client.post("/api/auth/settings", json=body, headers=hdr(csrf)).status_code == 200


def test_origin_and_fetch_site_checks(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    ok = {**hdr(csrf), "Origin": BASE}
    assert client.post("/api/auth/settings", json={"simbrief_id": "1"}, headers=ok).status_code == 200
    bad = {**hdr(csrf), "Origin": "https://evil.example"}
    assert client.post("/api/auth/settings", json={"simbrief_id": "1"}, headers=bad).status_code == 403
    cross = {**hdr(csrf), "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/auth/settings", json={"simbrief_id": "1"}, headers=cross).status_code == 403
    same_site = {**hdr(csrf), "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/auth/settings", json={"simbrief_id": "1"}, headers=same_site).status_code == 200
    # unauthenticated POSTs are checked too
    assert client.post("/api/auth/login", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert register(client, "z@example.test").status_code == 200


def test_unauthenticated_is_401_before_csrf(env):
    client = env[0]
    for path in ("settings", "password", "email", "delete", "logout"):
        assert client.post(f"/api/auth/{path}", json={}).status_code == 401


# ---------- lockout and rate limits ----------

def test_lockout_after_failures_and_unlock(env):
    client, mailer, clock, _ = env
    verified(client, mailer)
    for _ in range(5):
        assert login(client, password="bad bad bad bad").status_code == 401
    assert login(client).status_code == 429  # even with the right password
    assert login(client, "ghost@example.test").status_code == 401  # unknown address: own counter
    clock.advance(14 * 60)
    assert login(client).status_code == 429
    clock.advance(2 * 60)
    assert login(client).status_code == 200


def test_lockout_for_unknown_address_looks_the_same(env):
    client = env[0]
    for _ in range(5):
        assert login(client, "ghost@example.test").status_code == 401
    assert login(client, "ghost@example.test").status_code == 429


def test_register_rate_limit_per_ip_and_survives_restart(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, ip_hourly=3, ip_daily=20)
    for i in range(3):
        assert register(client, f"u{i}@example.test").status_code == 200
    assert register(client, "u9@example.test").status_code == 429
    again = make_client(tmp_path, clock, mailer, ip_hourly=3, ip_daily=20)  # "restart"
    assert register(again, "u9@example.test").status_code == 429
    clock.advance(3601)
    assert register(again, "u9@example.test").status_code == 200


def test_register_daily_limit(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, ip_hourly=100, ip_daily=4)
    for i in range(4):
        assert register(client, f"d{i}@example.test").status_code == 200
        clock.advance(1800)
    assert register(client, "dx@example.test").status_code == 429
    clock.advance(86400)
    assert register(client, "dx@example.test").status_code == 200


def test_mail_limits_per_address_and_global_keep_response_identical(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, mail_addr_hourly=2, mail_daily=3)
    for _ in range(4):
        assert register(client).status_code == 200
    assert len(mailer.sent) == 2  # per-address cap, same 200 answer
    clock.advance(3601)
    register(client, "b@example.test")
    register(client, "c@example.test")
    assert len(mailer.sent) == 3  # global daily cap reached
    clock.advance(3601 * 24)
    register(client, "d@example.test")
    assert len(mailer.sent) == 4


def test_ip_from_forwarded_for_only_when_peer_is_loopback(tmp_path):
    clock, mailer = Clock(), Mailer()
    # peer is Apache on loopback: the LAST X-Forwarded-For value is the client
    local = make_client(tmp_path, clock, mailer, peer=("127.0.0.1", 1), ip_hourly=2)
    for i, spoof in enumerate(("1.1.1.1", "2.2.2.2")):  # client-forged first value changes: no help
        h = {"X-Forwarded-For": f"{spoof}, 198.51.100.7"}
        assert local.post("/api/auth/register", json={"email": f"l{i}@example.test"},
                          headers=h).status_code == 200
    h = {"X-Forwarded-For": "3.3.3.3, 198.51.100.7"}
    assert local.post("/api/auth/register", json={"email": "l9@example.test"},
                      headers=h).status_code == 429
    # a different real client behind Apache is counted separately
    h = {"X-Forwarded-For": "198.51.100.8"}
    assert local.post("/api/auth/register", json={"email": "l8@example.test"},
                      headers=h).status_code == 200


def test_forwarded_for_ignored_from_other_peers(tmp_path):
    clock, mailer = Clock(), Mailer()
    remote = make_client(tmp_path, clock, mailer, peer=("203.0.113.50", 1), ip_hourly=2)
    for i in range(2):
        h = {"X-Forwarded-For": f"9.9.9.{i}"}  # rotating spoofed addresses must not help
        assert remote.post("/api/auth/register", json={"email": f"r{i}@example.test"},
                           headers=h).status_code == 200
    h = {"X-Forwarded-For": "9.9.9.77"}
    assert remote.post("/api/auth/register", json={"email": "r9@example.test"},
                       headers=h).status_code == 429


def test_login_ip_limit(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, login_ip_per_15min=3, login_max_failures=99)
    for _ in range(3):
        assert login(client, "g@example.test").status_code == 401
    assert login(client, "g@example.test").status_code == 429


# ---------- changes ----------

def test_change_password_revokes_other_sessions(env):
    client, mailer, _, tmp = env
    csrf = logged(client, mailer)
    other = TestClient(client.app, base_url="https://vc.example.test")
    assert login(other).status_code == 200
    assert other.get("/api/auth/me").status_code == 200
    body = {"current_password": "wrong wrong wrong", "new_password": PW2}
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 401
    body["current_password"] = PW
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200  # current session survives
    assert login(TestClient(client.app, base_url="https://vc.example.test"), password=PW).status_code == 401
    assert login(TestClient(client.app, base_url="https://vc.example.test"), password=PW2).status_code == 200
    short = {"current_password": PW2, "new_password": "tiny"}
    assert client.post("/api/auth/password", json=short, headers=hdr(csrf)).status_code == 400


def test_change_email_needs_password_and_new_mailbox_confirmation(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    n = len(mailer.sent)
    body = {"new_email": "New@Example.test", "password": "bad bad bad bad"}
    assert client.post("/api/auth/email", json=body, headers=hdr(csrf)).status_code == 401
    assert len(mailer.sent) == n
    body["password"] = PW
    assert client.post("/api/auth/email", json=body, headers=hdr(csrf)).status_code == 200
    assert mailer.sent[-1][0] == "new@example.test" and "#" in mailer.sent[-1][2]
    token = mailer.token()
    assert client.get("/api/auth/me").json()["email"] == "a@example.test"  # not yet
    assert client.post("/api/auth/verify/preview", json={"token": token}).json()["kind"] == "email_change"
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert login(client, "a@example.test").status_code == 401
    assert login(client, "new@example.test").status_code == 200


def test_change_email_to_taken_address_gives_same_answer_without_mail(env):
    client, mailer, _, _ = env
    verified(client, mailer, "taken@example.test")
    other = TestClient(client.app, base_url="https://vc.example.test")
    csrf = logged(other, mailer)
    n = len(mailer.sent)
    r = other.post("/api/auth/email", json={"new_email": "taken@example.test", "password": PW},
                   headers=hdr(csrf))
    assert r.status_code == 200 and r.json() == {"ok": True} and len(mailer.sent) == n


def test_simbrief_id_validation(tmp_path):
    mailer = Mailer()
    client = make_client(tmp_path, Clock(), mailer, account_user_minute=100)  # many writes in one test
    csrf = logged(client, mailer)
    for bad in ("1" * 41, "a;b", "<script>", "a\nb", "a/b", "caf\u00e9", "x@y", 5, None):
        r = client.post("/api/auth/settings", json={"simbrief_id": bad}, headers=hdr(csrf))
        assert r.status_code == 400
    raw = client.post("/api/auth/settings", content=b'{"simbrief_id": "a\\ud800"}',
                      headers={**hdr(csrf), "content-type": "application/json"})
    assert raw.status_code == 400  # lone surrogate
    r = client.post("/api/auth/settings", json={"simbrief_id": " 424242 "}, headers=hdr(csrf))
    assert r.json()["simbrief_id"] == "424242"
    assert client.get("/api/auth/me").json()["simbrief_id"] == "424242"
    for good in ("Captain_Jo-1.x", "A B", "1", "x" * 40):  # Pilot ID or username
        r = client.post("/api/auth/settings", json={"simbrief_id": good}, headers=hdr(csrf))
        assert r.status_code == 200 and r.json()["simbrief_id"] == good
    client.post("/api/auth/settings", json={"simbrief_id": ""}, headers=hdr(csrf))
    assert client.get("/api/auth/me").json()["simbrief_id"] == ""


def test_register_stores_simbrief_username(env):
    client, mailer, _, _ = env
    assert register(client, simbrief_id=" Pilot.Name_1 ").status_code == 200
    vpost(client, mailer.token())
    assert client.get("/api/auth/me").json()["simbrief_id"] == "Pilot.Name_1"


def test_delete_account_removes_everything(env):
    client, mailer, _, tmp = env
    csrf = logged(client, mailer)
    client.post("/api/auth/email", json={"new_email": "n@example.test", "password": PW},
                headers=hdr(csrf))  # leaves an email token behind
    path = tmp / "t.db"
    assert client.post("/api/auth/delete", json={"password": "bad bad bad bad"},
                       headers=hdr(csrf)).status_code == 401
    assert client.post("/api/auth/delete", json={"password": PW}, headers=hdr(csrf)).status_code == 200
    for table in ("users", "sessions", "email_tokens", "login_failures"):
        assert rows(path, table) == 0
    wal = tmp / "t.db-wal"
    assert b"a@example.test" not in path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")
    assert client.get("/api/auth/me").status_code == 401
    assert login(client).status_code == 401


def test_user_isolation(env):
    client, mailer, _, _ = env
    csrf_a = logged(client, mailer, "a@example.test")
    b = TestClient(client.app, base_url="https://vc.example.test")
    csrf_b = logged(b, mailer, "b@example.test", PW2)
    client.post("/api/auth/settings", json={"simbrief_id": "111"}, headers=hdr(csrf_a))
    b.post("/api/auth/settings", json={"simbrief_id": "222"}, headers=hdr(csrf_b))
    assert client.get("/api/auth/me").json()["simbrief_id"] == "111"
    assert b.get("/api/auth/me").json()["simbrief_id"] == "222"
    # A's CSRF token is useless on B's session and vice versa
    assert b.post("/api/auth/settings", json={"simbrief_id": "9"}, headers=hdr(csrf_a)).status_code == 403
    # deleting B leaves A untouched
    assert b.post("/api/auth/delete", json={"password": PW2}, headers=hdr(csrf_b)).status_code == 200
    assert client.get("/api/auth/me").json()["email"] == "a@example.test"
    # A's password does not open B's address
    assert login(TestClient(client.app, base_url="https://vc.example.test"), "b@example.test").status_code == 401


# ---------- reset ----------

def test_reset_flow_revokes_sessions_and_tokens(env):
    client, mailer, clock, _ = env
    logged(client, mailer)
    n = len(mailer.sent)
    assert client.post("/api/auth/forgot", json={"email": "ghost@example.test"}).status_code == 200
    assert len(mailer.sent) == n  # unknown: same answer, no mail
    r = client.post("/api/auth/forgot", json={"email": "a@example.test"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert BASE + "/reset#" in mailer.sent[-1][2]
    token = mailer.token()
    assert client.post("/api/auth/reset", json={"token": token, "password": "tiny"}).status_code == 400
    assert client.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 200
    assert client.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 400
    assert client.get("/api/auth/me").status_code == 401  # session revoked
    assert login(TestClient(client.app, base_url="https://vc.example.test"), password=PW).status_code == 401
    assert login(TestClient(client.app, base_url="https://vc.example.test"), password=PW2).status_code == 200


def test_reset_token_expires_and_kinds_do_not_mix(env):
    client, mailer, clock, _ = env
    verified(client, mailer)
    verify_like = "y" * 43
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = mailer.token()
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 400  # reset != verify
    assert client.post("/api/auth/reset", json={"token": verify_like, "password": PW2}).status_code == 400
    clock.advance(3601)
    assert client.post("/api/auth/reset", json={"token": token, "password": PW2}).status_code == 400


def test_verify_token_cannot_reset(env):
    client, mailer, _, _ = env
    register(client)
    assert client.post("/api/auth/reset", json={"token": mailer.token(), "password": PW2}).status_code == 400


def test_reset_for_unverified_sends_nothing(env):
    client, mailer, _, _ = env
    register(client)
    n = len(mailer.sent)
    assert client.post("/api/auth/forgot", json={"email": "a@example.test"}).status_code == 200
    assert len(mailer.sent) == n


def test_reset_clears_lockout(env):
    client, mailer, _, _ = env
    verified(client, mailer)
    for _ in range(5):
        login(client, password="bad bad bad bad")
    assert login(client).status_code == 429
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    client.post("/api/auth/reset", json={"token": mailer.token(), "password": PW2})
    assert login(client, password=PW2).status_code == 200


# ---------- logs ----------

def test_nothing_sensitive_in_logs(env, caplog):
    client, mailer, _, _ = env
    caplog.set_level(logging.DEBUG)
    secret_pw = "Sup3r-secret-passphrase"
    mailer.fail = True
    register(client, "private.person@example.test", secret_pw)
    mailer.fail = False
    csrf = logged(client, mailer, "private.person@example.test", secret_pw)
    token = mailer.token()
    login(client, "private.person@example.test", "wrong wrong wrong")
    client.post("/api/auth/forgot", json={"email": "private.person@example.test"})
    client.post("/api/auth/settings", json={"simbrief_id": "987654"}, headers=hdr(csrf))
    client.post("/api/auth/delete", json={"password": secret_pw}, headers=hdr(csrf))
    text = caplog.text
    for needle in ("private.person", secret_pw, "wrong wrong", token, csrf, "987654", "Authorization"):
        assert needle not in text
    assert "mail delivery failed (RuntimeError)" in text


# ---------- fix round 1 ----------

def test_reset_and_verify_ip_limits_and_no_hash_for_bad_token(tmp_path, monkeypatch):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, ip_hourly=3, ip_daily=20)
    calls = []
    real = security._hash
    monkeypatch.setattr(security, "_hash", lambda p: calls.append(1) or real(p))
    for _ in range(3):  # unknown token: 400 and NO argon2 work
        assert client.post("/api/auth/reset", json={"token": "z" * 43, "password": PW2}).status_code == 400
    assert calls == []
    assert client.post("/api/auth/reset", json={"token": "z" * 43, "password": PW2}).status_code == 429
    for _ in range(3):
        assert client.post("/api/auth/verify", json={"token": "z" * 43}).status_code == 400
    assert client.post("/api/auth/verify", json={"token": "z" * 43}).status_code == 429
    # the preview has its own bucket: the exhausted verify one does not block it
    assert client.post("/api/auth/verify/preview", json={"token": "z" * 43}).status_code == 200


def test_preview_bucket_is_separate_and_wider(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, ip_hourly=5, ip_daily=20, preview_ip_hourly=20)
    for _ in range(20):
        assert client.post("/api/auth/verify/preview", json={"token": "z" * 43}).status_code == 200
    assert client.post("/api/auth/verify/preview", json={"token": "z" * 43}).status_code == 429
    for _ in range(5):  # consuming has its own, tighter bucket (5 per hour)
        assert vpost(client, "z" * 43).status_code == 400
    assert vpost(client, "z" * 43).status_code == 429
    clock.advance(3601)
    assert client.post("/api/auth/verify/preview", json={"token": "z" * 43}).status_code == 200


def test_default_preview_limit_is_20_per_hour():
    assert AppConfig().preview_ip_hourly == 20 and AppConfig().ip_hourly == 5

def test_reset_single_use_still_atomic(env):
    client, mailer, _, _ = env
    verified(client, mailer)
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    t = mailer.token()
    assert client.post("/api/auth/reset", json={"token": t, "password": PW2}).status_code == 200
    assert client.post("/api/auth/reset", json={"token": t, "password": PW}).status_code == 400


def test_body_size_limits(env):
    client = env[0]
    h = {"content-type": "application/json"}
    big = b'{"email": "' + b"a" * 20000 + b'"}'
    assert client.post("/api/auth/login", content=big, headers=h).status_code == 413
    assert client.post("/api/auth/login", content=b"{}", headers={**h, "content-length": "abc"}
                       ).status_code == 400
    assert client.post("/api/auth/login", content=b"{}", headers={**h, "content-length": "99999"}
                       ).status_code == 413
    # streamed without trusting the header: a generator body has no Content-Length
    def chunks():
        for _ in range(10):
            yield b"x" * 2000
    assert client.post("/api/auth/login", content=chunks(), headers=h).status_code == 413


def test_lying_content_length_cannot_bypass_cap(env):
    client = env[0]
    # ASGI-level: header says 10 bytes, stream delivers far more
    import asyncio

    async def run():
        app, sent = client.app, []
        body = b"x" * 20000

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            sent.append(msg)

        scope = {"type": "http", "method": "POST", "path": "/api/auth/login", "query_string": b"",
                 "headers": [(b"host", b"vc.example.test"), (b"content-type", b"application/json"),
                     (b"content-length", b"10")],
                 "client": ("203.0.113.1", 1), "server": ("t", 443), "scheme": "https",
                 "http_version": "1.1", "root_path": ""}
        await app(scope, receive, send)
        return sent[0]["status"]

    assert asyncio.run(run()) == 413


def test_surrogates_and_deep_json_never_500(env):
    client = env[0]
    h = {"content-type": "application/json"}
    for body in (b'{"email": "a@example.test", "password": "\\ud800aaaaaaaaaaaa"}',
                 b'{"email": "\\ud800@example.test", "password": "aaaaaaaaaaaa"}',
                 b'{"token": "\\ud800' + b"a" * 30 + b'"}'):
        for path in ("register", "login", "verify", "verify/preview", "forgot", "reset"):
            r = client.post(f"/api/auth/{path}", content=body, headers=h)
            assert r.status_code < 500, (path, r.status_code)  # 200 only for generic answers
            if path in ("login", "reset", "verify"):
                assert r.status_code in (400, 401)
    deep = b"[" * 4000 + b"]" * 4000
    assert len(deep) < 8192
    assert client.post("/api/auth/login", content=deep, headers=h).status_code == 400


def test_suppressed_mail_keeps_previous_link_valid(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, mail_addr_hourly=1)
    register(client)
    link = mailer.token()
    register(client)  # mail suppressed by the per-address cap: old link must survive
    assert len(mailer.sent) == 1
    assert vpost(client, link).status_code == 200


def test_reset_mail_cap_is_separate_from_signup_flood(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, mail_daily=2, mail_addr_hourly=10)
    verified(client, mailer)
    register(client, "b@example.test")
    register(client, "c@example.test")
    n = len(mailer.sent)
    register(client, "d@example.test")
    assert len(mailer.sent) == n  # verify cap exhausted
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    assert len(mailer.sent) == n + 1  # reset still goes out


def test_ipv6_grouped_by_64_and_bad_forwarded_value_ignored(tmp_path):
    clock, mailer = Clock(), Mailer()
    local = make_client(tmp_path, clock, mailer, peer=("127.0.0.1", 1), ip_hourly=2)
    for i, addr in enumerate(("2001:db8:1:2::1", "2001:db8:1:2:ffff::9")):  # same /64
        h = {"X-Forwarded-For": addr}
        assert local.post("/api/auth/register", json={"email": f"v{i}@example.test"},
                          headers=h).status_code == 200
    h = {"X-Forwarded-For": "2001:db8:1:2:aaaa::1"}
    assert local.post("/api/auth/register", json={"email": "v9@example.test"},
                      headers=h).status_code == 429
    h = {"X-Forwarded-For": "2001:db8:1:3::1"}  # another /64
    assert local.post("/api/auth/register", json={"email": "v8@example.test"},
                      headers=h).status_code == 200
    # garbage in the header: falls back to the peer (127.0.0.1), not to a forged key
    from server.api_auth import client_ip

    class Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        headers = {"x-forwarded-for": "1.2.3.4, not-an-ip"}

    assert client_ip(Req()) == "127.0.0.1"


def test_cleanup_removes_expired_rows_and_caps_sessions(env):
    client, mailer, clock, tmp = env
    verified(client, mailer)
    for _ in range(12):
        assert login(TestClient(client.app, base_url="https://vc.example.test")).status_code == 200
    assert rows(tmp / "t.db", "sessions") == 10  # oldest revoked
    register(client, "old@example.test")
    login(client, "ghost@example.test")
    clock.advance(3 * 86400)
    client.post("/api/auth/forgot", json={"email": "nobody@example.test"})  # triggers cleanup
    assert rows(tmp / "t.db", "login_failures") == 0
    assert rows(tmp / "t.db", "email_tokens") == 0  # verify token expired after 24 h
    clock.advance(8 * 86400)
    client.post("/api/auth/forgot", json={"email": "nobody@example.test"})
    assert rows(tmp / "t.db", "sessions") == 0
    assert rows(tmp / "t.db", "email_tokens") == 0
    assert rows(tmp / "t.db", "rate_events") <= 3


def test_password_change_invalidates_pending_reset_tokens(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = mailer.token()
    body = {"current_password": PW, "new_password": PW2}
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 200
    assert client.post("/api/auth/reset", json={"token": token, "password": "yet another one"}
                       ).status_code == 400


def test_migration_idempotent_under_reopen(tmp_path):
    db = Database(str(tmp_path / "m.db"))
    db._prepare()
    other = Database(str(tmp_path / "m.db"))
    other._prepare()
    c = sqlite3.connect(tmp_path / "m.db")
    assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    c.close()


# ---------- C2: purge of unconfirmed sign-ups, security notices ----------

def _exists(path, email):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM users WHERE email=?", (email,)).fetchone()[0] == 1
    finally:
        c.close()


def test_purge_removes_stale_unverified_but_spares_verified_and_live_links(env):
    client, mailer, clock, tmp = env
    verified(client, mailer, "ok@example.test")
    register(client, "old@example.test")          # link expires after 24 h
    clock.advance(6 * 24 * 3600)
    register(client, "fresh@example.test")        # link still alive after the 7 days
    clock.advance(2 * 24 * 3600)                  # old: 8 days, no live link
    client.post("/api/auth/forgot", json={"email": "nobody@example.test"})  # runs the cleanup
    db = tmp / "t.db"
    assert not _exists(db, "old@example.test")
    assert _exists(db, "ok@example.test")
    assert _exists(db, "fresh@example.test")      # younger than 7 days
    clock.advance(30 * 24 * 3600)
    clock.advance(601)
    client.post("/api/auth/forgot", json={"email": "nobody@example.test"})
    assert _exists(db, "ok@example.test")         # verified: never purged
    assert not _exists(db, "fresh@example.test")


def test_password_change_and_reset_send_a_notice_without_token(tmp_path):
    mailer = Mailer()  # per-address cap raised: this flow sends 4 mails to the same address
    client = make_client(tmp_path, Clock(), mailer, mail_addr_hourly=20)
    csrf = logged(client, mailer)
    n = len(mailer.sent)
    body = {"current_password": PW, "new_password": PW2}
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 200
    to, subject, text = mailer.sent[-1]
    assert len(mailer.sent) == n + 1 and to == "a@example.test"
    assert "password was changed" in subject and "#" not in text and "http" not in text
    client.cookies.clear()
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    token = mailer.token()
    assert client.post("/api/auth/reset", json={"token": token, "password": PW}).status_code == 200
    to, subject, text = mailer.sent[-1]
    assert "password was changed" in subject and token not in text and "http" not in text


def test_email_change_notice_goes_to_the_old_address_only_after_confirmation(env):
    client, mailer, _, _ = env
    csrf = logged(client, mailer)
    client.post("/api/auth/email", json={"new_email": "new@example.test", "password": PW},
                headers=hdr(csrf))
    assert mailer.sent[-1][0] == "new@example.test"
    n = len(mailer.sent)
    token = mailer.token()
    assert vpost(client, token).status_code == 200
    to, subject, text = mailer.sent[-1]
    assert len(mailer.sent) == n + 1 and to == "a@example.test"
    assert "email address was changed" in subject and token not in text and "http" not in text


def test_notice_respects_the_other_cap(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, mail_daily=1, mail_addr_hourly=10)
    verified(client, mailer)                      # verify group: separate cap
    csrf = login(client).json()["csrf_token"]
    body = {"current_password": PW, "new_password": PW2}
    n = len(mailer.sent)
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 200
    assert len(mailer.sent) == n + 1
    body = {"current_password": PW2, "new_password": PW}
    assert client.post("/api/auth/password", json=body, headers=hdr(csrf)).status_code == 200
    assert len(mailer.sent) == n + 1              # 'other' daily cap reached: no second notice


def test_reset_of_unverified_account_does_nothing(env):
    client, mailer, _, _ = env
    register(client)
    n = len(mailer.sent)
    client.post("/api/auth/forgot", json={"email": "a@example.test"})
    assert len(mailer.sent) == n
    assert client.post("/api/auth/reset", json={"token": "z" * 43, "password": PW2}).status_code == 400
    assert len(mailer.sent) == n
