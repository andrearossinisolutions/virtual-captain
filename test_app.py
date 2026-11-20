"""Tests of the non-GUI parts of the window version (no pytest, no tkinter): status registry and State/Uplink -> rows
mapping, applog, paths, the main loop of App with --sim, login code never in the logs. Run: python3 test_app.py"""
import logging
import os
import pathlib
import queue
import sys
import tempfile
import threading

import applog
import main
import paths
import status as S
from state import State


# ---------------------------------------------------------------- status registry and mapping
def test_registry_transitions_and_order():
    reg = S.StatusRegistry(clock=lambda: 42.0)
    reg.register("b", "B", "Flight", 0)
    reg.register("a", "A", "System", 0)
    assert [i.key for i in reg.snapshot()] == ["a", "b"]  # group order: System before Flight
    v = reg.version
    reg.report("a", S.OK, "x")
    it = reg.snapshot()[0]
    assert reg.version == v + 1 and it.state == S.OK and it.detail == "x" and it.ts == 42.0
    reg.report("a", S.OK, "x")  # unchanged: no-op
    reg.report("nope", S.OK)    # unknown key
    reg.report("a", "BOH")      # invalid state
    assert reg.version == v + 1
    reg.register("a", "other", "System", 5)  # double registration ignored
    assert reg.snapshot()[0].label == "A"


def test_standard_registry_is_in_english_with_three_groups():
    snap = make = S.make_registry().snapshot()
    assert {i.group for i in make} == {"System", "Connections", "Flight"}
    assert [i.key for i in snap if i.group == "Connections"] == ["sim", "simdll", "uplink", "page"]
    assert all(i.state == S.PENDING for i in snap)


def test_sim_and_dll_rows():
    assert S.sim_row({"sim_demo": True})[0] == S.OK
    assert S.sim_row({"sim_ok": None}) == (S.PENDING, "Starting")
    assert S.sim_row({"sim_ok": False}) == (S.PENDING, "Waiting for MSFS")  # grey: MSFS not started yet
    assert S.sim_row({"sim_ok": True}) == (S.OK, "Connected")
    assert S.sim_row({"sim_ok": False, "dll_missing": True})[0] == S.ERROR
    assert S.dll_row({"sim_ok": True}) == (S.OK, "Loaded")
    assert S.dll_row({"dll_missing": True, "dll_fetching": True}) == (S.PENDING, "Downloading...")
    assert S.dll_row({"dll_missing": True, "dll_msg": "no network"}) == (S.ERROR, "no network")
    assert S.dll_row({"sim_demo": True})[0] == S.PENDING


def test_uplink_rows():
    for state, color in (("unlinked", S.WARN), ("linking", S.WARN), ("connecting", S.WARN), ("connected", S.OK),
                         ("relink_needed", S.ERROR), ("stopped", S.ERROR)):
        assert S.uplink_row(state)[0] == color
    assert S.uplink_row("connecting", "server unreachable, retrying") == (S.WARN, "server unreachable, retrying")
    assert S.uplink_row("connected", "connected to the server") == (S.OK, "Connected")  # message only for non-ok states
    assert S.uplink_row(None, "server link disabled") == (S.PENDING, "server link disabled")
    assert S.uplink_row(None, "", enabled=False) == (S.PENDING, "Disabled")


def test_flight_rows():
    snap = {"phase": "flying", "fields": {"origin": "LIML/17", "destination": "LIRF"},
            "cl_phase": "cruise", "cl_phases": [{"id": "cruise", "name": "Cruise"}], "log": ["10:00:01  Gear up", "old"]}
    rows = S.flight_rows(snap)
    assert rows["flight"] == (S.OK, "In flight") and rows["plan"] == (S.OK, "LIML - LIRF")
    assert rows["checklist"] == (S.OK, "Cruise") and rows["captain"] == (S.OK, "10:00:01  Gear up")
    idle = S.flight_rows({"phase": "briefing", "fields": {}, "log": []})
    assert idle["flight"][1] == "Briefing" and idle["plan"] == (S.PENDING, "No plan") and idle["captain"][0] == S.PENDING


def test_real_state_snapshot_maps_to_rows():
    with tempfile.TemporaryDirectory() as d:
        st = State(pathlib.Path(d) / "c.json")
        rows = S.derive_rows(st.snapshot(), None, "")
        assert set(rows) >= {"sim", "simdll", "uplink", "flight", "plan", "checklist", "captain"}
        assert all(s in S.STATES and isinstance(t, str) for s, t in rows.values())
        reg = S.make_registry()
        S.apply_rows(reg, rows)
        assert {i.key: i.state for i in reg.snapshot()}["sim"] == S.PENDING


# ---------------------------------------------------------------- applog and paths
def _clean_logger():
    lg = applog.get_logger()
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    return lg


def test_applog_file_queue_and_idempotence():
    lg = _clean_logger()
    with tempfile.TemporaryDirectory() as d:
        q = queue.Queue(10)
        applog.setup_logging(d, gui_queue=q)
        applog.setup_logging(d, gui_queue=q)  # twice: no duplicated handlers
        logging.getLogger("captain.app").warning("hello %s", "pilot")
        text = pathlib.Path(d, applog.LOG_FILE).read_text(encoding="utf-8")
        assert text.count("hello pilot") == 1 and "WARNING" in text
        level, line = q.get_nowait()
        assert level == logging.WARNING and line.endswith("hello pilot") and q.empty()
        _clean_logger()


def test_gui_queue_never_blocks_when_full():
    lg = _clean_logger()
    q = queue.Queue(2)
    lg.addHandler(applog.GuiQueueHandler(q))
    lg.setLevel(logging.INFO)
    for i in range(5):
        lg.info("m%d", i)  # must not block nor raise
    assert q.qsize() == 2
    _clean_logger()


def test_stdio_redirect_goes_to_the_log():
    lg = _clean_logger()
    q = queue.Queue(10)
    lg.addHandler(applog.GuiQueueHandler(q))
    lg.setLevel(logging.INFO)
    old = sys.stdout, sys.stderr
    try:
        applog.redirect_stdio(lg)
        print("printed line")
        sys.stderr.write("trace")
        sys.stderr.write("back\n")
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr = old
    got = [q.get_nowait() for _ in range(q.qsize())]
    assert (logging.INFO, "printed line") in got and (logging.WARNING, "traceback") in got
    _clean_logger()


def test_paths_log_dir_is_writable_and_named():
    d = paths.log_dir()
    assert os.path.isdir(d) and os.access(d, os.W_OK)
    assert paths.APP_NAME == "VirtualCaptain"


# ---------------------------------------------------------------- the app (no window)
class Said:
    def __init__(self):
        self.items = []

    def __call__(self, text, on_start=None, shown=None):
        self.items.append(text)
        if on_start:
            on_start(shown or text)


def make_app(argv, d):
    said = Said()
    app = main.App(argv, state=State(pathlib.Path(d) / "c.json"), say=said)
    return app, said


def test_args_defaults_and_restart_argv():
    with tempfile.TemporaryDirectory() as d:
        app, _ = make_app(["--sim", "--test-links", "--port", "9001"], d)
        assert app.sim and app.port == 9001 and app.host == "127.0.0.1" and app.url == "http://127.0.0.1:9001"
        assert app.st.cl_trust_links and app.st.cl_demo
        app2, _ = make_app([], d)
        assert not app2.sim and app2.port == 8080 and not app2.st.cl_trust_links
        app.request_restart_with("--sim", False)
        assert app.restart_argv == ["--test-links", "--port", "9001"]
        app2.request_restart_with("--sim", True)
        assert app2.restart_argv == ["--sim"]
        assert main.toggled_argv(["--sim", "--sim"], "--sim", True) == ["--sim"]


def test_step_idle_in_briefing_does_nothing():
    with tempfile.TemporaryDirectory() as d:
        app, said = make_app(["--sim"], d)
        assert app.st.phase == "briefing" and app.step() == 0.3 and said.items == []


def test_sim_flight_runs_to_the_end_through_step():
    with tempfile.TemporaryDirectory() as d:
        app, said = make_app(["--sim"], d)
        app.st.set_phase("flying")
        delays = []
        for _ in range(200):
            delays.append(app.step())
            if "Simulated flight finished." in said.items:
                break
        assert said.items.count("Simulated flight finished.") == 1
        assert said.items[0] and app.st.phase == "flying"
        n = len(said.items)
        for _ in range(3):
            assert app.step() == 0.4  # finished: keeps flying with the last sample, says nothing more
        assert len(said.items) == n
        assert app.st.snapshot()["log"]  # on_start fed the state log


def test_run_loop_stops_on_event_and_survives_errors():
    with tempfile.TemporaryDirectory() as d:
        app, _ = make_app(["--sim"], d)
        calls = []

        def boom():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            app.stop.set()
            return 0
        app.step = boom
        sys_stderr = sys.stderr
        sys.stderr = open(os.devnull, "w")
        try:
            t = threading.Thread(target=app.run)
            t.start()
            t.join(10)
        finally:
            sys.stderr.close()
            sys.stderr = sys_stderr
        assert not t.is_alive() and len(calls) == 2 and app.last_error == "RuntimeError: boom"


def test_shutdown_is_idempotent_without_start():
    with tempfile.TemporaryDirectory() as d:
        app, _ = make_app(["--sim"], d)
        app.shutdown()
        app.shutdown()
        assert app.stop.is_set()


def test_login_code_is_never_logged():
    lg = _clean_logger()
    with tempfile.TemporaryDirectory() as d:
        q = queue.Queue(50)
        applog.setup_logging(d, gui_queue=q)
        app, _ = make_app(["--sim"], d)
        info = {"user_code": "ABCD-EFGH", "verification_uri": "https://x.test/link",
                "verification_uri_complete": "https://x.test/link?code=ABCD-EFGH"}
        out = sys.stdout
        sys.stdout = open(os.devnull, "w")  # headless is False: nothing must be printed either
        try:
            app.show_login(info)
        finally:
            sys.stdout.close()
            sys.stdout = out
        assert app.login_info["user_code"] == "ABCD-EFGH"
        for h in lg.handlers:
            h.flush()
        logged = pathlib.Path(d, applog.LOG_FILE).read_text(encoding="utf-8") + " ".join(t for _, t in list(q.queue))
        assert "ABCD" not in logged and "EFGH" not in logged and "link?code" not in logged and "Waiting for approval" in logged
        _clean_logger()


def test_login_text_has_link_code_and_last_four():
    t = main.login_text({"user_code": "ABCD-EFGH", "verification_uri": "https://x.test/link", "verification_uri_complete": ""})
    assert "https://x.test/link" in t and "ABCD-EFGH" in t and "(EFGH)" in t and "QR" not in t


def test_uplink_say_strips_prefix_and_refresh_clears_login():
    with tempfile.TemporaryDirectory() as d:
        app, _ = make_app(["--sim"], d)
        app._uplink_say("[uplink] server unreachable, retrying")
        assert app.uplink_msg == "server unreachable, retrying"
        app.login_info = {"user_code": "X"}
        app.refresh_status()  # no uplink: no pending approval
        assert app.login_info is None
        rows = {i.key: (i.state, i.detail) for i in app.registry.snapshot()}
        assert rows["uplink"] == (S.PENDING, "server unreachable, retrying")
        assert rows["sim"] == (S.OK, "Simulated flight (no simulator)") and rows["error"] == (S.PENDING, "-")
        app.last_error = "X: y"
        app.refresh_status()
        assert {i.key: i.state for i in app.registry.snapshot()}["error"] == S.ERROR


def test_start_reports_busy_port_in_english():
    import socket
    with tempfile.TemporaryDirectory() as d:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            app, _ = make_app(["--sim", "--port", str(s.getsockname()[1])], d)
            try:
                app.start()
                raise AssertionError("start should fail")
            except main.StartupError as e:
                assert "already running" in str(e) and "--port" in str(e)
        finally:
            s.close()


def test_simbrief_url():
    import simbrief as sb
    assert sb.briefing_url({"ofp_id": "194176147"}) == "https://dispatch.simbrief.com/briefing/194176147"
    for bad in (None, {}, {"ofp_id": None}, {"ofp_id": ""}, {"ofp_id": "12a"}, {"ofp_id": "1/../x"}, {"ofp_id": "1" * 21},
                {"ofp_id": True}, {"ofp_id": "12\n"}, "x"):
        assert sb.briefing_url(bad) == "https://dispatch.simbrief.com/home", bad
    assert sb.extract_plan({"params": {"request_id": "194176147"}})["ofp_id"] == "194176147"
    assert sb.extract_plan({"params": {"request_id": 194176147}})["ofp_id"] == "194176147"
    assert sb.extract_plan({"params": {"request_id": "abc"}})["ofp_id"] is None
    assert sb.extract_plan({})["ofp_id"] is None


def test_window_has_row_buttons_when_tk_available():
    try:
        import tkinter as tk
        import gui
        root = tk.Tk()
    except Exception:  # noqa: BLE001  (no tkinter or no display)
        print("skip window test")
        return
    try:
        opened = []
        gui.webbrowser.open = opened.append
        plan = {}
        reg = S.make_registry()
        win = gui.build_window(root, reg, queue.Queue(), {"page_url": lambda: "http://127.0.0.1:1/",
                                                           "simbrief_url": lambda: __import__("simbrief").briefing_url(plan)})
        root.update_idletasks()
        assert win.rows["page"].button is not None and win.rows["plan"].button is not None
        assert win.rows["flight"].button is None
        assert win.rows["plan"].button.cget("text") == "SimBrief"
        win.rows["plan"].button.invoke()
        plan["ofp_id"] = "42"
        win.rows["plan"].button.invoke()
        win.rows["page"].button.invoke()
        assert opened == ["https://dispatch.simbrief.com/home", "https://dispatch.simbrief.com/briefing/42", "http://127.0.0.1:1/"]
    finally:
        root.destroy()


def run_all():
    names = [n for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for n in names:
        globals()[n]()
        print("ok", n)


if __name__ == "__main__":
    run_all()
