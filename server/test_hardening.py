"""C3 hardening tests: headers, 500 handler, Host check, per-user limits, hash queue cap, audit log.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import security  # noqa: E402
from server.app import CSP, create_app  # noqa: E402
from server.test_accounts import (PW, PW2, Clock, Mailer, cfg, env, hdr, logged, login,  # noqa: E402,F401
                                  make_client, register, rows)


def test_cross_origin_headers_and_healthz_on_loopback(env):
    client, _, _, _ = env
    r = client.get("/healthz")
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    lo = TestClient(client.app, base_url="http://127.0.0.1:8000")
    assert lo.get("/healthz").status_code == 200
    assert TestClient(client.app, base_url="http://localhost:8000").get("/healthz").status_code == 200


def test_unknown_host_is_rejected_with_400(env):
    client, _, _, _ = env
    bad = TestClient(client.app, base_url="https://evil.example.test")
    assert bad.get("/healthz").status_code == 400
    assert bad.get("/").status_code == 400


def test_unhandled_error_is_a_generic_500_with_headers(env, caplog):
    client, _, _, _ = env

    @client.app.get("/boom-secret-detail")
    async def boom():
        raise RuntimeError("secret-detail-xyz")

    c = TestClient(client.app, base_url="https://vc.example.test", raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        r = c.get("/boom-secret-detail")
    assert r.status_code == 500 and r.json() == {"error": "server_error"}
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "secret-detail-xyz" not in r.text
    assert "secret-detail-xyz" not in caplog.text


def test_user_limit_429_with_retry_after_and_no_db_writes(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, account_user_minute=3)
    csrf = logged(client, mailer)
    before = rows(tmp_path / "t.db", "rate_events")
    codes = [client.post("/api/auth/settings", json={"simbrief_id": ""}, headers=hdr(csrf))
             for _ in range(4)]
    assert [r.status_code for r in codes] == [200, 200, 200, 429]
    assert codes[-1].headers["retry-after"] == "60"
    assert rows(tmp_path / "t.db", "rate_events") == before


def test_ip_limit_applies_across_users(tmp_path):
    clock, mailer = Clock(), Mailer()
    client = make_client(tmp_path, clock, mailer, account_user_minute=100, account_ip_minute=2)
    csrf = logged(client, mailer)
    codes = [client.post("/api/auth/settings", json={"simbrief_id": ""}, headers=hdr(csrf)).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_hash_queue_cap_answers_503_before_hashing(env):
    client, mailer, _, _ = env
    register(client)
    default = client.app.state.accounts.cfg.hash_queue_max
    try:
        security.set_queue_max(0)
        r = login(client)
    finally:
        security.set_queue_max(default)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert login(client).status_code == 401  # queue free again: the normal answer


def test_audit_log_has_ids_only(env, caplog):
    client, mailer, _, _ = env
    csrf = logged(client, mailer, "audit.me@example.test")
    with caplog.at_level(logging.INFO, logger="virtual_captain.audit"):
        login(client, "audit.me@example.test", "wrong password!")
        for _ in range(5):
            login(client, "audit.me@example.test", "wrong password!")  # reaches the lockout
        client.cookies.clear()
        client.post("/api/auth/forgot", json={"email": "audit.me@example.test"})
        token = mailer.token()
        client.post("/api/auth/reset", json={"token": token, "password": PW2})
        login(client, "audit.me@example.test", PW2)
        csrf2 = login(client, "audit.me@example.test", PW2).json()["csrf_token"]
        client.post("/api/auth/password", json={"current_password": PW2, "new_password": PW},
                    headers=hdr(csrf2))
        client.post("/api/device/mine/revoke-all", json={}, headers=hdr(csrf2))
    text = " ".join(r.getMessage() for r in caplog.records if r.name == "virtual_captain.audit")
    for event in ("login_failed", "lockout", "reset_requested", "reset_done", "password_changed",
                  "device_revoked"):
        assert event in text, event
    for secret in ("audit.me", "example.test", "203.0.113", "testclient", token, PW, PW2):
        assert secret not in text, secret
