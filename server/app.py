"""Virtual Captain public server: skeleton, account API (2b-1) and account pages (2b-2).

Placeholder page, health check, /api/auth/*, /api/device/*, /api/agent/status, /api/flight/state, WS /ws/agent and, with the API, the pages in public/
plus web/app.js and web/app.css (whitelist). No docs. It does NOT import protocol.py. Nothing touches the DB until first use.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware

from server import security
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
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-cache",
}

# Pages and scripts of the account area: whitelist, served only when the API is mounted.
ACCOUNT_PAGES = {
    "/": "home.html", "/register": "register.html", "/login": "login.html",
    "/verify": "verify.html", "/forgot": "forgot.html", "/reset": "reset.html",
    "/account": "account.html", "/waiting": "waiting.html", "/link": "link.html", "/flight": "flight.html",
}
ACCOUNT_SCRIPTS = {
    "/common.js": "common.js", "/register.js": "register.js", "/login.js": "login.js",
    "/verify.js": "verify.js", "/forgot.js": "forgot.js", "/reset.js": "reset.js",
    "/account.js": "account.js", "/waiting.js": "waiting.js",
    "/link.js": "link.js",
}
# The flight page reuses the program's own web/app.js, web/app.css (and common.js from public/): whitelist, no traversal.
WEB_DIR = BASE_DIR.parent / "web"
WEB_FILES = {"/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8")}

# Unhandled errors are answered outside the middleware stack: the 500 handler adds the headers itself.
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


NO_STORE_PREFIXES = ("/api/auth/", "/api/device/", "/api/agent/", "/api/flight/")


class SecurityHeadersMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware: it would wrap the body of the long SSE streams): adds the security headers to
    every HTTP response at `http.response.start`, and Cache-Control: no-store on the API paths."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        no_store = scope.get("path", "").startswith(NO_STORE_PREFIXES)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if no_store:
                    headers["Cache-Control"] = "no-store"
            await send(message)
        await self.app(scope, receive, send_with_headers)


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
    security.set_queue_max(config.hash_queue_max)
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
        app.state.accounts = accounts
        from server.api_agent import build_agent_router
        from server.hub import Hub
        hub = Hub(stale_after=config.agent_stale_after)  # in RAM: --workers 1 is mandatory
        app.state.hub = hub
        from server import wire

        async def close_program(user_id: int) -> None:  # password change, reset, deletion: no 10 s wait
            await hub.close_user(user_id, wire.CLOSE_REVOKED)
        from server.api_stream import StreamRegistry, build_stream_router
        streams = StreamRegistry()
        app.state.streams = streams

        async def close_program_and_streams(user_id: int) -> None:
            streams.close_user(user_id)   # the browser streams too: no waiting for the 60 s re-check
            await close_program(user_id)
        accounts.on_revoke = close_program_and_streams
        accounts.on_logout = streams.close_session
        app.include_router(build_router(accounts))
        app.include_router(build_device_router(accounts, devices, hub))
        app.include_router(build_agent_router(accounts, devices, hub))
        if config.stream:
            app.include_router(build_stream_router(accounts, hub, streams))

    if mounted:  # Host must be the public host (or the local health check of deploy.sh)
        host = urlparse(config.public_base_url).hostname
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "127.0.0.1", "localhost"])

    @app.exception_handler(security.HashBusy)
    async def hash_busy(request: Request, exc: security.HashBusy) -> JSONResponse:
        return JSONResponse({"error": "busy"}, status_code=503, headers={"Retry-After": "5"})

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error (%s)", type(exc).__name__)  # never the message or the request
        return JSONResponse({"error": "server_error"}, status_code=500,
                            headers={**SECURITY_HEADERS, "Cache-Control": "no-store"})

    app.add_middleware(SecurityHeadersMiddleware)

    def add_file(path: str, name: str, media_type: str, directory: Path = PUBLIC_DIR) -> None:
        async def serve() -> FileResponse:
            return FileResponse(directory / name, media_type=media_type)
        app.add_api_route(path, serve, methods=["GET", "HEAD"], include_in_schema=False)

    # "/" is the placeholder unless the API is mounted: then the home page links to the account pages
    add_file("/", "home.html" if mounted else "index.html", "text/html; charset=utf-8")
    if mounted:
        for path, name in ACCOUNT_PAGES.items():
            if path != "/":
                add_file(path, name, "text/html; charset=utf-8")
        for path, name in ACCOUNT_SCRIPTS.items():
            add_file(path, name, "text/javascript; charset=utf-8")
        for path, (name, media_type) in WEB_FILES.items():
            add_file(path, name, media_type, WEB_DIR)

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
