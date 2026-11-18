"""JSON API under /api/device/*: device-code login of the PC program (step 2c).
Program endpoints (start, poll, token, revoke) have no cookie; website endpoints (lookup,
approve, deny) need the session, the CSRF token and a same-origin request."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.accounts import Accounts, AccountError
from server.api_auth import _body, _fail, client_ip, current_session, origin_check
from server.devicecode import DeviceCodes, DeviceError

OK = {"ok": True}


def build_device_router(accounts: Accounts, devices: DeviceCodes) -> APIRouter:
    router = APIRouter(prefix="/api/device")
    base = accounts.cfg.public_base_url

    def post(path):
        def deco(fn):
            async def wrapper(request: Request):
                try:
                    return await fn(request)
                except DeviceError as e:
                    body = {"error": e.code}
                    body.update(e.extra)
                    return JSONResponse(body, status_code=e.status)
                except AccountError as e:
                    return _fail(e.status, e.code)
            wrapper.__name__ = fn.__name__
            router.add_api_route(path, wrapper, methods=["POST"], include_in_schema=False)
            return fn
        return deco

    @post("/start")
    async def start(request):
        origin_check(base, request)
        data = await _body(request)
        return JSONResponse(devices.start(client_ip(request),
                                          request.headers.get("user-agent", ""),
                                          data.get("device_name")))

    @post("/poll")
    async def poll(request):
        origin_check(base, request)
        data = await _body(request)
        return JSONResponse(devices.poll(data.get("device_code"), client_ip(request)))

    @post("/token")
    async def token(request):
        origin_check(base, request)
        data = await _body(request)
        if data.get("grant_type") != "refresh_token":
            raise DeviceError(400, "unsupported_grant_type")
        return JSONResponse(devices.refresh(data.get("refresh_token"), client_ip(request)))

    @post("/revoke")
    async def revoke(request):
        origin_check(base, request)
        data = await _body(request)
        devices.revoke(data.get("token"), client_ip(request))
        return JSONResponse(OK)

    @post("/lookup")
    async def lookup(request):
        sess = current_session(accounts, request, True)
        data = await _body(request)
        return JSONResponse(devices.lookup(sess, data.get("user_code"), client_ip(request)))

    @post("/approve")
    async def approve(request):
        sess = current_session(accounts, request, True)
        data = await _body(request)
        devices.decide(sess, data.get("user_code"), client_ip(request), True,
                       data.get("confirm"))
        return JSONResponse(OK)

    @post("/deny")
    async def deny(request):
        sess = current_session(accounts, request, True)
        data = await _body(request)
        devices.decide(sess, data.get("user_code"), client_ip(request), False)
        return JSONResponse(OK)

    return router
