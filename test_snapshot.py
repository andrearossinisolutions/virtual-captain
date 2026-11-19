"""Snapshot del volo dal vero State (volo demo): allowlist, dimensioni, posizione mai in uscita."""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest

import main as M
import snapshot
from server import wire
from state import State
from weather import Weather

LAT, LON = 12.3456, 65.4321  # sentinelle: mai nel JSON remoto


def make_state(big=False, mode="complete") -> State:
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json", weather=Weather(fetch=lambda icao: 1013.0))
    st.cl_demo = st.sim_demo = True
    st.checklist_mode = mode
    st.update({"origin": "LIMC/17R", "destination": "EDDF/25C", "atis_letter": "K", "atis_rwy": "25C", "datis_qnh": "1013"})
    st.plan = {"flight": "DLH1", "aircraft": "A320", "route": "SRN1R SRN UL613 " * (60 if big else 2), "sid": "SRN1R",
               "star": "X", "cruise_alt": 36000, "distance_nm": 300, "ete": "1h30", "alternate": "EDDK",
               "orig_metar": "x", "dest_metar": "y",
               "waypoints": [{"ident": "WP%d" % i, "via": "UL613", "alt": 30000, "lat": LAT, "lon": LON} for i in range(300 if big else 12)]}
    st.set_phase("flying")
    src = M.simulated()
    for _ in range(30):
        s, m = next(src)
    m = dict(m, lat=LAT, lon=LON)
    st.set_core(s)
    st.set_monitor(m)
    for i in range(30 if big else 3):
        st.add_log("12:00 Gear up %d" % i)
    return st


def test_real_snapshot_keeps_the_allowlist_and_drops_the_rest():
    snap = make_state().snapshot()
    view = snapshot.remote_view(snap)
    assert set(view) <= set(wire.SNAPSHOT_FIELDS)
    for k in ("sim_error", "dll_msg", "dll_confirm", "dll_fetching", "now", "spoke_ago", "test_links"):
        assert k in snap and k not in view, k
    for k in ("simbrief", "info", "labels", "modes", "cl_modes", "rev", "dll_missing"):  # briefing view on the site (U3)
        assert k in snap and k in view, k
    assert view["phase"] == "flying" and view["checklist"]["subphases"] and view["pills"]["gs"]


def test_position_never_leaves_the_pc():
    st = make_state(big=True)
    snap = st.snapshot()
    assert any(r[0] == "Position" for r in snap["flight"])  # il dato c'e' nello snapshot locale...
    view = snapshot.remote_view(snap)
    j = snapshot.dumps(view)
    for s in ("Latitude", "Longitude", '"lat"', '"lon"'):  # ...mai numerico/grezzo nel remoto
        assert s not in j, s
    assert not re.search(r"[:\[,]-?\d+\.\d{3,}[,\]}]", j)  # nessun numero JSON con decimali (coordinate grezze)
    # ...solo la riga Position gia' formattata (4 decimali, nessun URL) e solo in volo
    pos = [r for r in view["flight"] if r[0] == "Position"]
    assert pos and pos[0][1] == dict(snap["flight"])["Position"] and "http" not in j and "maps" not in j


def test_position_does_not_leave_in_the_briefing():
    st = make_state()
    st.set_phase("briefing")
    j = snapshot.dumps(snapshot.remote_view(st.snapshot()))
    assert "Position" not in j and "12.3456" not in j and "65.4321" not in j


def test_checklist_item_keys_are_all_covered_by_the_allowlist():
    """Ogni chiave di ogni voce (tutte le fasi, entrambe le modalita') e' nell'allowlist o e' nota come esclusa."""
    allowed = set(wire._ITEM[1])
    excluded = {"raw"}  # solo con --test-links
    for mode in ("complete", "simplified"):
        st = make_state(mode=mode)
        for p in st.cl.phases:
            try:
                st.set_cl_phase(p["id"])
            except ValueError:
                continue
            for sp in st.snapshot()["checklist"]["subphases"]:
                for it in sp["items"]:
                    assert set(it) <= allowed | excluded, set(it) - allowed - excluded
                    for f in it.get("inputs") or []:
                        assert set(f) <= set(wire._ITEM[1]["inputs"][1][1]), set(f)
                    for f in it.get("fields") or []:
                        assert set(f) <= set(wire._ITEM[1]["fields"][1][1]), set(f)


def test_every_phase_of_every_mode_passes_and_stays_under_the_limit():
    worst = 0
    for mode in ("complete", "simplified"):
        st = make_state(big=True, mode=mode)
        for p in st.cl.phases:
            try:
                st.set_cl_phase(p["id"])
            except ValueError:
                continue
            n = len(snapshot.dumps(snapshot.remote_view(st.snapshot())).encode())
            worst = max(worst, n)
            assert n <= wire.MAX_SNAPSHOT_TARGET, (mode, p["id"], n)
    assert worst > 10000  # il caso peggiore (300 waypoint, 30 righe di log, 34 voci) pesa davvero


def test_hard_limit_trims_waypoints_then_monitor(monkeypatch):
    st = make_state(big=True)
    monkeypatch.setattr(snapshot, "SOFT_LIMIT", 20000)
    v = snapshot.remote_view(st.snapshot())
    assert v["plan"]["waypoints"] == [] and v["monitor"]
    monkeypatch.setattr(snapshot, "SOFT_LIMIT", 3000)
    v = snapshot.remote_view(st.snapshot())
    assert v["plan"]["waypoints"] == [] and v["monitor"] == []


def test_same_state_gives_same_text():
    snap = make_state().snapshot()  # stesso dizionario: il meteo si riempie in background e cambierebbe tra due snapshot
    a = snapshot.dumps(snapshot.remote_view(snap))
    b = snapshot.dumps(snapshot.remote_view(snap))
    assert a == b and json.loads(a)


def test_a_bad_snapshot_raises_snapshot_error():
    snap = make_state().snapshot()
    snap["log"] = "not a list"
    with pytest.raises(wire.SnapshotError):
        snapshot.remote_view(snap)


def test_import_pulls_no_server_libraries_nor_simconnect():
    code = ("import sys; import protocol, uplink, secure_store, snapshot; "
            "print([m for m in ('fastapi','uvicorn','websockets','starlette','simconnect_backend','speak','pyttsx3') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=str(pathlib.Path(__file__).parent)).stdout.strip()
    assert out == "[]"
