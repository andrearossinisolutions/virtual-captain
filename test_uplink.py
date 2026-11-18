"""Uplink tests: fake http and fake WebSocket, injected clock and waits, no network (one loopback
test for the websockets adapter). Logic runs synchronously through Uplink.run(); a few tests use
the real thread to check that it never blocks the caller."""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
import urllib.error
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import pytest

import protocol
import uplink
from secure_store import PlainProtector, TokenStore
from uplink import (Backoff, ConnClosed, ConnError, ConnRejected, InstanceLock, NetworkError, Uplink,
                    check_base_url, device_login, ws_url)

BASE = "https://vc.example.test"
R0, A0 = "REFRESH0" * 5, "ACCESS00" * 5
R1, A1 = "REFRESH1" * 5, "ACCESS11" * 5


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class FakeHttp:
    """script: {path: [(status, dict) | Exception, ...]} consumed in order; the last one repeats."""

    def __init__(self, script):
        self.script = {k: deque(v) for k, v in script.items()}
        self.calls = []

    def post_json(self, url, body, timeout=None):
        path = urlparse(url).path
        self.calls.append((path, body))
        q = self.script[path]
        item = q.popleft() if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        return item

    def paths(self):
        return [c[0] for c in self.calls]


class FakeWs:
    def __init__(self, clock, script, tick=True):
        self.clock, self.script, self.sent, self.closed, self.tick = clock, deque(script), [], [], tick

    def recv(self, timeout):
        if self.tick:
            self.clock.advance(timeout)
        if not self.script:
            raise ConnClosed(1006)
        item = self.script.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, text):
        self.sent.append(json.loads(text))

    def close(self, code=1000):
        self.closed.append(code)


WELCOME = json.dumps({"t": "welcome", "v": 1, "heartbeat": 5, "max_msg": 262144})
PONG = json.dumps({"t": "pong"})


def start_reply(interval=5, expires=600, code="ABCD-EFGH"):
    return (200, {"device_code": "dc" * 20, "user_code": code, "verification_uri": BASE + "/link",
                  "verification_uri_complete": BASE + "/link#" + code, "expires_in": expires,
                  "interval": interval})


def token_reply(access=A1, refresh=R1, ttl=600):
    return (200, {"access_token": access, "refresh_token": refresh, "expires_in": ttl, "token_type": "Bearer"})


PENDING = (400, {"error": "authorization_pending", "interval": 5})


class Rig:
    """An Uplink with everything injected; `waits` records the pauses (and advances the clock)."""

    def __init__(self, tmp_path, http=None, ws_factory=None, tokens=None, wait_limit=50, force_link=False, **kw):
        self.clock, self.msgs, self.waits, self.shown, self.states = Clock(), [], [], [], []
        self.store = TokenStore(tmp_path / "vc", PlainProtector(), warn=lambda m: None)
        if tokens is not None:
            self.store.save(tokens)
        self.http, self.connects, self.wait_limit, self.ws_factory = http, [], wait_limit, ws_factory
        self.up = Uplink(BASE, self.store, http=http, connect=self._connect, say=self.msgs.append,
                         show_login=self.shown.append, wait=self._wait, wall=self.clock, mono=self.clock,
                         rng=lambda: 0.5, force_link=force_link, **kw)
        real_set = self.up._set
        self.up._set = lambda state, message="": (self.states.append(state), real_set(state, message))[1]

    def _wait(self, seconds):
        self.waits.append(seconds)
        self.clock.advance(seconds)
        if len(self.waits) >= self.wait_limit:
            self.up.request_stop()
        return self.up.stopping()

    def _connect(self, url, token):
        self.connects.append((url, token, self.store.load()))
        item = self.ws_factory(len(self.connects)) if callable(self.ws_factory) else self.ws_factory
        if isinstance(item, Exception):
            raise item
        return item

    def run(self):
        self.up.run()
        return self

    def say_text(self):
        return "\n".join(self.msgs)


def good_tokens(clock_now=1_000_000.0, ttl=600, base=BASE):
    return {"refresh": R0, "access": A0, "access_exp": clock_now + ttl, "base_url": base}


# ---------------------------------------------------------------- address

@pytest.mark.parametrize("url", ["http://vc.example.test", "http://10.0.0.1:8100", "ftp://vc.example.test",
                                 "https://vc.example.test/path", "https://u:p@vc.example.test", "https://",
                                 "vc.example.test", "https://vc.example.test?x=1", "http://127.0.0.1.evil.test"])
def test_check_base_url_rejects(url):
    with pytest.raises(ValueError):
        check_base_url(url)


def test_check_base_url_accepts_and_normalises():
    assert check_base_url("https://vc.example.test/") == "https://vc.example.test"
    assert check_base_url("http://127.0.0.1:8100") == "http://127.0.0.1:8100"
    assert check_base_url("http://localhost:8100/") == "http://localhost:8100"
    assert ws_url("https://vc.example.test") == "wss://vc.example.test/ws/agent"
    assert ws_url("http://127.0.0.1:8100") == "ws://127.0.0.1:8100/ws/agent"
    assert "?" not in ws_url(BASE)  # the token never goes in the query string


def test_http_url_refused_by_uplink(tmp_path):
    store = TokenStore(tmp_path, PlainProtector(), warn=lambda m: None)
    with pytest.raises(ValueError):
        Uplink("http://vc.example.test", store)
    Uplink("http://127.0.0.1:9", store)


def test_default_server_url_is_https_and_public():
    assert uplink.DEFAULT_SERVER_URL.startswith("https://")
    assert check_base_url(uplink.DEFAULT_SERVER_URL) == uplink.DEFAULT_SERVER_URL


# ---------------------------------------------------------------- backoff

def test_backoff_sequence_cap_and_jitter():
    b = Backoff(rng=lambda: 0.5)
    assert [b.failure() for _ in range(9)] == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    lo, hi = Backoff(rng=lambda: 0.0), Backoff(rng=lambda: 1.0)
    assert lo.failure() == pytest.approx(0.8) and hi.failure() == pytest.approx(1.2)
    assert [round(lo.failure(), 2) for _ in range(3)] == [1.6, 3.2, 6.4]
    import random
    r = Backoff(rng=random.Random(1).random)
    for n in range(40):
        d = r.failure()
        base = min(60, 2 ** n)
        assert base * 0.8 - 1e-9 <= d <= base * 1.2 + 1e-9


def test_backoff_resets_only_after_a_stable_connection():
    b = Backoff(rng=lambda: 0.5)
    b.failure(), b.failure(), b.failure()
    b.session_ended(29.9)
    assert b.failure() == 8
    b.session_ended(30)
    assert b.failure() == 1


# ---------------------------------------------------------------- device login

def make_login(http, clock=None, limit=100):
    clock = clock or Clock()
    waits, shown = [], []

    def wait(s):
        waits.append(s)
        clock.advance(s)
        return len(waits) >= limit
    return clock, waits, shown, lambda: device_login(http, BASE, shown.append, wait, clock)


def test_login_pending_slow_down_then_approved():
    http = FakeHttp({"/api/device/start": [start_reply()],
                     "/api/device/poll": [PENDING, (400, {"error": "slow_down", "interval": 10}), token_reply()]})
    clock, waits, shown, run = make_login(http)
    kind, tokens = run()
    assert kind == "ok"
    assert waits == [5, 5, 10]  # interval respected, slow_down raises it
    assert tokens == {"refresh": R1, "access": A1, "access_exp": clock.now + 600, "base_url": BASE}
    assert shown[0]["user_code"] == "ABCD-EFGH" and shown[0]["verification_uri_complete"].endswith("#ABCD-EFGH")
    assert http.calls[0] == ("/api/device/start", {})
    assert [c[0] for c in http.calls].count("/api/device/poll") == 3
    assert http.calls[1][1] == {"device_code": "dc" * 20}


def test_login_slow_down_without_interval_adds_five():
    http = FakeHttp({"/api/device/start": [start_reply(interval=5)],
                     "/api/device/poll": [(400, {"error": "slow_down"}), token_reply()]})
    _, waits, _, run = make_login(http)
    assert run()[0] == "ok" and waits == [5, 10]


def test_login_expired_token_restarts_with_a_new_code_up_to_three_times():
    http = FakeHttp({"/api/device/start": [start_reply(code="AAAA-AAAA"), start_reply(code="BBBB-BBBB"),
                                           start_reply(code="CCCC-CCCC"), start_reply(code="DDDD-DDDD")],
                     "/api/device/poll": [(400, {"error": "expired_token"})]})
    _, _, shown, run = make_login(http)
    assert run() == ("expired", None)
    assert [s["user_code"] for s in shown] == ["AAAA-AAAA", "BBBB-BBBB", "CCCC-CCCC", "DDDD-DDDD"]  # 1 + 3 restarts
    assert http.paths().count("/api/device/start") == 4


def test_login_restart_then_success():
    http = FakeHttp({"/api/device/start": [start_reply(code="AAAA-AAAA"), start_reply(code="BBBB-BBBB")],
                     "/api/device/poll": [(400, {"error": "expired_token"}), token_reply()]})
    _, _, shown, run = make_login(http)
    assert run()[0] == "ok" and len(shown) == 2


def test_login_local_deadline_also_restarts():
    http = FakeHttp({"/api/device/start": [start_reply(expires=12, code="AAAA-AAAA"), start_reply(code="BBBB-BBBB")],
                     "/api/device/poll": [PENDING, PENDING, token_reply()]})
    _, waits, shown, run = make_login(http)
    assert run()[0] == "ok"
    assert len(shown) == 2 and waits[:3] == [5, 5, 5]  # third wait passes the 12 s: no poll, new code


def test_login_access_denied_stops():
    http = FakeHttp({"/api/device/start": [start_reply()],
                     "/api/device/poll": [PENDING, (400, {"error": "access_denied"}), token_reply()]})
    _, waits, _, run = make_login(http)
    assert run() == ("denied", None)
    assert http.paths().count("/api/device/poll") == 2  # no polling after the refusal


def test_login_network_errors():
    http = FakeHttp({"/api/device/start": [NetworkError("x")]})
    assert make_login(http)[3]() == ("network", None)
    http = FakeHttp({"/api/device/start": [(500, {})]})
    assert make_login(http)[3]() == ("network", None)
    http = FakeHttp({"/api/device/start": [start_reply()],
                     "/api/device/poll": [NetworkError("x"), (429, {"error": "too_many_attempts"}), token_reply()]})
    _, waits, _, run = make_login(http)
    assert run()[0] == "ok" and waits == [5, 5, 10]  # transient errors do not abort; a 429 slows down


def test_login_stop_requested():
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [PENDING]})
    assert make_login(http, limit=2)[3]() == ("stopped", None)


# ---------------------------------------------------------------- uplink: states and sessions

def test_first_run_links_saves_connects_then_stops_on_4001(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [PENDING, token_reply()]})
    rig = Rig(tmp_path, http)
    ws = FakeWs(rig.clock, [WELCOME, PONG, ConnClosed(4001)])
    rig.ws_factory = ws
    rig.run()
    assert rig.states[:4] == ["linking", "connecting", "connected", "stopped"] and rig.up.state == "stopped"
    assert rig.store.load()["refresh"] == R1  # saved before the first connection
    assert rig.connects[0][0] == "wss://vc.example.test/ws/agent"
    assert rig.connects[0][1] == A1 and rig.connects[0][2]["refresh"] == R1
    assert len(rig.connects) == 1  # 4001: no reconnection
    assert "un'altra istanza" in rig.say_text()
    assert rig.shown and rig.shown[0]["user_code"] == "ABCD-EFGH"
    hello = ws.sent[0]
    assert hello == {"t": "hello", "v": 1, "app": "virtual-captain/" + uplink.APP_VERSION, "platform": sys.platform}
    assert ws.closed  # socket always closed


def test_pings_every_heartbeat(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens())
    ws = FakeWs(rig.clock, [WELCOME] + [None] * 74 + [PONG] * 1 + [ConnClosed(4001)])  # ~15 s of ticks
    rig.ws_factory = ws
    rig.run()
    pings = [m for m in ws.sent if m["t"] == "ping"]
    assert 2 <= len(pings) <= 3 and ws.sent[0]["t"] == "hello"


def test_silent_server_is_dropped_after_stale_and_reconnected(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), wait_limit=1)
    ws = FakeWs(rig.clock, [WELCOME] + [None] * 200)
    rig.ws_factory = ws
    rig.run()
    assert ws.closed and "collegamento perso" in rig.say_text()
    assert rig.waits == [pytest.approx(1.0)]


def test_bad_welcome_closes_and_retries(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), wait_limit=2)
    rig.ws_factory = lambda n: FakeWs(rig.clock, [json.dumps({"t": "welcome", "v": 2})])
    rig.run()
    assert len(rig.connects) == 2 and rig.waits == [1, 2]


def test_4003_clears_tokens_and_waits_for_a_new_link(tmp_path):
    http = FakeHttp({})
    rig = Rig(tmp_path, http, tokens=good_tokens())
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4003)])
    rig.run()
    assert rig.up.state == "relink_needed" and rig.store.load() is None
    assert len(rig.connects) == 1 and http.calls == [] and rig.waits == []  # no hammering, no polling


def test_4002_stops_without_retry(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens())
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4002)])
    rig.run()
    assert rig.up.state == "stopped" and len(rig.connects) == 1 and "aggiorna" in rig.say_text()
    assert rig.store.load() is not None  # tokens are fine: only the program is old


@pytest.mark.parametrize("code", [4004, 4005, 1001, 1006, 1000])
def test_other_closes_reconnect_with_backoff(tmp_path, code):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), wait_limit=2)
    rig.ws_factory = lambda n: FakeWs(rig.clock, [WELCOME, ConnClosed(code)])
    rig.run()
    assert len(rig.connects) == 2 and rig.waits == [1, 2]  # short session: backoff keeps growing


def test_backoff_resets_after_a_stable_session(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(ttl=10_000), wait_limit=3)

    def factory(n):
        if n == 1:
            return ConnError("down")
        if n == 2:
            return ConnError("down")
        if n == 3:  # stable for ~34 s, then dropped
            return FakeWs(rig.clock, [WELCOME] + [PONG] * 170 + [ConnClosed(1006)])
        return ConnError("down")
    rig.ws_factory = factory
    rig.run()
    assert rig.waits == [1, 2, 1]  # after the stable session the delay restarts from 1 s


def test_server_unreachable_connect_errors_back_off_without_exceptions(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(ttl=10_000), wait_limit=8)
    rig.ws_factory = ConnError("down")
    rig.run()
    assert rig.waits == [1, 2, 4, 8, 16, 32, 60, 60]
    assert rig.up.state == "connecting"


def test_server_unreachable_at_login_retries(tmp_path):
    http = FakeHttp({"/api/device/start": [NetworkError("down")]})
    rig = Rig(tmp_path, http, wait_limit=3)
    rig.run()
    assert rig.waits == [1, 2, 4] and rig.up.state in ("unlinked", "linking")
    assert "resta utilizzabile in locale" in rig.say_text()
    assert rig.msgs.count(rig.msgs[0]) == 1  # the same message is not repeated every retry


# ---------------------------------------------------------------- refresh

def test_refresh_before_expiry_saves_before_using(tmp_path):
    http = FakeHttp({"/api/device/token": [token_reply(access=A1, refresh=R1)]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=60))  # < 120 s left
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
    rig.run()
    assert http.calls == [("/api/device/token", {"grant_type": "refresh_token", "refresh_token": R0})]
    url, access, stored_at_connect = rig.connects[0]
    assert access == A1 and stored_at_connect["refresh"] == R1 and stored_at_connect["access"] == A1  # saved first


def test_no_refresh_when_the_access_token_is_fresh(tmp_path):
    http = FakeHttp({})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=200))
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
    rig.run()
    assert http.calls == [] and rig.connects[0][1] == A0


def test_invalid_grant_clears_tokens_without_polling(tmp_path):
    http = FakeHttp({"/api/device/token": [(400, {"error": "invalid_grant"})]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10))
    rig.run()
    assert rig.up.state == "relink_needed" and rig.store.load() is None
    assert http.paths() == ["/api/device/token"] and rig.connects == [] and rig.waits == []


def test_refresh_network_error_backs_off_and_keeps_tokens(tmp_path):
    http = FakeHttp({"/api/device/token": [NetworkError("x"), (503, {}), token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10))
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
    rig.run()
    assert rig.waits == [1, 2] and rig.connects[0][1] == A1
    assert http.paths().count("/api/device/token") == 3


def test_save_failure_keeps_running_with_the_new_tokens(tmp_path):
    http = FakeHttp({"/api/device/token": [token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10))
    real_save = rig.store.save
    fail = {"on": True}

    def save(t):
        if fail["on"]:
            raise OSError("read-only")
        real_save(t)
    rig.store.save = save
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(1006)])
    rig.wait_limit = 1
    rig.run()
    assert rig.connects[0][1] == A1 and "impossibile salvare" in rig.say_text()
    assert rig.up._dirty is not None  # retried at the next loop
    fail["on"] = False
    rig.up._stop.clear()
    rig.up._persist(rig.up._dirty)
    assert rig.store.load()["refresh"] == R1


def test_rejected_401_forces_one_refresh_not_a_loop(tmp_path):
    http = FakeHttp({"/api/device/token": [token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10_000), wait_limit=3)
    rig.ws_factory = ConnRejected(401)
    rig.run()
    assert http.paths().count("/api/device/token") == 1  # one refresh, then plain backoff
    assert len(rig.connects) == 3 and rig.waits == [1, 2, 4]


def test_repeated_401_after_a_fresh_refresh_ends_in_relink_needed(tmp_path):
    http = FakeHttp({"/api/device/token": [token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10_000), wait_limit=50)
    rig.ws_factory = ConnRejected(401)
    rig.run()
    assert rig.up.state == "relink_needed" and rig.store.load() is None
    assert http.paths().count("/api/device/token") == 1 and len(rig.connects) == 4  # 1 stale + 3 fresh rejections
    assert "appena rinnovati" in rig.say_text() and len(rig.waits) == 3  # backoff between tries, none after the end


def test_a_good_connection_clears_the_rejection_count(tmp_path):
    http = FakeHttp({"/api/device/token": [token_reply(), token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10), wait_limit=6)
    seq = [ConnRejected(401), ConnRejected(401), "ok"]

    def factory(n):
        item = seq[n - 1] if n <= len(seq) else ConnClosed(1006)
        return FakeWs(rig.clock, [WELCOME, ConnClosed(4001)]) if item == "ok" else item
    rig.ws_factory = factory
    rig.run()
    assert rig.up.state == "stopped" and rig.up._rejects == 0 and not rig.up._unproven


@pytest.mark.parametrize("status", [403, 429, 503])
def test_rejected_other_statuses_just_back_off(tmp_path, status):
    http = FakeHttp({})
    rig = Rig(tmp_path, http, tokens=good_tokens(ttl=10_000), wait_limit=2)
    rig.ws_factory = ConnRejected(status)
    rig.run()
    assert http.calls == [] and rig.waits == [1, 2] and rig.store.load() is not None


def test_tokens_of_another_server_are_not_used(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [token_reply()]})
    rig = Rig(tmp_path, http, tokens=good_tokens(base="https://other.example.test"))
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
    rig.run()
    assert http.paths()[0] == "/api/device/start" and rig.connects[0][1] == A1  # new login, old tokens not sent


def test_force_link_does_not_reuse_the_saved_tokens(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [token_reply()],
                     "/api/device/revoke": [(200, {"ok": True})]})
    rig = Rig(tmp_path, http, tokens=good_tokens(), force_link=True)
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
    rig.run()
    assert http.paths()[0] == "/api/device/start" and rig.connects[0][1] == A1


def test_link_keeps_the_old_tokens_until_the_new_link_works(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [(400, {"error": "access_denied"})]})
    rig = Rig(tmp_path, http, tokens=good_tokens(), force_link=True).run()
    assert rig.up.state == "stopped" and rig.store.load()["refresh"] == R0  # failed new link: old tokens stay
    assert "/api/device/revoke" not in http.paths()


def test_link_success_replaces_and_revokes_the_old_family_best_effort(tmp_path):
    for revoke_reply in ((200, {"ok": True}), NetworkError("down")):  # errors are ignored
        http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [token_reply()],
                         "/api/device/revoke": [revoke_reply]})
        rig = Rig(tmp_path / str(type(revoke_reply)), http, tokens=good_tokens(), force_link=True)
        rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4001)])
        rig.run()
        assert rig.store.load()["refresh"] == R1 and rig.up.state == "stopped"
        assert ("/api/device/revoke", {"token": R0}) in http.calls
        assert http.paths().index("/api/device/revoke") > http.paths().index("/api/device/poll")


def test_send_timeout_aborts_a_blocked_send(monkeypatch):
    import socket as _s

    class Raw:
        def __init__(self):
            self.ev = threading.Event()
            self.socket = self

        def shutdown(self, how):
            self.ev.set()

        def send(self, text):
            self.ev.wait(10)  # the server never reads: blocked until the socket is shut down
            raise OSError("closed")
    monkeypatch.setattr(uplink, "SEND_TIMEOUT", 0.2)
    ad = uplink._WsAdapter(Raw())
    t0 = time.monotonic()
    with pytest.raises(ConnError):
        ad.send("x")
    assert time.monotonic() - t0 < 2.0 and _s.SHUT_RDWR is not None


def test_denied_and_expired_login_are_terminal(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [(400, {"error": "access_denied"})]})
    rig = Rig(tmp_path, http).run()
    assert rig.up.state == "stopped" and rig.connects == [] and rig.waits == [5]
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [(400, {"error": "expired_token"})]})
    rig = Rig(tmp_path / "b", http).run()
    assert rig.up.state == "relink_needed" and rig.connects == []


def test_console_messages_never_hold_tokens_or_the_device_code(tmp_path):
    http = FakeHttp({"/api/device/start": [start_reply()], "/api/device/poll": [token_reply()]})
    rig = Rig(tmp_path, http)
    rig.ws_factory = FakeWs(rig.clock, [WELCOME, ConnClosed(4003)])
    rig.run()
    text = rig.say_text()
    for secret in (R1, A1, "dc" * 20, "ABCD-EFGH", "EFGH"):
        assert secret not in text


# ---------------------------------------------------------------- threads: never block the caller

def real_rig(tmp_path, http, connect, tokens=None):
    store = TokenStore(tmp_path / "vc", PlainProtector(), warn=lambda m: None)
    if tokens:
        store.save(tokens)
    return Uplink(BASE, store, http=http, connect=connect, say=lambda m: None)


def test_unreachable_server_never_blocks_and_stop_is_fast(tmp_path):
    http = FakeHttp({"/api/device/start": [NetworkError("down")]})
    up = real_rig(tmp_path, http, lambda *a: ConnError("down"))
    t0 = time.monotonic()
    up.start()
    assert time.monotonic() - t0 < 0.5  # start() returns at once
    time.sleep(0.3)
    s0 = time.monotonic()
    up.stop()
    assert time.monotonic() - s0 < 2.0
    assert not up._thread.is_alive()


def test_stop_while_connected_sends_bye_within_two_seconds(tmp_path):
    class SlowWs(FakeWs):
        def recv(self, timeout):
            if self.script:
                return super().recv(timeout)
            time.sleep(min(timeout, 0.2))
            return None
    ws = SlowWs(Clock(), [WELCOME], tick=False)
    up = real_rig(tmp_path, FakeHttp({}), lambda *a: ws, tokens=good_tokens(time.time(), ttl=10_000))
    up.start()
    for _ in range(100):
        if up.state == "connected":
            break
        time.sleep(0.02)
    assert up.state == "connected"
    s0 = time.monotonic()
    up.stop()
    assert time.monotonic() - s0 < 2.0 and not up._thread.is_alive()
    assert ws.sent[-1] == {"t": "bye"} and ws.closed


def test_unexpected_error_in_the_thread_is_contained(tmp_path):
    def boom(*a):
        raise RuntimeError("bug")
    up = real_rig(tmp_path, FakeHttp({}), boom, tokens=good_tokens(time.time(), ttl=10_000))
    up.start()
    up._thread.join(2)
    assert not up._thread.is_alive() and up.state == "stopped"


def test_missing_websockets_library_is_reported_not_fatal(tmp_path):
    msgs = []
    store = TokenStore(tmp_path / "vc", PlainProtector(), warn=lambda m: None)
    store.save(good_tokens(time.time(), ttl=10_000))

    def no_lib(*a):
        raise ImportError("websockets")
    up = Uplink(BASE, store, http=FakeHttp({}), connect=no_lib, say=msgs.append)
    up.run()
    assert up.state == "stopped" and "pip install -r requirements.txt" in msgs[0]


def test_uplink_module_never_touches_simconnect_or_voice():
    tree = ast.parse(Path(uplink.__file__).read_text(encoding="utf-8"))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
    assert not imported & {"simconnect_backend", "speak", "state", "web", "pyttsx3", "monitor", "brain"}
    assert imported <= {"__future__", "json", "os", "random", "socket", "sys", "threading", "time", "urllib", "dataclasses",
                        "pathlib", "typing", "protocol", "secure_store", "websockets", "msvcrt", "fcntl", "hashlib", "snapshot"}


# ---------------------------------------------------------------- snapshot del volo (U2)

def _run_with_source(tmp_path, source, ticks=30, factory=None):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), snapshot_source=source)
    ws = FakeWs(rig.clock, [WELCOME] + [None] * ticks + [ConnClosed(4001)])
    rig.ws_factory = factory or ws
    rig.run()
    return rig, ws


def _snaps(ws):
    return [m for m in ws.sent if m["t"] == "snapshot"]


def test_no_source_no_snapshot(tmp_path):
    rig, ws = _run_with_source(tmp_path, None)
    assert _snaps(ws) == []


def test_first_snapshot_right_after_the_hello_with_seq_and_version(tmp_path):
    rig, ws = _run_with_source(tmp_path, lambda: {"phase": "briefing", "n": 1}, ticks=2)
    assert [m["t"] for m in ws.sent[:2]] == ["hello", "snapshot"]
    assert ws.sent[1] == {"t": "snapshot", "sv": 1, "seq": 1, "d": {"n": 1, "phase": "briefing"}}


def test_unchanged_snapshot_is_not_resent_and_change_gets_the_next_seq(tmp_path):
    state = {"v": 1}
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), snapshot_source=lambda: {"phase": "flying", "v": state["v"]})
    class Ws(FakeWs):
        def recv(self, timeout):
            if len(self.sent) and self.clock.now > 1_000_000.0 + 5:
                state["v"] = 2  # a change after ~5 s
            return super().recv(timeout)
    ws = Ws(rig.clock, [WELCOME] + [None] * 45 + [ConnClosed(4001)])  # 9 s < STALE_AFTER
    rig.ws_factory = ws
    rig.run()
    snaps = _snaps(ws)
    assert [m["seq"] for m in snaps] == [1, 2] and snaps[0]["d"]["v"] == 1 and snaps[1]["d"]["v"] == 2


def test_at_most_one_snapshot_per_second(tmp_path):
    n = {"i": 0}

    def src():
        n["i"] += 1
        return {"phase": "flying", "i": n["i"]}  # changes at every call
    rig, ws = _run_with_source(tmp_path, src, ticks=50)  # 50 ticks = 10 s of clock
    assert 9 <= len(_snaps(ws)) <= 11 and n["i"] <= 11
    assert [m["seq"] for m in _snaps(ws)] == list(range(1, len(_snaps(ws)) + 1))


def test_seq_restarts_and_the_first_snapshot_is_resent_on_each_connection(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), snapshot_source=lambda: {"phase": "flying"}, wait_limit=3)
    wss = []

    def factory(n):
        ws = FakeWs(rig.clock, [WELCOME, None, None, ConnClosed(1006) if n < 2 else ConnClosed(4001)])
        wss.append(ws)
        return ws
    rig.ws_factory = factory
    rig.run()
    assert len(wss) == 2 and all([m["seq"] for m in _snaps(w)] == [1] for w in wss)


def test_source_errors_are_contained_reported_once_and_recover(tmp_path):
    calls = {"n": 0}

    def src():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("SECRET-CONTENT lat=1.5")
        return {"phase": "flying"}
    rig, ws = _run_with_source(tmp_path, src, ticks=30)
    assert [m["seq"] for m in _snaps(ws)] == [1]  # recovered, connection never dropped
    said = rig.say_text()
    assert said.count("snapshot del volo non disponibile") == 1 and "SECRET-CONTENT" not in said and "lat=" not in said


def test_source_returning_none_sends_nothing(tmp_path):
    rig, ws = _run_with_source(tmp_path, lambda: None, ticks=10)
    assert _snaps(ws) == []


def test_snapshot_messages_are_valid_json_under_the_size_limit(tmp_path):
    rig, ws = _run_with_source(tmp_path, lambda: {"phase": "flying", "log": ["x" * 300] * 30}, ticks=3)
    assert len(json.dumps(_snaps(ws)[0]).encode()) < protocol.MAX_MSG


def test_server_rejection_1008_after_snapshots_is_reported_once_without_content(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), snapshot_source=lambda: {"phase": "flying", "log": ["SECRET-X"]}, wait_limit=3)
    rig.ws_factory = lambda n: FakeWs(rig.clock, [WELCOME, None, None, ConnClosed(1008 if n < 3 else 4001)])
    rig.run()
    said = rig.say_text()
    assert said.count("snapshot e' stato rifiutato dal server") == 1 and "SECRET-X" not in said


def test_1008_without_snapshots_sent_is_not_reported_as_rejected(tmp_path):
    rig = Rig(tmp_path, FakeHttp({}), tokens=good_tokens(), wait_limit=2)
    rig.ws_factory = lambda n: FakeWs(rig.clock, [WELCOME, ConnClosed(1008 if n < 2 else 4001)])
    rig.run()
    assert "rifiutato" not in rig.say_text()


def test_local_validation_failure_is_reported_once_and_nothing_is_sent(tmp_path):
    import snapshot as S
    from server import wire
    def src():
        return S.remote_view({"phase": "flying", "fields": {"a": 5}})  # numeric value: the allowlist refuses it
    rig, ws = _run_with_source(tmp_path, src, ticks=20)
    assert _snaps(ws) == [] and rig.say_text().count("snapshot del volo non disponibile (SnapshotError)") == 1


# ---------------------------------------------------------------- instance lock

def test_instance_lock_second_holder_is_refused(tmp_path):
    a, b = InstanceLock(tmp_path / "d" / "x.lock"), InstanceLock(tmp_path / "d" / "x.lock")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()
    b.release()  # idempotent


def test_second_instance_starts_without_uplink_and_says_so(tmp_path):
    msgs = []
    held = InstanceLock(tmp_path / uplink.LOCK_NAME)
    assert held.acquire()
    assert uplink.launch([], lambda i: None, say=msgs.append, directory=tmp_path) is None
    assert "un'altra istanza" in msgs[0] and "senza" in msgs[0]
    held.release()


# ---------------------------------------------------------------- options: default ON

def test_default_is_on_and_no_server_or_local_turn_it_off():
    cfg = {"enabled": True, "base_url": BASE}
    assert uplink.parse_options([], cfg).enabled is True
    assert uplink.parse_options(["--sim"], cfg).enabled is True
    for flag in ("--no-server", "--local"):
        assert uplink.parse_options([flag, "--sim"], cfg).enabled is False
    assert uplink.parse_options(["--server", "--no-server"], cfg).enabled is False
    o = uplink.parse_options(["--link"], cfg)
    assert o.link and not o.unlink
    assert uplink.parse_options(["--unlink"], cfg).unlink


def test_options_saving(tmp_path):
    saved = []
    cfg = {"enabled": True, "base_url": BASE}
    uplink.parse_options(["--no-server"], cfg, saved.append)
    assert saved == []  # --no-server / --local only affect this run
    uplink.parse_options(["--server"], {"enabled": False, "base_url": BASE}, saved.append)
    assert saved == [{"enabled": True, "base_url": BASE}]
    o = uplink.parse_options(["--server-url", "https://other.example.test/"], cfg, saved.append)
    assert o.base_url == "https://other.example.test" and saved[-1]["base_url"] == "https://other.example.test"
    o = uplink.parse_options(["--server-url", "http://vc.example.test"], cfg, saved.append)
    assert o.enabled is False and "https" in o.error and len(saved) == 2  # refused, nothing saved
    assert uplink.parse_options(["--server-url"], cfg).error


def test_config_file_roundtrip_and_corruption(tmp_path):
    assert uplink.load_config(tmp_path) == {"enabled": True, "base_url": uplink.DEFAULT_SERVER_URL}
    uplink.save_config(tmp_path, {"enabled": False, "base_url": BASE})
    assert uplink.load_config(tmp_path) == {"enabled": False, "base_url": BASE}
    (tmp_path / uplink.CONFIG_NAME).write_text("{broken", encoding="utf-8")
    assert uplink.load_config(tmp_path)["enabled"] is True
    (tmp_path / uplink.CONFIG_NAME).write_text(json.dumps({"enabled": "no", "base_url": 5}), encoding="utf-8")
    assert uplink.load_config(tmp_path) == {"enabled": True, "base_url": uplink.DEFAULT_SERVER_URL}


def test_launch_default_starts_a_thread_and_no_server_does_not(tmp_path):
    msgs = []
    http = FakeHttp({"/api/device/start": [NetworkError("down")]})
    up = uplink.launch([], lambda i: None, say=msgs.append, directory=tmp_path, http=http,
                       store=TokenStore(tmp_path, PlainProtector(), warn=lambda m: None))
    try:
        assert up is not None and up._thread.is_alive() and up.base_url == uplink.DEFAULT_SERVER_URL
    finally:
        up.stop()
    assert not up._thread.is_alive()
    before = {t.name for t in threading.enumerate()}
    assert uplink.launch(["--no-server"], lambda i: None, say=msgs.append, directory=tmp_path / "b") is None
    assert {t.name for t in threading.enumerate()} == before
    assert any("disattivato" in m for m in msgs)
    assert uplink.launch(["--local"], lambda i: None, say=msgs.append, directory=tmp_path / "c") is None


def test_launch_bad_url_disables_the_uplink_but_does_not_raise(tmp_path):
    msgs = []
    assert uplink.launch(["--server-url", "http://evil.example.test"], lambda i: None, say=msgs.append,
                         directory=tmp_path) is None
    assert "senza collegamento" in msgs[0]


# ---------------------------------------------------------------- unlink

def test_unlink_revokes_then_clears(tmp_path):
    store = TokenStore(tmp_path, PlainProtector(), warn=lambda m: None)
    store.save(good_tokens())
    http = FakeHttp({"/api/device/revoke": [(200, {"ok": True})]})
    msgs = []
    assert uplink.unlink_cli(["--unlink"], msgs.append, store, http) == 0
    assert http.calls == [("/api/device/revoke", {"token": R0})] and store.load() is None
    assert all(R0 not in m for m in msgs)


def test_unlink_keeps_the_tokens_if_the_server_is_unreachable(tmp_path):
    store = TokenStore(tmp_path, PlainProtector(), warn=lambda m: None)
    store.save(good_tokens())
    assert uplink.unlink_cli(["--unlink"], lambda m: None, store, FakeHttp({"/api/device/revoke": [NetworkError("x")]})) == 1
    assert store.load() is not None
    assert uplink.unlink_cli(["--unlink"], lambda m: None, store, FakeHttp({"/api/device/revoke": [(429, {})]})) == 1


def test_unlink_without_tokens_is_a_no_op(tmp_path):
    http = FakeHttp({})
    assert uplink.unlink_cli(["--unlink"], lambda m: None, TokenStore(tmp_path, PlainProtector()), http) == 0
    assert http.calls == []


# ---------------------------------------------------------------- stdlib http client

class _Resp:
    def __init__(self, status, body):
        self.status, self._b = status, body

    def read(self, n=-1):
        return self._b[:n] if n > 0 else self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_urllib_http_sends_user_agent_and_json(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req, timeout))
        return _Resp(200, b'{"ok": true}')
    monkeypatch.setattr(uplink.urllib.request, "urlopen", fake_urlopen)
    status, data = uplink.UrllibHttp().post_json(BASE + "/api/device/start", {"a": 1})
    req = seen[0][0]
    assert (status, data) == (200, {"ok": True})
    assert req.get_method() == "POST" and req.full_url == BASE + "/api/device/start"
    assert req.get_header("User-agent") == "VirtualCaptain/" + uplink.APP_VERSION
    assert req.get_header("Content-type") == "application/json" and json.loads(req.data) == {"a": 1}
    assert req.get_header("Origin") is None and seen[0][1] == 15.0


def test_urllib_http_errors(monkeypatch):
    def http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, __import__("io").BytesIO(b'{"error":"slow_down"}'))
    monkeypatch.setattr(uplink.urllib.request, "urlopen", http_error)
    assert uplink.UrllibHttp().post_json(BASE + "/x", {}) == (400, {"error": "slow_down"})

    def down(req, timeout=None):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(uplink.urllib.request, "urlopen", down)
    with pytest.raises(NetworkError):
        uplink.UrllibHttp().post_json(BASE + "/x", {})

    monkeypatch.setattr(uplink.urllib.request, "urlopen", lambda req, timeout=None: _Resp(200, b"<html>"))
    assert uplink.UrllibHttp().post_json(BASE + "/x", {}) == (200, {})


# ---------------------------------------------------------------- the real websockets adapter (loopback only)

def test_websockets_adapter_against_a_loopback_server():
    pytest.importorskip("websockets")
    from websockets.exceptions import InvalidStatus  # noqa: F401
    from websockets.http11 import Response
    from websockets.sync.server import serve
    seen = {}

    def process_request(connection, request):
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.path
        seen["origin"] = request.headers.get("Origin")
        seen["ua"] = request.headers.get("User-Agent")
        if request.headers.get("Authorization") == "Bearer BAD":
            return connection.respond(401, "no\n")
        return None

    def handler(ws):
        ws.send(WELCOME)
        hello = json.loads(ws.recv())
        seen["hello"] = hello
        ws.send(PONG)
        time.sleep(0.3)
        ws.close(4001)

    with serve(handler, "127.0.0.1", 0, process_request=process_request) as server:
        port = server.socket.getsockname()[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"ws://127.0.0.1:{port}/ws/agent"
        with pytest.raises(ConnRejected) as exc:
            uplink.websockets_connect(url, "BAD")
        assert exc.value.status == 401
        conn = uplink.websockets_connect(url, "GOOD-TOKEN-123")
        assert seen["auth"] == "Bearer GOOD-TOKEN-123" and seen["path"] == "/ws/agent"
        assert seen["origin"] is None and seen["ua"].startswith("VirtualCaptain/")
        assert json.loads(conn.recv(2)) == json.loads(WELCOME)
        conn.send(json.dumps({"t": "hello", "v": 1}))
        assert json.loads(conn.recv(2)) == {"t": "pong"}
        assert conn.recv(0.05) is None  # idle: timeout, connection still fine
        with pytest.raises(ConnClosed) as closed:
            for _ in range(20):
                conn.recv(0.2)
        assert closed.value.code == 4001
        conn.close()
        server.shutdown()
    with pytest.raises(ConnError):
        uplink.websockets_connect(f"ws://127.0.0.1:{port}/ws/agent", "x", timeout=1)  # nobody listening now


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
