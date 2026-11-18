"""JSON API under /api/auth/*. Generic errors, CSRF + Origin checks, __Host-session cookie."""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from server.accounts import SESSION_ABSOLUTE, AccountError, Accounts
from server.ratelimit import group_ip, is_ip
from server.security import same

COOKIE = "__Host-session"
LOOPBACK = ("127.0.0.1", "::1")
MAX_BODY = 8192

OK = {"ok": True}


def client_ip_from(peer: str, headers) -> str:
    """X-Forwarded-For is trusted only when the connection comes from the local Apache,
    and only its LAST value (the one Apache appended; earlier ones are client-controlled).
    A value that is not an IP address is ignored. IPv6 is grouped by /64."""
    if peer in LOOPBACK:
        parts = [p.strip() for p in headers.get("x-forwarded-for", "").split(",")]
        parts = [p for p in parts if p]
        if parts and is_ip(parts[-1]):
            return group_ip(parts[-1])
    return group_ip(peer) if peer else "unknown"


def client_ip(request: Request) -> str:
    return client_ip_from(request.client.host if request.client else "", request.headers)


def _fail(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def _body(request: Request) -> dict:
    if (request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "application/json"):
        raise AccountError(400, "invalid_request")
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise AccountError(400, "invalid_request")
        if int(declared) > MAX_BODY:
            raise AccountError(413, "too_large")
    raw = bytearray()
    async for chunk in request.stream():  # never read more than the cap, whatever the header says
        raw += chunk
        if len(raw) > MAX_BODY:
            raise AccountError(413, "too_large")
    try:
        data = json.loads(bytes(raw) or b"{}")
    except (ValueError, RecursionError):
        raise AccountError(400, "invalid_request") from None
    if not isinstance(data, dict):
        raise AccountError(400, "invalid_request")
    return data


def origin_check(base: str, request: Request) -> None:
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        raise AccountError(403, "forbidden")
    origin = request.headers.get("origin")
    if origin is not None and origin != base:
        raise AccountError(403, "forbidden")


def current_session(accounts: Accounts, request: Request, need_csrf: bool) -> dict:
    sess = accounts.session(request.cookies.get(COOKIE))
    if sess is None:
        raise AccountError(401, "unauthenticated")
    if need_csrf:
        origin_check(accounts.cfg.public_base_url, request)
        sent = request.headers.get("x-csrf-token") or ""
        if not sent or not same(sent, sess["csrf"]):
            raise AccountError(403, "forbidden")
    return sess


def build_router(accounts: Accounts) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    base = accounts.cfg.public_base_url

    def check_origin(request: Request) -> None:
        origin_check(base, request)

    def current(request: Request, need_csrf: bool) -> dict:
        return current_session(accounts, request, need_csrf)

    def set_cookie(resp: JSONResponse, token: str) -> None:
        resp.set_cookie(COOKIE, token, max_age=int(SESSION_ABSOLUTE), path="/",
                        secure=True, httponly=True, samesite="lax")

    def handler(fn):
        """Wrap a route: AccountError -> generic JSON error."""
        async def wrapper(request: Request, background: BackgroundTasks):
            try:
                return await fn(request, background)
            except AccountError as e:
                return _fail(e.status, e.code)
        wrapper.__name__ = fn.__name__
        return wrapper

    def post(path):
        def deco(fn):
            router.add_api_route(path, handler(fn), methods=["POST"], include_in_schema=False)
            return fn
        return deco

    def queue(background: BackgroundTasks, mail) -> None:
        if mail is not None:
            background.add_task(accounts.deliver, mail)

    @post("/register")
    async def register(request, background):
        check_origin(request)
        data = await _body(request)
        queue(background, await accounts.register(data.get("email"), data.get("simbrief_id"),
                                                  client_ip(request)))
        return JSONResponse(OK)

    @post("/verify/preview")  # token in the body, never in a query string (it would reach the logs)
    async def verify_preview(request, background):
        check_origin(request)
        data = await _body(request)
        return JSONResponse(accounts.verify_preview(data.get("token"), client_ip(request)))

    @post("/verify")
    async def verify(request, background):
        check_origin(request)
        data = await _body(request)
        kind, session = await accounts.verify(data.get("token"), data.get("password"),
                                              client_ip(request))
        if session is None:  # email change: every session was closed, log in again
            return JSONResponse({"ok": True, "kind": kind})
        resp = JSONResponse({"ok": True, "kind": kind, "csrf_token": session[1]})
        set_cookie(resp, session[0])
        return resp

    @post("/login")
    async def login(request, background):
        check_origin(request)
        data = await _body(request)
        token, csrf = await accounts.login(data.get("email"), data.get("password"),
                                           client_ip(request))
        resp = JSONResponse({"ok": True, "csrf_token": csrf})
        set_cookie(resp, token)
        return resp

    @post("/logout")
    async def logout(request, background):
        accounts.logout(current(request, True))
        resp = JSONResponse(OK)
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    @post("/forgot")
    async def forgot(request, background):
        check_origin(request)
        data = await _body(request)
        queue(background, accounts.request_reset(data.get("email"), client_ip(request)))
        return JSONResponse(OK)

    @post("/reset")
    async def reset(request, background):
        check_origin(request)
        data = await _body(request)
        await accounts.reset_password(data.get("token"), data.get("password"),
                                      client_ip(request))
        return JSONResponse(OK)

    @router.get("/me", include_in_schema=False)
    async def me(request: Request):
        try:
            sess = current(request, False)
        except AccountError as e:
            return _fail(e.status, e.code)
        return JSONResponse({"email": sess["email"], "verified": sess["verified"],
                             "simbrief_id": sess["simbrief_id"], "csrf_token": sess["csrf"]})

    @post("/settings")
    async def settings(request, background):
        sess = current(request, True)
        data = await _body(request)
        return JSONResponse({"ok": True, "simbrief_id":
                             accounts.set_simbrief_id(sess, data.get("simbrief_id"))})

    @post("/password")
    async def password(request, background):
        sess = current(request, True)
        data = await _body(request)
        await accounts.change_password(sess, data.get("current_password"),
                                       data.get("new_password"))
        return JSONResponse(OK)

    @post("/email")
    async def email(request, background):
        sess = current(request, True)
        data = await _body(request)
        queue(background, await accounts.change_email(sess, data.get("new_email"),
                                                      data.get("password")))
        return JSONResponse(OK)

    @post("/delete")
    async def delete(request, background):
        sess = current(request, True)
        data = await _body(request)
        await accounts.delete_account(sess, data.get("password"))
        resp = JSONResponse(OK)
        resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    return router
