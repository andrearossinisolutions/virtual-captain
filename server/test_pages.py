"""Tests for the account pages (step 2b-2): routes, headers, no inline code, redirects.
Skipped when fastapi/httpx/argon2 are missing (pip install -r server/requirements-dev.txt)."""
from __future__ import annotations

import glob
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")

from fastapi.testclient import TestClient  # noqa: E402

from server import app as srv  # noqa: E402
from server.app import ACCOUNT_PAGES, ACCOUNT_SCRIPTS, CSP, PUBLIC_DIR, create_app  # noqa: E402
from server.config import AppConfig  # noqa: E402

BASE = "https://vc.example.test"
PAGES = ["/", "/register", "/login", "/verify", "/forgot", "/reset", "/account", "/waiting"]


def mounted(tmp_path):
    cfg = AppConfig(public_base_url=BASE, db_path=str(tmp_path / "p.db"), secret_key="k" * 40)
    return TestClient(create_app(cfg, mailer=lambda *a: None), base_url="https://testserver")


def public_files():
    return sorted(p for p in PUBLIC_DIR.iterdir() if p.is_file())


@pytest.mark.parametrize("path", PAGES)
def test_pages_ok_with_security_headers(tmp_path, path):
    r = mounted(tmp_path).get(path)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-security-policy"] == CSP
    assert "default-src 'none'" in CSP and "frame-ancestors 'none'" in CSP
    assert "script-src 'self'" in CSP and "form-action 'self'" in CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-cache"
    assert mounted(tmp_path).head(path).status_code == 200


@pytest.mark.parametrize("path", sorted(ACCOUNT_SCRIPTS))
def test_scripts_served_as_javascript(tmp_path, path):
    r = mounted(tmp_path).get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["content-security-policy"] == CSP


def test_every_html_references_only_existing_whitelisted_files(tmp_path):
    client = mounted(tmp_path)
    for f in public_files():
        if f.suffix != ".html":
            continue
        for src in re.findall(r'(?:src|href)="(/[^"#?]*)"', f.read_text(encoding="utf-8")):
            ok = src == "/style.css" or src in ACCOUNT_SCRIPTS or src in ACCOUNT_PAGES
            assert ok, (f.name, src)
            assert client.get(src).status_code == 200


def test_whitelist_covers_public_dir_and_nothing_else(tmp_path):
    served = set(ACCOUNT_PAGES.values()) | set(ACCOUNT_SCRIPTS.values()) | {"style.css", "index.html"}
    assert {f.name for f in public_files()} == served
    client = mounted(tmp_path)
    for path in ("/index.html", "/home.html", "/register.html", "/account/x",
                 "/public/login.html", "/../server/app.py", "/app.js", "/app.css", "/docs",
                 "/openapi.json", "/redoc", "/waiting.js.map"):
        assert client.get(path).status_code == 404, path


def test_pages_not_mounted_without_api(tmp_path):
    client = TestClient(create_app(AppConfig(db_path=str(tmp_path / "x.db"))))
    assert "coming soon" in client.get("/").text
    assert 'href="/login"' not in client.get("/").text
    for path in PAGES[1:] + sorted(ACCOUNT_SCRIPTS):
        assert client.get(path).status_code == 404, path
    assert client.get("/style.css").status_code == 200
    assert not (tmp_path / "x.db").exists()


def test_home_links_only_when_mounted(tmp_path):
    text = mounted(tmp_path).get("/").text
    assert 'href="/login"' in text and 'href="/register"' in text


def test_protected_pages_check_session_and_redirect_to_login():
    for name, needs in (("account.js", True), ("waiting.js", True),
                        ("login.js", False), ("register.js", False)):
        js = (PUBLIC_DIR / name).read_text(encoding="utf-8")
        assert ("requireSession" in js) is needs
        assert ("redirectIfSignedIn" in js) is (not needs)
    common = (PUBLIC_DIR / "common.js").read_text(encoding="utf-8")
    assert '/api/auth/me' in common and 'go("/login")' in common and 'go("/waiting")' in common
    assert "X-CSRF-Token" in common


def test_me_without_session_is_401_so_pages_redirect(tmp_path):
    assert mounted(tmp_path).get("/api/auth/me").status_code == 401


# ---------- static scans of server/public ----------

def test_no_inline_script_style_or_handlers():
    for f in public_files():
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".html":
            for tag in re.findall(r"<script\b[^>]*>", text, re.I):
                assert re.search(r'\bsrc="/[^"]+"', tag), (f.name, tag)
            for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.I | re.S):
                assert m.group(1).strip() == "", f.name
            assert not re.search(r"<style\b", text, re.I), f.name
            assert not re.search(r"\sstyle\s*=", text, re.I), f.name
            assert not re.search(r"\son[a-z]+\s*=", text, re.I), f.name
            assert "javascript:" not in text.lower(), f.name
            assert "http://" not in text and "https://" not in text, f.name  # no external resource
        if f.suffix == ".css":
            assert "@import" not in text and "url(" not in text, f.name


def test_forms_post_so_a_failed_script_never_puts_data_in_the_url():
    for f in public_files():
        if f.suffix != ".html":
            continue
        for tag in re.findall(r"<form\b[^>]*>", f.read_text(encoding="utf-8")):
            assert 'method="post"' in tag and "action=" not in tag, (f.name, tag)


def test_js_never_uses_innerhtml_or_dynamic_code():
    for f in public_files():
        if f.suffix != ".js":
            continue
        text = f.read_text(encoding="utf-8")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                    "new Function", "localStorage", "sessionStorage", "document.cookie"):
            assert bad not in text, (f.name, bad)
        assert "http://" not in text and "https://" not in text, f.name


def test_token_never_in_query_string_links_or_logs():
    for f in public_files():
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"[?&]token=", text), f.name
        assert "location.search" not in text and "console." not in text, f.name
        if f.suffix == ".html":
            assert not re.search(r'href="[^"]*[?#]', text), f.name
    for name in ("verify.js", "reset.js"):
        js = (PUBLIC_DIR / name).read_text(encoding="utf-8")
        assert "takeFragmentToken" in js
    common = (PUBLIC_DIR / "common.js").read_text(encoding="utf-8")
    assert "window.location.hash" in common and "history.replaceState" in common
    # the token travels only in the JSON body of a POST
    assert 'api("/api/auth/verify/preview", { token: token })' in (PUBLIC_DIR / "verify.js").read_text()


def test_waiting_page_shows_no_flight_data_and_start_is_disabled():
    text = (PUBLIC_DIR / "waiting.html").read_text(encoding="utf-8")
    assert "Program not connected" in text
    assert re.search(r'<button[^>]*id="start"[^>]*\bdisabled\b', text)
    assert "Start flight" in text


def test_simbrief_label_everywhere():
    for name in ("register.html", "account.html"):
        assert "SimBrief Pilot ID or username" in (PUBLIC_DIR / name).read_text(encoding="utf-8")


def test_pages_and_scripts_match_the_module_tables():
    assert srv.ACCOUNT_PAGES["/account"] == "account.html"
    assert all((PUBLIC_DIR / n).is_file() for n in
               list(ACCOUNT_PAGES.values()) + list(ACCOUNT_SCRIPTS.values()))
    assert not Path(PUBLIC_DIR / "app.js").exists() and not (PUBLIC_DIR / "app.css").exists()


# ---------- redirects, run for real in node (skipped when node is missing) ----------

NODE = shutil.which("node") or next(iter(sorted(glob.glob(str(Path.home() / ".nvm/versions/node/*/bin/node")))), None)

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [common, me, out] = [process.argv[2], Number(process.argv[3]), process.argv[4]];
const replaced = [];
const window = { location: { hash: "", pathname: "/x", replace: (p) => replaced.push(p) },
                 history: { replaceState() {} } };
const ctx = vm.createContext({ window, document: {}, Promise,
  fetch: () => Promise.resolve({ ok: me === 200, status: me,
    json: () => Promise.resolve(me === 200 ? { email: "a@b.c", csrf_token: "t" } : { error: "unauthenticated" }) }) });
vm.runInContext(fs.readFileSync(common, "utf8"), ctx);
const VC = vm.runInContext("VC", ctx);
const mode = process.argv[5];
(mode === "protected" ? VC.requireSession() : VC.redirectIfSignedIn())
  .then(() => { console.log(JSON.stringify(replaced)); });
// requireSession never resolves on 401: print after the redirect has been requested
setTimeout(() => { console.log(JSON.stringify(replaced)); process.exit(0); }, 200);
"""


def run_harness(tmp_path, me_status, mode):
    script = tmp_path / "h.js"
    script.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run([NODE, str(script), str(PUBLIC_DIR / "common.js"), str(me_status), "x", mode],
                         capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stderr
    return res.stdout.strip().splitlines()[-1]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_redirects_for_real_in_node(tmp_path):
    assert run_harness(tmp_path, 401, "protected") == '["/login"]'   # /account, /waiting without session
    assert run_harness(tmp_path, 200, "protected") == "[]"           # with a session they stay
    assert run_harness(tmp_path, 200, "guest") == '["/waiting"]'     # /login, /register when signed in
    assert run_harness(tmp_path, 401, "guest") == "[]"
