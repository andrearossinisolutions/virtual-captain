"""Persistenza di sessione del volo (session.py + integrazione in State/Brain)."""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
import time

import session as sess
import state as S
from brain import Brain, Briefing
from state import State

T0 = 1_800_000_000.0  # 2027-01-15 ~08:00 UTC


class Wall:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _st(d, wall=None, debounce=0.05):
    wall = wall or Wall()
    return State(d / "cfg.json", session_path=d / "s.json", session_debounce=debounce, wall=wall), wall


def _prep(st):
    st.update({"origin": "LIMC/17R", "destination": "EDDF", "dep_time": "08:30", "ta": "6000", "v1": "140"})
    st.plan = {"route": "A B C", "sid": "X1", "waypoints": [{"ident": "AAA", "via": "Z", "alt": 100, "lat": 45.1, "lon": 8.2}], "lat": 1}
    st.plan_fields = {"v1": "140"}
    st.wt_unit = "kgs"
    st.info = "LIMC -> EDDF"
    st.set_clearance({"c": "EDDF", "t": "1234"})


def _ids(st, n=3):
    return [it["id"] for ph in st.cl.phases for sp in ph["subphases"] for it in sp["items"]][:n]


def _load(path):
    return json.loads(path.read_text())


@contextlib.contextmanager
def _fake_plan(plan=None):
    old = S.fetch_latest, S.to_briefing
    S.fetch_latest = lambda user: {}
    S.to_briefing = lambda ofp: {"origin": "LIPZ", "destination": "LIRF", "dep_time": "09:00", "generated": T0, "plan": plan or {"route": "N"}}
    try:
        yield
    finally:
        S.fetch_latest, S.to_briefing = old


def test_file_content_and_what_is_not_saved(tmp_path):
    st, _ = _st(tmp_path)
    _prep(st)
    st.set_phase("flying")
    st.set_core({"alt": 1})
    st.set_monitor({"flap": 1, "lat": 45.0})
    st.flush_session()
    d = _load(tmp_path / "s.json")
    assert d["v"] == 1 and d["phase"] == "flying" and d["flight_id"] and d["fields"]["origin"] == "LIMC/17R"
    assert d["clearance"]["t"] == "1234" and d["plan_fields"] == {"v1": "140"} and d["wt_unit"] == "kgs"
    assert d["plan"]["waypoints"] == [{"ident": "AAA", "via": "Z", "alt": 100}] and "lat" not in d["plan"]
    for k in ("monitor", "pills", "raw", "log", "labels", "core", "token", "simbrief", "mode", "calibration", "sim_backend"):
        assert k not in d
    assert "latitude" not in json.dumps(d) and '"lon"' not in json.dumps(d)
    assert set(d["brain"]) == {"done", "roll_done", "armed"} and set(d["latch"]) == {"latched"}


def test_atomic_write_mode_and_no_tmp_left(tmp_path):
    p = tmp_path / "s.json"
    assert sess.write_atomic(p, {"v": 1})
    assert not (tmp_path / "s.json.tmp").exists()
    if os.name != "nt":
        assert (p.stat().st_mode & 0o777) == 0o600
    assert not sess.write_atomic(tmp_path / "nodir" / "s.json", {"v": 1})  # errore: False, nessuna eccezione
    assert not sess.write_atomic(p, {"x": {1, 2}})  # non serializzabile: il file buono resta
    assert _load(p) == {"v": 1} and not (tmp_path / "s.json.tmp").exists()


def test_debounce_coalesces_writes(tmp_path):
    st, _ = _st(tmp_path)
    st.update({"ta": "1"})
    assert not (tmp_path / "s.json").exists()  # non subito
    for i in range(5):
        st.update({"ta": str(i)})
    time.sleep(0.4)
    assert _load(tmp_path / "s.json")["fields"]["ta"] == "4"


def test_no_session_path_writes_nothing(tmp_path):
    st = State(tmp_path / "cfg.json")  # --sim e test: nessuna persistenza
    st.update({"ta": "1"})
    st.set_phase("flying")
    st.flush_session()
    assert st._timer is None and list(tmp_path.glob("*session*")) == [] and not (tmp_path / "s.json").exists()


def test_restore_flying_with_latch_gates_brain(tmp_path):
    st, w = _st(tmp_path)
    _prep(st)
    st.set_phase("flying")
    st.set_cl_phase("takeoff")
    x, y, z = _ids(st)
    st.gates["open"].add(x)
    st.gates["done"].add(y)
    st.latch._ids.add(z)
    st.latch._since["a"] = 1.0  # timer: non si salvano
    st.set_brain({"done": ["posrate"], "roll_done": ["v1"], "armed": True})
    st.flush_session()
    assert "since" not in (tmp_path / "s.json").read_text()
    w.t += 600
    st2, _ = _st(tmp_path, w)
    assert st2.phase == "flying" and st2.cl_phase == "takeoff" and st2.resumed and st2.restored_flying
    assert st2.fields["origin"] == "LIMC/17R" and st2.fields["runway"] == "17R"
    assert st2.clearance["t"] == "1234" and st2.plan["route"] == "A B C" and st2.wt_unit == "kgs"
    assert st2.gates == {"open": {x}, "done": {y}}
    assert st2.latch.ids() == frozenset({z}) and not st2.latch._since
    assert st2.brain_saved == {"done": ["posrate"], "roll_done": ["v1"], "armed": True}
    assert st2.flight_id == st.flight_id
    assert "Flight restored" in st2.log[0] and st2.spoke_n == 0 and st2.spoke_at is None  # nessuna frase


def test_restore_briefing_phase_not_resumed(tmp_path):
    st, w = _st(tmp_path)
    _prep(st)
    st.flush_session()
    st2, _ = _st(tmp_path, w)
    assert st2.phase == "briefing" and not st2.resumed and not st2.restored_flying and st2.fields["destination"] == "EDDF"


def test_not_same_flight_rules(tmp_path):
    st, w = _st(tmp_path)
    _prep(st)
    st.flush_session()
    d = _load(tmp_path / "s.json")
    assert sess.same_flight(d, w.t + 3600)
    assert not sess.same_flight(d, w.t + 6 * 3600 + 1)  # saved_at troppo vecchio
    assert not sess.same_flight(d, w.t - 10)  # nel futuro (orologio spostato)
    # briefing salvato alle 20:00 con dep 08:30 del giorno dopo, riavviato alle 20:45: si ripristina
    eve = {**d, "saved_at": T0 + 12 * 3600, "fields": {**d["fields"], "dep_time": "08:30"}}
    assert sess.same_flight(eve, T0 + 12 * 3600 + 45 * 60)
    # volo lungo (partenza 08:30, ora 22:00) con saved_at fresco per il battito: si ripristina
    long_ = {**d, "phase": "flying", "saved_at": T0 + 14 * 3600 - 30}
    assert sess.same_flight(long_, T0 + 14 * 3600)
    for bad in (None, [], {"saved_at": "x"}, {**d, "phase": "landed"}, {**d, "saved_at": True}):
        assert not sess.same_flight(bad, w.t)
    st2, _ = _st(tmp_path, Wall(w.t + 7 * 3600))
    assert st2.fields["origin"] == "" and not st2.resumed  # file vecchio: stato pulito


def test_write_failure_backs_off_and_leaves_good_file(tmp_path):
    st, w = _st(tmp_path, debounce=60)
    st.update({"ta": "1"})
    st.flush_session()
    assert _load(tmp_path / "s.json")["fields"]["ta"] == "1"
    st.session_path = tmp_path / "nodir" / "s.json"  # scrittura impossibile
    w.t += 5
    st.update({"ta": "2"})
    st.flush_session()  # nessuna eccezione
    assert not st._dirty and st._written == w.t  # niente tentativi continui: ritenta al prossimo cambio/battito
    st.session_path = tmp_path / "s.json"
    assert _load(tmp_path / "s.json")["fields"]["ta"] == "1"


def test_permission_error_on_replace_is_retried(tmp_path):
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        return real(a, b)
    os.replace = flaky
    try:
        assert sess.write_atomic(tmp_path / "s.json", {"v": 1})
    finally:
        os.replace = real
    assert len(calls) == 3 and _load(tmp_path / "s.json") == {"v": 1}


def test_stale_tmp_is_overwritten_and_removed(tmp_path):
    (tmp_path / "s.json.tmp").write_text("garbage")
    assert sess.write_atomic(tmp_path / "s.json", {"v": 1})
    assert not (tmp_path / "s.json.tmp").exists() and _load(tmp_path / "s.json") == {"v": 1}
    (tmp_path / "s.json.tmp").write_text("x")
    sess.delete(tmp_path / "s.json")
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "s.json.tmp").exists()


def test_strip_plan_nested_uppercase_keys():
    p = {"LAT": 1, "waypoints": [{"ident": "A", "Lon": 2, "pos": {"Latitude": 3, "x": 4}}], "o": object()}
    out = sess.strip_plan(p)
    assert out == {"waypoints": [{"ident": "A", "pos": {"x": 4}}], "o": None}


def test_end_flight_wins_over_concurrent_flush(tmp_path):
    import threading
    st, _ = _st(tmp_path, debounce=60)
    st.set_phase("flying")
    st.update({"ta": "1"})
    gate, started = threading.Event(), threading.Event()
    real = sess.write_atomic

    def slow(path, data):
        started.set()
        gate.wait(2)  # scrittura "lenta" fuori dal lock
        return real(path, data)
    sess.write_atomic = slow
    try:
        th = threading.Thread(target=st.flush_session)
        th.start()
        assert started.wait(2)
        assert st.lock.acquire(timeout=1)  # lo State non e' bloccato dall'I/O
        st.lock.release()
        st.set_phase("briefing")  # End flight mentre si scrive
        gate.set()
        th.join(3)
    finally:
        sess.write_atomic = real
    assert not (tmp_path / "s.json").exists()


def test_unknown_gate_and_latch_ids_are_dropped(tmp_path):
    st, w = _st(tmp_path)
    st.set_phase("flying")
    real = next(it["id"] for ph in st.cl.phases for sp in ph["subphases"] for it in sp["items"])
    st.gates["open"].update({real, "ghost.id"})
    st.latch._ids.update({real, "ghost.id"})
    st._mark_session()
    st.flush_session()
    st2, _ = _st(tmp_path, w)
    assert st2.gates["open"] == {real} and st2.latch.ids() == frozenset({real})


def test_tolerant_read(tmp_path):
    p = tmp_path / "s.json"
    for txt in ("", "not json", "[]", '{"v": 99}', '{"v": "1"}', '{"v": true}', "null"):
        p.write_text(txt)
        assert sess.read(p) is None
        st = State(tmp_path / "c.json", session_path=p, wall=Wall())
        assert st.phase == "briefing" and st.fields["origin"] == ""
    assert sess.read(tmp_path / "missing.json") is None
    # valori mancanti o di tipo sbagliato => vuoti, nessuna eccezione
    p.write_text(json.dumps({"v": 1, "saved_at": T0, "phase": "flying", "fields": {"origin": "LIMC", "ta": [1], "v1": True},
                             "plan": "x", "plan_fields": 3, "clearance": {"t": 9, "c": "EDDF"}, "cl_phase": "nope",
                             "brain": {"done": "x", "armed": "yes"}, "gates": {"open": [1, "a"], "done": None},
                             "latch": {"latched": "zz"}, "last_seen": "q", "flight_id": 7, "wt_unit": "stones"}))
    st = State(tmp_path / "c.json", session_path=p, wall=Wall(T0 + 5))
    assert st.phase == "flying" and st.fields["origin"] == "LIMC" and st.fields["ta"] == "" and st.fields["v1"] == ""
    assert st.plan == {} and st.plan_fields == {} and st.clearance["t"] == "" and st.clearance["c"] == "EDDF"
    assert st.cl_phase in st.cl.ids and st.brain_saved == {"done": [], "roll_done": [], "armed": False}
    assert st.gates == {"open": set(), "done": set()} and st.latch.ids() == frozenset()
    assert st.last_seen is None and st.flight_id == "" and st.wt_unit == ""


def test_end_flight_deletes_and_does_not_rewrite(tmp_path):
    st, w = _st(tmp_path)
    _prep(st)
    st.set_phase("flying")
    st.flush_session()
    assert (tmp_path / "s.json").exists()
    st.set_phase("briefing")  # End flight
    assert not (tmp_path / "s.json").exists() and st.flight_id == ""
    st.update({"ta": "5000"})  # nessuna riscrittura dopo la fine
    st.flush_session()
    time.sleep(0.2)
    assert not (tmp_path / "s.json").exists()
    st2, _ = _st(tmp_path, w)
    assert st2.fields["origin"] == ""  # il volo dopo non e' pre-compilato


def test_pending_timer_cancelled_by_end_flight(tmp_path):
    st, _ = _st(tmp_path)
    st.set_phase("flying")
    st.update({"ta": "1"})
    st.end_flight_session()
    time.sleep(0.3)
    assert not (tmp_path / "s.json").exists()


def test_load_plan_is_a_new_flight_and_latch_follows_the_file(tmp_path):
    st, _ = _st(tmp_path)
    _prep(st)
    st.set_phase("flying")
    st.latch._ids.add("a")
    st.flush_session()
    old = st.flight_id
    with _fake_plan():
        st.load_plan("user")
    assert st.flight_id and st.flight_id != old and st.latch.ids() == frozenset() and not st.resumed
    st.flush_session()
    d = _load(tmp_path / "s.json")
    assert d["fields"]["origin"] == "LIPZ" and d["latch"]["latched"] == [] and d["flight_id"] == st.flight_id


def test_load_plan_after_end_flight_writes_again(tmp_path):
    st, _ = _st(tmp_path)
    st.set_phase("flying")
    st.set_phase("briefing")
    with _fake_plan():
        st.load_plan("u")
    st.flush_session()
    assert (tmp_path / "s.json").exists()


def test_gate_change_and_heartbeat(tmp_path):
    st, w = _st(tmp_path, debounce=60)  # il timer non scatta: si osserva solo il flag
    st.set_phase("flying")
    st.set_core({"on_ground": 0, "agl": 10, "alt": 10, "vs": 0})
    st.set_monitor({"flap": 0})  # primo campione live: _written=0 -> battito
    st.flush_session()
    assert _load(tmp_path / "s.json")["last_seen"] == w.t
    w.t += 30
    st.set_monitor({"flap": 0})
    assert not st._dirty  # nessun cambio e battito non scaduto: nessuna scrittura
    w.t += 31
    st.set_monitor({"flap": 0})
    assert st._dirty  # battito: saved_at resta fresco in un volo lungo
    st.flush_session()
    assert _load(tmp_path / "s.json")["saved_at"] == w.t


def test_real_latch_freeze_is_saved_and_restored(tmp_path):
    class Clk:
        t = 1000.0

        def __call__(self):
            return self.t
    st, w = _st(tmp_path, debounce=60)
    st.fields.update({"ta": "6000", "thr_red_agl": "1500", "accel_agl": "1500"})
    st.clock = Clk()
    st.set_phase("flying")
    st.set_cl_phase("takeoff")
    st.flush_session()
    assert _load(tmp_path / "s.json")["latch"]["latched"] == []
    for dt in (0.5, 1.0, 1.5):  # stesso schema di test_checklist_latch._freeze: voce flap 1 stabile -> latch
        st.clock.t += dt
        st.set_core({"on_ground": 0, "agl": 2000, "alt": 2100, "vs": 800})
        st.set_monitor({"flap": 1})
    assert st.latch.ids() and st._dirty  # il congelamento segna il file da riscrivere
    st.flush_session()
    saved = _load(tmp_path / "s.json")
    assert saved["latch"]["latched"] and saved["gates"]["open"]
    w.t += 120
    st2, _ = _st(tmp_path, w)
    assert st2.latch.ids() == st.latch.ids() and st2.gates["open"] == st.gates["open"] and st2.gates["done"] == st.gates["done"]


def test_sim_stopped_and_flight_over():
    core = {"on_ground": 1, "ias": 0}
    raw = {"eng1": 0, "eng2": 0, "parking": 1}
    assert sess.stopped_on_ground(core, raw) and sess.flight_over(core, raw)
    assert sess.stopped_on_ground(core, {**raw, "parking": 0}) and not sess.flight_over(core, {**raw, "parking": 0})
    assert not sess.stopped_on_ground({"on_ground": 0, "ias": 0}, raw)
    assert not sess.stopped_on_ground(core, {**raw, "eng2": 1})
    assert not sess.stopped_on_ground({"on_ground": 1}, raw) and not sess.stopped_on_ground(None, None)
    assert not sess.flight_over(core, {"eng1": 0, "eng2": 0})  # parking sconosciuto: nel dubbio no


def test_brain_to_from_dict():
    b = Brain(Briefing())
    b.done, b.roll_done, b.armed = {"ta", "posrate"}, {"v1"}, True
    d = b.to_dict()
    assert d == {"done": ["posrate", "ta"], "roll_done": ["v1"], "armed": True}
    b2 = Brain(Briefing())
    b2.from_dict(d)
    assert b2.done == b.done and b2.roll_done == b.roll_done and b2.armed and not b2.resumed
    b2.from_dict(None)
    assert b2.done == set() and b2.roll_done == set() and not b2.armed
    b2.from_dict({"done": [1, "a"], "armed": 1})
    assert b2.done == {"a"} and not b2.armed
