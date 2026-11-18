"""Tests for the public server skeleton. Skipped when fastapi/httpx are missing
(the PC suite does not need them): pip install -r server/requirements-dev.txt"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from server import app as srv  # noqa: E402


def test_healthz_reports_revision(tmp_path):
    rev = tmp_path / "REVISION"
    rev.write_text("abc123\n", encoding="utf-8")
    assert srv.read_revision(rev) == "abc123"
    assert srv.read_revision(tmp_path / "missing") == "unknown"


def test_healthz_endpoint(tmp_path, monkeypatch):
    rev = tmp_path / "REVISION"
    rev.write_text("deadbeef\n", encoding="utf-8")
    monkeypatch.setattr(srv, "REVISION_FILE", rev)
    r = TestClient(srv.create_app()).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "rev": "deadbeef"}


def test_placeholder_and_style():
    c = TestClient(srv.create_app())
    r = c.get("/")
    assert r.status_code == 200 and "coming soon" in r.text
    assert "<script" not in r.text and "style=" not in r.text and "<style" not in r.text
    assert c.get("/style.css").status_code == 200
    assert "Disallow: /" in c.get("/robots.txt").text


def test_security_headers_everywhere():
    c = TestClient(srv.create_app())
    for path in ("/", "/style.css", "/healthz", "/nope"):
        h = c.get(path).headers
        assert "default-src 'none'" in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
        assert "noindex" in h["x-robots-tag"]
        assert h["cache-control"] == "no-cache"
        assert "permissions-policy" in h


@pytest.mark.parametrize("path", [
    "/docs", "/redoc", "/openapi.json", "/api/state", "/api/", "/app.js", "/app.css",
    "/server/app.py", "/app.py", "/../server/app.py", "/public/index.html", "/.env",
])
def test_not_exposed(path):
    assert TestClient(srv.create_app()).get(path).status_code == 404


def test_no_api_routes_registered():
    paths = {getattr(r, "path", "") for r in srv.create_app().routes}
    assert not any(p.startswith("/api") for p in paths)
    assert paths >= {"/", "/style.css", "/robots.txt", "/healthz"}


def test_head_has_status_and_headers():
    c = TestClient(srv.create_app())
    for path in ("/", "/style.css", "/robots.txt", "/healthz"):
        r = c.head(path)
        assert r.status_code == 200
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"


def test_post_is_405_with_headers():
    r = TestClient(srv.create_app()).post("/")
    assert r.status_code == 405
    assert r.headers["x-content-type-options"] == "nosniff"
