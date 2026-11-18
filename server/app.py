"""Virtual Captain public server: skeleton, account API (2b-1) and account pages (2b-2).

Placeholder page, health check, /api/auth/*, /api/device/*, /api/agent/status, WS /ws/agent and, with the API, the pages in public/.
No docs. It does NOT serve web/ and does NOT import protocol.py yet. Nothing touches the DB until first use.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from server.config import AppConfig, check_secret, load_app_config, load_smtp_config

log = logging.getLogger("virtual_captain")
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
# deploy.sh writes the commit sha here (current/REVISION, next to server/)
REVISION_FILE = Path(os.environ.get("VC_REVISION_FILE", str(BASE_DIR.parent / "REVISION")))

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-cache",
}

# Pages and scripts of the account area: whitelist, served only when the API is mounted.
ACCOUNT_PAGES = {
    "/": "home.html", "/register": "register.html", "/login": "login.html",
    "/verify": "verify.html", "/forgot": "forgot.html", "/reset": "reset.html",
    "/account": "account.html", "/waiting": "waiting.html", "/link": "link.html",
}
ACCOUNT_SCRIPTS = {
    "/common.js": "common.js", "/register.js": "register.js", "/login.js": "login.js",
    "/verify.js": "verify.js", "/forgot.js": "forgot.js", "/reset.js": "reset.js",
    "/account.js": "account.js", "/waiting.js": "waiting.js",
    "/link.js": "link.js",
}

# Known limit: the middleware does not add these headers to unhandled 500 errors
# (Starlette answers those outside the middleware stack); Apache/clients see a bare 500.
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def read_revision(path: Path | None = None) -> str:
    try:
        rev = (path or REVISION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return rev[:64] or "unknown"


def smtp_mailer(env=os.environ) -> Callable[[str, str, str], None]:
    """Default mailer: reads the SMTP settings when a mail is sent."""
    def send(to: str, subject: str, body: str) -> None:
        from server.mail import send_mail
        send_mail(load_smtp_config(env), to, subject, body)
    return send


def create_app(config: Optional[AppConfig] = None, *, db=None, mailer=None,
               clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    config = config if config is not None else load_app_config()
    check_secret(config.public_base_url, config.secret_key)
    mounted = bool(config.public_base_url)
    if not mounted:
        log.warning("PUBLIC_BASE_URL is not set: the account API /api/auth/* is NOT mounted")
    else:  # without it the account API stays off (no safe link/Origin)
        from server.accounts import Accounts
        from server.api_auth import build_router
        from server.db import Database
        accounts = Accounts(config, db or Database(config.db_path), mailer or smtp_mailer(), clock)
        from server.api_device import build_device_router
        from server.devicecode import DeviceCodes
        devices = DeviceCodes(accounts)
        app.state.devices = devices
        from server.api_agent import build_agent_router
        from server.hub import Hub
        hub = Hub(stale_after=config.agent_stale_after)  # in RAM: --workers 1 is mandatory
        app.state.hub = hub
        app.include_router(build_router(accounts))
        app.include_router(build_device_router(accounts, devices))
        app.include_router(build_agent_router(accounts, devices, hub))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith(("/api/auth/", "/api/device/", "/api/agent/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def add_file(path: str, name: str, media_type: str) -> None:
        async def serve() -> FileResponse:
            return FileResponse(PUBLIC_DIR / name, media_type=media_type)
        app.add_api_route(path, serve, methods=["GET", "HEAD"], include_in_schema=False)

    # "/" is the placeholder unless the API is mounted: then the home page links to the account pages
    add_file("/", "home.html" if mounted else "index.html", "text/html; charset=utf-8")
    if mounted:
        for path, name in ACCOUNT_PAGES.items():
            if path != "/":
                add_file(path, name, "text/html; charset=utf-8")
        for path, name in ACCOUNT_SCRIPTS.items():
            add_file(path, name, "text/javascript; charset=utf-8")

    @app.api_route("/style.css", methods=["GET", "HEAD"], include_in_schema=False)
    async def style() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "style.css", media_type="text/css; charset=utf-8")

    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    async def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS_TXT)

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "rev": read_revision()})

    return app


app = create_app()
