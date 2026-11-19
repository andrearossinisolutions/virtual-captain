"""Latch delle voci soddisfatte (checklist_latch.py + integrazione in State/evaluate_phase)."""
from __future__ import annotations

import pathlib
import tempfile

from checklist_latch import Latch
from state import State

F1 = "takeoff.at-f-speed-flaps-1"  # gate: AGL >= accel_agl; ok con flap 1
F0 = "takeoff.at-s-speed-flaps-0"  # gate: dopo Flaps 1; ok con flap 0


class Clk:
    t = 1000.0

    def __call__(self):
        return self.t


def _state(phase="takeoff"):
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.fields.update({"ta": "6000", "thr_red_agl": "1500", "accel_agl": "1500"})
    st.clock = Clk()
    st.set_phase("flying")
    st.set_cl_phase(phase)
    return st


def _row(st, iid):
    return {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}[iid]


def _tick(st, flap, agl=2000, dt=0.5):
    st.clock.t += dt
    st.set_core({"on_ground": 0, "agl": agl, "alt": agl + 100, "vs": 800})
    st.set_monitor({"flap": flap})


def test_latch_class_basic():
    la = Latch()
    assert la.observe("a", True, 0) is False
    assert la.observe("a", True, 1.0) is False
    assert la.observe("a", False, 1.9) is False  # interrotto: il timer riparte
    assert la.observe("a", True, 2.0) is False
    assert la.observe("a", True, 3.0) is False
    assert la.observe("a", True, 4.0) is True and la.ids() == {"a"} and "a" in la
    assert la.observe("a", False, 5.0) is False and "a" in la  # congelata: il rosso non la toglie
    la.clear(["a", "zzz"])
    assert not la.ids()
    la.observe("b", True, 0)
    la.observe("b", True, 1)
    la.observe("b", True, 2.5)
    la.clear_all()
    assert la.ids() == frozenset()


def test_latch_to_dict_from_dict_tolerant():
    la = Latch()
    la.observe("x", True, 0)
    la.observe("x", True, 1)
    la.observe("x", True, 2.5)
    d = la.to_dict()
    assert d == {"latched": ["x"]}
    other = Latch()
    other.from_dict(d)
    assert other.ids() == {"x"}
    for bad in (None, 3, "x", [], {}, {"latched": None}, {"latched": "x"}, {"zzz": 1}):
        other.from_dict(bad)
        assert other.ids() == frozenset(), bad
    other.from_dict({"latched": ["a", 5, None, "", "b", ["c"]]})
    assert other.ids() == {"a", "b"}


def test_freezes_after_stable_ok_and_stays_green():
    st = _state()
    _tick(st, 1)
    assert _row(st, F1)["status"] == "ok" and not _row(st, F1).get("latched")  # ok ma non ancora stabile
    _tick(st, 1, dt=1.0)
    assert F1 not in st.latch.ids()
    _tick(st, 1, dt=1.5)  # 3 s di ok continuo
    assert F1 in st.latch.ids()
    _tick(st, 0)  # flap 0: grezzo rosso, resta verde
    r = _row(st, F1)
    assert r["status"] == "ok" and r["kind"] == "latched" and r["latched"] is True and "expected" in r["detail"]
    assert r["was_ok"] is True and r["live"] and r["live"][0][1] != "1"  # la riga live resta quella reale
    assert st.snapshot()["checklist"]["bad"] == sum(i["status"] == "bad" for s in st.snapshot()["checklist"]["subphases"] for i in s["items"])
    _tick(st, 1)
    assert _row(st, F1)["detail"] == "" and _row(st, F1)["latched"] is True and "was_ok" not in _row(st, F1)  # di nuovo ok: nessun "was OK"


def test_short_green_does_not_freeze():
    st = _state()
    _tick(st, 1)
    _tick(st, 1, dt=1.0)
    _tick(st, 0, dt=0.5)  # interrompe
    _tick(st, 1, dt=1.0)
    _tick(st, 1, dt=1.0)
    assert F1 not in st.latch.ids()
    _tick(st, 1, dt=1.5)
    assert F1 in st.latch.ids()


def test_closed_gate_never_freezes_and_snapshot_has_no_side_effects():
    st = _state()
    for _ in range(10):
        _tick(st, 1, agl=500, dt=1.0)  # gate AGL chiuso: wait, mai congelata
    assert _row(st, F1)["status"] == "wait" and not st.latch.ids()
    for _ in range(10):
        st.clock.t += 1
        st.snapshot()  # lo snapshot non aggiorna il latch
    assert not st.latch.ids()
    _tick(st, 1, dt=1.0)
    st.clock.t += 10
    for _ in range(5):
        st.snapshot()
    assert F1 not in st.latch.ids()


def _freeze(st, flap=1):
    _tick(st, flap)
    _tick(st, flap, dt=1.0)
    _tick(st, flap, dt=1.5)


def test_resets_like_gates():
    st = _state()
    _freeze(st)
    assert F1 in st.latch.ids()
    st.set_cl_phase("takeoff")  # riclicare la stessa fase
    st.set_cl_phase(1)
    st.set_cl_phase("climb")  # avanzare
    assert F1 in st.latch.ids()
    st.checklist_mode = "simplified"  # cambio modalita'
    st.checklist_mode = "complete"
    assert F1 in st.latch.ids()
    st.set_phase("briefing")  # End flight / briefing
    assert not st.latch.ids()
    st = _state()
    _freeze(st)
    st.set_phase("briefing")
    st.set_phase("flying")  # Briefing complete
    assert not st.latch.ids()


def test_partial_reset_on_phase_back():
    st = _state("takeoff")
    _freeze(st)
    st.latch._ids.update({"climb.landing-lights", "descent-checklist.landing-lights"})
    st.set_cl_phase("climb")
    st.set_cl_phase(-1)  # indietro verso after-takeoff: azzera after-takeoff e successive, non takeoff
    assert st.cl_phase == "after-takeoff"
    assert st.latch.ids() == {F1}


def test_load_plan_resets():
    st = _state()
    _freeze(st)
    assert F1 in st.latch.ids()
    st._reset_gates()  # chiamato da load_plan, set_phase
    assert not st.latch.ids()
    import inspect
    assert "self._reset_gates()" in inspect.getsource(State.load_plan)


def test_sample_gap_does_not_freeze():
    la = Latch()
    la.observe("a", True, 0)
    assert la.observe("a", True, 600) is False  # ritorno dopo minuti: riparte
    assert la.observe("a", True, 601) is False and la.observe("a", True, 602) is True
    st = _state()
    _tick(st, 1)
    _tick(st, 1, dt=1.0)
    st.set_cl_phase("climb")  # fuori fase: nessun campione per 60 s
    st.set_cl_phase("takeoff")
    _tick(st, 1, dt=60)
    assert F1 not in st.latch.ids()
    _tick(st, 1, dt=1.0)
    _tick(st, 1, dt=1.5)
    assert F1 in st.latch.ids()
