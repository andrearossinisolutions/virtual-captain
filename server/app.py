"""Virtual Captain public server: skeleton (step 1).

Serves a placeholder page and a health check. No API, no docs, no state.
It does NOT serve web/ and does NOT import protocol.py yet.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

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

# Known limit: the middleware does not add these headers to unhandled 500 errors
# (Starlette answers those outside the middleware stack); Apache/clients see a bare 500.
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


def read_revision(path: Path | None = None) -> str:
    try:
        rev = (path or REVISION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return rev[:64] or "unknown"


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "index.html", media_type="text/html; charset=utf-8")

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
