"""Comandi remoti lato PC (U3, consegna 1): commands.py (esecuzione su State, riconvalida, worker) e la sessione dell'uplink
con un WebSocket finto. Nessuna rete, nessun SimConnect, nessuna voce."""
from __future__ import annotations

import ast
import json
import pathlib
import queue
import subprocess
import sys
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest

import commands
import protocol
import snapshot
import uplink
from secure_store import PlainProtector, TokenStore
from server import wire
from test_snapshot import make_state

ROOT = pathlib.Path(__file__).resolve().parent
ID1, ID2 = "1" * 32, "2" * 32


def ids(n):
    return "%032x" % n


# ---------------------------------------------------------------- isolamento dei moduli

def test_pc_modules_do_not_import_the_server_stack():
    code = ("import sys; sys.path.insert(0, %r); import protocol, uplink, secure_store, snapshot, commands; "
            "bad = [m for m in ('fastapi', 'uvicorn', 'websockets', 'starlette', 'httpx') if m in sys.modules]; "
            "assert not bad, bad" % str(ROOT))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr


def test_commands_module_has_no_local_machine_functions():
    src = (ROOT / "commands.py").read_text(encoding="utf-8").lower()
    for word in ("simdll", "fetch_dll", "simconnect", "speak", "pyttsx3", "subprocess", "os.system",
                 "shutil", "open(", "eval(", "exec(", "__import__"):
        assert word not in src, word
    tree = ast.parse((ROOT / "commands.py").read_text(encoding="utf-8"))
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"__future__", "queue", "threading", "time", "urllib", "collections", "typing", "server"}, mods
    assert set(commands.COMMANDS) == set(wire.REMOTE_COMMANDS)


def test_uplink_still_does_not_import_state_or_voice():
    tree = ast.parse((ROOT / "uplink.py").read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not mods & {"state", "speak", "simconnect_backend", "monitor", "brain", "web"}


def test_simbrief_host_is_fixed_and_the_parameter_is_url_encoded():
    import simbrief
    assert simbrief.URL.startswith("https://www.simbrief.com/")
    src = (ROOT / "simbrief.py").read_text(encoding="utf-8")
    assert "urllib.parse.urlencode" in src and 'f"{URL}?{qs}"' in src


# ---------------------------------------------------------------- execute() su uno State vero

def test_execute_fields_clearance_checklist_phase_and_phase():
    st = make_state()
    assert commands.execute(st, "fields", {"v1": "150", "origin": "LIMC/17R"}) == {"ok": True}
    assert st.fields["v1"] == "150"
    assert commands.execute(st, "fields", {"mode": "training", "checklist_mode": "simplified"}) == {"ok": True}
    assert st.mode == "training" and st.checklist_mode == "simplified"
    assert commands.execute(st, "clearance", {"c": "EDDF", "t": "1234"}) == {"ok": True}
    assert st.clearance["c"] == "EDDF" and st.clearance["t"] == "1234"
    assert commands.execute(st, "clearance", {"reset": True}) == {"ok": True}
    assert st.clearance["t"] == ""
    first = st.cl_visible()[0]
    assert commands.execute(st, "checklist-phase", {"step": 1}) == {"ok": True}
    assert st.cl_phase == st.cl_visible()[1]
    assert commands.execute(st, "checklist-phase", {"step": -1}) == {"ok": True} and st.cl_phase == first
    assert commands.execute(st, "checklist-phase", {"phase": st.cl_visible()[2]}) == {"ok": True}
    assert commands.execute(st, "phase", {"phase": "briefing", "confirm": True}) == {"ok": True} and st.phase == "briefing"
    assert commands.execute(st, "phase", {"phase": "flying"}) == {"ok": True} and st.phase == "flying"


def test_execute_revalidates_what_the_server_sends():
    """The program does not trust the server: the same schemas, and nothing changes on a rejected command."""
    st = make_state()
    st.simbrief = "KEEP"
    before = (dict(st.fields), st.mode, st.simbrief, st.phase, dict(st.clearance))
    for name, data in (("fields", {"simbrief": "HACK"}), ("fields", {"v1": 150}), ("fields", {"nope": "x"}),
                       ("fields", {"mode": "silent"}), ("clearance", {"t": "9"}), ("clearance", {"reset": True, "c": "x"}),
                       ("checklist-phase", {"step": 5}), ("checklist-phase", {}), ("phase", {"phase": "briefing"}),
                       ("phase", {"phase": "nope"}), ("load-plan", {"simbrief": "a;b"}), ("load-plan", {"simbrief": "   "}), ("sim-retry", {"x": 1}),
                       ("simdll-fetch", {"confirm": True}), ("fetch_dll", {}),
                       ("nonsense", {}), ("fields", "text"), ("fields", None), (None, {})):
        out = commands.execute(st, name, data)
        assert out["ok"] is False and out["code"] == "invalid" and len(out["msg"]) <= wire.MAX_RESULT_MSG, (name, data)
    assert (dict(st.fields), st.mode, st.simbrief, st.phase, dict(st.clearance)) == before


def test_execute_state_errors_are_invalid_not_failures():
    st = make_state()
    out = commands.execute(st, "checklist-phase", {"phase": "no-such-phase"})
    assert out == {"ok": False, "code": "invalid", "msg": "unknown checklist phase"}


def test_sim_retry_calls_the_backend_only():
    st = make_state()
    calls = []
    st.sim_backend = SimpleNamespace(retry=lambda: calls.append("retry"), retry_dll=lambda: calls.append("dll"))
    assert commands.execute(st, "sim-retry", {}) == {"ok": True} and calls == ["retry"]
    st.sim_backend = None
    assert commands.execute(st, "sim-retry", {}) == {"ok": True}


def test_load_plan_uses_the_given_code_or_the_known_one(monkeypatch):
    import state
    got = []
    monkeypatch.setattr(state, "fetch_latest", lambda user: got.append(user) or {"origin": {"icao_code": "LIMC", "plan_rwy": "17R"}, "destination": {"icao_code": "EDDF"},
                                                                                  "params": {"time_generated": "1700000000"}})
    st = make_state()
    st.simbrief = "KNOWN1"
    assert commands.execute(st, "load-plan", {"simbrief": "NEW42"}) == {"ok": True}
    assert commands.execute(st, "load-plan", {}) == {"ok": True}
    assert got == ["NEW42", "NEW42"]  # the code given becomes the known one; {} reuses it
    st.simbrief = "KNOWN2"
    commands.execute(st, "load-plan", {})
    assert got[-1] == "KNOWN2"


def _http(code):
    return lambda: urllib.error.HTTPError("u", code, "x", {}, None)


@pytest.mark.parametrize("make,reason", [
    (lambda: urllib.error.URLError("dns"), "network"), (lambda: TimeoutError(), "network"), (lambda: ConnectionResetError(), "network"),
    (_http(404), "not_found"), (_http(400), "not_found"), (_http(503), "network"),
    (lambda: ValueError("not json LEAK"), "bad_plan"), (lambda: KeyError("destination"), "bad_plan"), (lambda: TypeError("x"), "bad_plan")],
    ids=["dns", "timeout", "reset", "http404", "http400", "http503", "badjson", "keyerror", "typeerror"])
def test_load_plan_failures_have_a_fixed_reason_and_never_the_exception_text(monkeypatch, make, reason):
    import state

    def boom(user):
        raise make()
    monkeypatch.setattr(state, "fetch_latest", boom)
    st = make_state()
    out = commands.execute(st, "load-plan", {"simbrief": "X1"})
    assert out == {"ok": False, "code": "failed", "reason": reason}


def test_other_execution_errors_are_failed_without_text():
    class Boom:
        simbrief = ""

        def update(self, d):
            raise RuntimeError("SECRET-TEXT")
    assert commands.execute(Boom(), "fields", {"v1": "1"}) == {"ok": False, "code": "failed"}


# ---------------------------------------------------------------- il worker

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def worker(executor=None, clock=None, **kw):
    w = commands.CommandWorker(SimpleNamespace(), mono=clock or Clock(), executor=executor or (lambda st, n, d: {"ok": True}), **kw)
    w.start()
    return w


def collect(w, n, timeout=3.0):
    out, end = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        try:
            out.append(w.results.get(timeout=0.05))
        except queue.Empty:
            pass
    return out


def test_worker_runs_a_command_and_answers_with_its_id():
    seen = []
    w = worker(lambda st, n, d: seen.append((n, d)) or {"ok": True})
    w.submit(ID1, "fields", {"v1": "150"})
    assert collect(w, 1) == [{"t": "result", "id": ID1, "ok": True}]
    assert seen == [("fields", {"v1": "150"})]
    w.close()


def test_worker_validates_before_executing():
    seen = []
    w = worker(lambda st, n, d: seen.append(n) or {"ok": True})
    w.submit(ID1, "fields", {"simbrief": "x"})
    w.submit(ID2, "simdll-fetch", {})
    out = collect(w, 2)
    assert [(r["id"], r["code"]) for r in out] == [(ID1, "invalid"), (ID2, "invalid")] and seen == []
    w.close()


def test_worker_ignores_bad_ids_and_repeated_ids():
    seen = []
    w = worker(lambda st, n, d: seen.append(n) or {"ok": True})
    for bad in (None, 5, "", "short", ID1 + "\n", "G" * 32, "A" * 32, "1" * 31, "1" * 33):
        w.submit(bad, "sim-retry", {})
    w.submit(ID1, "fields", {})
    assert collect(w, 1)[0]["id"] == ID1
    w.submit(ID1, "fields", {})  # repeated: ignored, no second execution, no second result
    time.sleep(0.2)
    assert w.results.empty() and seen == ["fields"]
    w.close()


def test_worker_remembers_only_the_last_64_ids():
    w = worker()
    for i in range(70):
        w.submit(ids(i), "fields", {})
        collect(w, 1)
        w._times.clear()  # (not testing the rate limit here)
    assert len(w._ids) == commands.CACHE_IDS
    w.submit(ids(0), "fields", {})  # forgotten: runs again
    assert collect(w, 1)[0]["id"] == ids(0)
    w.close()


def test_worker_one_at_a_time_second_is_busy():
    gate, started = threading.Event(), threading.Event()

    def slow(st, name, d):
        started.set()
        gate.wait(5)
        return {"ok": True}
    w = worker(slow)
    w.submit(ID1, "fields", {})
    assert started.wait(2)
    w.submit(ID2, "checklist-phase", {"step": 1})
    out = collect(w, 1)
    assert out == [{"t": "result", "id": ID2, "ok": False, "code": "busy"}]
    gate.set()
    assert collect(w, 1)[0]["id"] == ID1
    w.submit(ids(3), "fields", {})  # free again
    assert collect(w, 1)[0]["ok"] is True
    w.close()


def test_worker_rate_limit_ten_in_ten_seconds():
    clock = Clock()
    w = worker(clock=clock)
    for i in range(10):
        w.submit(ids(i), "fields", {})
        assert collect(w, 1)[0]["ok"] is True
    w.submit(ids(11), "fields", {})
    assert collect(w, 1)[0] == {"t": "result", "id": ids(11), "ok": False, "code": "busy"}
    clock.now += 10.5
    w.submit(ids(12), "fields", {})
    assert collect(w, 1)[0]["ok"] is True
    w.close()


def test_worker_minimum_gap_for_load_plan_and_sim_retry():
    clock = Clock()
    w = worker(clock=clock)
    for name, gap in (("load-plan", 10), ("sim-retry", 5)):
        w.submit(ids(hash(name) % 1000 + 1), name, {})
        assert collect(w, 1)[0]["ok"] is True
        clock.now += gap - 1
        w.submit(ids(hash(name) % 1000 + 2), name, {})
        assert collect(w, 1)[0]["code"] == "busy"
        clock.now += 1.5
        w.submit(ids(hash(name) % 1000 + 3), name, {})
        assert collect(w, 1)[0]["ok"] is True
    w.close()


def test_worker_disabled_answers_disabled_and_runs_nothing():
    seen = []
    w = worker(lambda st, n, d: seen.append(n) or {"ok": True}, enabled=False)
    w.submit(ID1, "fields", {})
    w.submit(ID2, "simdll-fetch", {})
    assert collect(w, 2) == [{"t": "result", "id": ID1, "ok": False, "code": "disabled"},
                             {"t": "result", "id": ID2, "ok": False, "code": "disabled"}] and seen == []
    w.close()


def test_worker_survives_an_executor_that_raises_and_closes_cleanly():
    def boom(st, n, d):
        raise RuntimeError("TEXT")
    w = worker(boom)
    w.submit(ID1, "fields", {})
    assert collect(w, 1) == [{"t": "result", "id": ID1, "ok": False, "code": "failed"}]
    w.close()
    w._thread.join(2)
    assert not w._thread.is_alive()


def test_worker_closed_while_busy_stops_after_the_job():
    gate = threading.Event()
    w = worker(lambda st, n, d: gate.wait(5) and {"ok": True})
    w.submit(ID1, "fields", {})
    time.sleep(0.1)
    w.close()
    gate.set()
    w._thread.join(3)
    assert not w._thread.is_alive()


# ---------------------------------------------------------------- la sessione dell'uplink

BASE = "https://vc.example.test"


class LiveWs:
    """WebSocket finto con coda in ingresso (thread-safe): welcome subito, poi cio' che il test mette in coda."""

    def __init__(self, heartbeat=1):
        self.inbox, self.sent, self.closed, self.lock = queue.Queue(), [], [], threading.Lock()
        self.inbox.put(json.dumps({"t": "welcome", "v": 1, "heartbeat": heartbeat, "max_msg": 262144}))

    def recv(self, timeout):
        try:
            return self.inbox.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None

    def send(self, text):
        with self.lock:
            self.sent.append(json.loads(text))

    def close(self, code=1000):
        self.closed.append(code)

    def kinds(self):
        with self.lock:
            return [m["t"] for m in self.sent]

    def results(self):
        with self.lock:
            return [m for m in self.sent if m["t"] == "result"]

    def put_cmd(self, cmd_id, name, d):
        self.inbox.put(json.dumps({"t": "cmd", "id": cmd_id, "name": name, "d": d}))


def start_uplink(tmp_path, st, ws, **kw):
    store = TokenStore(tmp_path / "vc", PlainProtector(), warn=lambda m: None)
    store.save({"refresh": "R" * 40, "access": "A" * 40, "access_exp": time.time() + 3600, "base_url": BASE})
    msgs = []
    up = uplink.Uplink(BASE, store, http=None, connect=lambda url, token: ws, say=msgs.append,
                       snapshot_source=lambda: snapshot.remote_view(st.snapshot()), command_target=st, **kw)
    up.start()
    return up, msgs


def waitfor(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


def hello_of(ws):
    assert waitfor(lambda: "hello" in ws.kinds())
    return next(m for m in ws.sent if m["t"] == "hello")


def test_hello_announces_the_capability_or_not(tmp_path):
    for kw, expect in (({}, {"caps": ["cmd"], "cv": 1}), ({"remote_commands": False}, {"cv": 1})):
        ws, st = LiveWs(), make_state()
        up, _ = start_uplink(tmp_path / ("a" if not kw else "b"), st, ws, **kw)
        h = hello_of(ws)
        up.stop()
        assert {k: h[k] for k in ("caps", "cv") if k in h} == expect
        assert "caps" not in h or h["caps"] == ["cmd"]
    ws, st = LiveWs(), make_state()
    store = TokenStore(tmp_path / "c", PlainProtector(), warn=lambda m: None)
    store.save({"refresh": "R" * 40, "access": "A" * 40, "access_exp": time.time() + 3600, "base_url": BASE})
    up = uplink.Uplink(BASE, store, connect=lambda u, t: ws, say=lambda m: None)  # no command target: a read-only program
    up.start()
    h = hello_of(ws)
    up.stop()
    assert "caps" not in h and "cv" not in h


@pytest.mark.parametrize("rep", range(3))
def test_command_changes_state_and_the_fresh_snapshot_comes_before_the_result(tmp_path, rep):
    ws, st = LiveWs(), make_state()
    up, msgs = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    assert waitfor(lambda: "snapshot" in ws.kinds())
    marker = "V1MARK"
    ws.put_cmd(ID1, "fields", {"v1": "157"})
    assert waitfor(lambda: ws.results())
    up.stop()
    assert st.fields["v1"] == "157"
    assert ws.results() == [{"t": "result", "id": ID1, "ok": True}]
    sent = ws.sent
    r_idx = next(i for i, m in enumerate(sent) if m["t"] == "result")
    fresh = [m for m in sent[:r_idx] if m["t"] == "snapshot" and m["d"]["fields"].get("v1") == "157"]
    assert fresh, "the snapshot with the new value must precede the result"
    assert marker not in "".join(msgs)


def test_rejected_and_local_only_commands_leave_the_state_alone(tmp_path):
    ws, st = LiveWs(), make_state()
    st.simbrief = "KEEP"
    up, msgs = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    cmds = [("fields", {"simbrief": "HACK"}), ("simdll-fetch", {"confirm": True}),
            ("fields", {"v1": 1}), ("nonsense", {})]
    for i, (n, d) in enumerate(cmds):
        ws.put_cmd(ids(i + 1), n, d)
        time.sleep(0.05)
    assert waitfor(lambda: len(ws.results()) == len(cmds))
    up.stop()
    assert [(r["id"], r["code"]) for r in ws.results()] == [(ids(i + 1), "invalid") for i in range(len(cmds))]
    assert st.simbrief == "KEEP" and st.fields["v1"] == ""
    assert not any(w in "".join(msgs) for w in ("HACK", "simdll"))


def test_switched_off_program_answers_disabled(tmp_path):
    ws, st = LiveWs(), make_state()
    up, _ = start_uplink(tmp_path, st, ws, remote_commands=False)
    hello_of(ws)
    ws.put_cmd(ID1, "fields", {"v1": "157"})
    assert waitfor(lambda: ws.results())
    up.stop()
    assert ws.results() == [{"t": "result", "id": ID1, "ok": False, "code": "disabled"}]
    assert st.fields["v1"] == ""


def test_oversized_and_unknown_messages_are_ignored(tmp_path):
    ws, st = LiveWs(), make_state()
    up, _ = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    ws.inbox.put(json.dumps({"t": "cmd", "id": ID1, "name": "fields", "d": {"v1": "x" * 5000}}))
    ws.inbox.put(json.dumps({"t": "weird"}))
    ws.inbox.put("not json")
    ws.inbox.put(json.dumps({"t": "cmd", "id": "bad", "name": "fields", "d": {}}))
    ws.inbox.put(json.dumps({"t": "cmd", "id": ID2, "name": "fields", "d": {"v1": "9"}}))
    assert waitfor(lambda: ws.results())
    up.stop()
    assert [r["id"] for r in ws.results()] == [ID2]


def test_slow_load_plan_does_not_stop_pings_or_snapshots_and_a_second_command_is_busy(tmp_path, monkeypatch):
    import state
    gate, started = threading.Event(), threading.Event()

    def slow_fetch(user):
        started.set()
        gate.wait(10)
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(state, "fetch_latest", slow_fetch)
    ws, st = LiveWs(heartbeat=1), make_state()
    up, _ = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    assert waitfor(lambda: "snapshot" in ws.kinds())
    ws.put_cmd(ID1, "load-plan", {"simbrief": "SB123"})
    assert started.wait(3)
    pings, snaps = ws.kinds().count("ping"), ws.kinds().count("snapshot")
    ws.put_cmd(ID2, "sim-retry", {})
    assert waitfor(lambda: any(r["id"] == ID2 for r in ws.results()))
    assert next(r for r in ws.results() if r["id"] == ID2) == {"t": "result", "id": ID2, "ok": False, "code": "busy"}
    st.add_log("12:00 while the plan is loading")
    assert waitfor(lambda: ws.kinds().count("ping") > pings, 4.0), "the heartbeat must go on during a slow load-plan"
    assert waitfor(lambda: ws.kinds().count("snapshot") > snaps, 4.0), "snapshots must go on during a slow load-plan"
    assert not any(r["id"] == ID1 for r in ws.results())
    gate.set()
    assert waitfor(lambda: any(r["id"] == ID1 for r in ws.results()))
    up.stop()
    assert next(r for r in ws.results() if r["id"] == ID1) == {"t": "result", "id": ID1, "ok": False, "code": "failed", "reason": "network"}


def test_repeated_id_is_ignored_in_the_session(tmp_path):
    ws, st = LiveWs(), make_state()
    up, _ = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    ws.put_cmd(ID1, "checklist-phase", {"step": 1})
    assert waitfor(lambda: ws.results())
    phase = st.cl_phase
    ws.put_cmd(ID1, "checklist-phase", {"step": 1})  # a replay must not step twice
    time.sleep(0.4)
    up.stop()
    assert st.cl_phase == phase and len(ws.results()) == 1


def test_worker_thread_ends_with_the_session(tmp_path):
    before = {t for t in threading.enumerate() if t.name == "commands"}
    ws, st = LiveWs(), make_state()
    up, _ = start_uplink(tmp_path, st, ws)
    hello_of(ws)
    assert waitfor(lambda: any(t.name == "commands" and t not in before for t in threading.enumerate()))
    up.stop()
    assert waitfor(lambda: not any(t.name == "commands" and t not in before and t.is_alive() for t in threading.enumerate()))


# ---------------------------------------------------------------- opzioni

def test_no_remote_commands_option_and_config_key():
    cfg = {"enabled": True, "base_url": BASE}
    assert uplink.parse_options([], cfg).remote_commands is True
    assert uplink.parse_options(["--no-remote-commands"], cfg).remote_commands is False
    assert uplink.parse_options([], dict(cfg, remote_commands=False)).remote_commands is False
    saved = []
    uplink.parse_options(["--no-remote-commands", "--server"], cfg, saved.append)
    assert saved == [{"enabled": True, "base_url": BASE}]  # the switch of this run is not saved
    saved.clear()
    uplink.parse_options(["--server"], dict(cfg, remote_commands=False), saved.append)
    assert saved == [{"enabled": True, "base_url": BASE, "remote_commands": False}]  # the key chosen in the file survives


def test_config_file_keeps_the_remote_commands_key(tmp_path):
    assert "remote_commands" not in uplink.load_config(tmp_path)
    (tmp_path / "uplink.json").write_text(json.dumps({"enabled": True, "base_url": BASE, "remote_commands": False}))
    cfg = uplink.load_config(tmp_path)
    assert cfg["remote_commands"] is False
    uplink.save_config(tmp_path, cfg)
    assert json.loads((tmp_path / "uplink.json").read_text())["remote_commands"] is False
    (tmp_path / "uplink.json").write_text(json.dumps({"enabled": True, "base_url": BASE, "remote_commands": "no"}))
    assert "remote_commands" not in uplink.load_config(tmp_path)


def test_launch_tells_when_commands_are_off(tmp_path):
    said = []
    up = uplink.launch(["--no-remote-commands"], lambda i: None, say=said.append, directory=tmp_path,
                       store=TokenStore(tmp_path, PlainProtector(), warn=lambda m: None),
                       connect=lambda u, t: (_ for _ in ()).throw(uplink.ConnError()), command_target=make_state())
    try:
        assert any("--no-remote-commands" in m for m in said)
        assert up is not None and up.remote_commands is False
    finally:
        if up is not None:
            up.stop()
