"""Snapshot allowlist (server/wire.py): pure, stdlib only, no server needed."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from server import wire

LAT, LON = "11.1111", "22.2222"  # sentinels: must never appear in what the allowlist returns


def base() -> dict:
    return {
        "phase": "flying", "sim_ok": True, "sim_demo": False, "mode": "normal", "checklist_mode": "complete",
        "fields": {"origin": "LIMC/17R", "v1": "140"},
        "plan": {"flight": "DLH1", "aircraft": "A320", "route": "SRN1R SRN", "cruise_alt": 36000, "distance_nm": 300.5,
                 "ete": "1h30", "alternate": "EDDK", "sid": "SRN1R", "star": None,
                 "waypoints": [{"ident": "SRN", "via": "UL613", "alt": 30000, "lat": float(LAT), "lon": float(LON)}]},
        "rwy_bad": {"atis": False, "datis": True}, "atis_warn": {"atis": "", "datis": "METAR QNH 1009"},
        "wx_dep": None, "wx_dest": {"icao": "EDDF", "line": "Wind 270/12", "raw": "EDDF 270012KT"},
        "flight": [["Position", f"{LAT} N  {LON} E"], ["Ground speed", "165 kt"], ["Latitude", LAT]],
        "pills": {"gs": "165 kt"}, "pill_defs": [["gs", "GS"]],
        "monitor": [["Flight data", [["Latitude", LAT, "", LAT], ["Longitude", LON, "", LON], ["GS", "165 kt", "", "165"]]]],
        "cl_phases": [{"id": "a", "name": "A"}], "cl_phase": "a",
        "checklist": {"bad": 0, "phase": {"id": "a", "name": "A"}, "subphases": [
            {"id": "a.x", "name": "", "items": [{"id": "a.1", "text": "t", "response": "r", "type": "action", "live": [["l", "v"]],
                                                  "status": "ok", "kind": "none", "detail": "", "raw": [["VAR", LAT]]}]}]},
        "clearance": {"c": "", "t": "1234"}, "log": ["12:00  hi"], "spoke_n": 3,
        # not allowed: must be dropped
        "simbrief": "SECRET-ID", "info": "x", "sim_error": "boom", "dll_msg": "m", "labels": [["a", "b"]], "now": 1.0,
        "spoke_ago": 2.0, "test_links": True, "rev": 5, "thr_cal": {"when": "x"}, "calib": {"a": 1},
    }


def test_allowlist_keeps_known_and_drops_the_rest():
    out = wire.sanitize_snapshot(base())
    assert set(out) <= set(wire.SNAPSHOT_FIELDS)
    for k in ("simbrief", "info", "sim_error", "dll_msg", "labels", "now", "spoke_ago", "test_links", "rev", "thr_cal", "calib"):
        assert k not in out
    item = out["checklist"]["subphases"][0]["items"][0]
    assert "raw" not in item and item["live"] == [["l", "v"]]
    assert out["plan"]["star"] is None and out["wx_dep"] is None and out["spoke_n"] == 3


def test_position_and_coordinates_never_come_out():
    j = json.dumps(wire.sanitize_snapshot(base()))
    assert LAT not in j and LON not in j and "Position" not in j and "Latitude" not in j and "Longitude" not in j
    out = wire.sanitize_snapshot(base())
    assert out["flight"] == [["Ground speed", "165 kt"]]
    assert out["monitor"] == [["Flight data", [["GS", "165 kt", "", "165"]]]]
    assert out["plan"]["waypoints"] == [{"ident": "SRN", "via": "UL613", "alt": 30000}]


def test_hidden_labels_are_case_and_space_insensitive():
    d = base()
    d["flight"] = [[" position ", "x"], ["LATITUDE", "y"], ["Pitch", "1"]]
    assert wire.sanitize_snapshot(d)["flight"] == [["Pitch", "1"]]


def test_inputs_are_not_modified():
    d = base()
    before = copy.deepcopy(d)
    wire.sanitize_snapshot(d)
    assert d == before


def test_phase_is_required_and_input_must_be_an_object():
    d = base()
    del d["phase"]
    with pytest.raises(wire.SnapshotError):
        wire.sanitize_snapshot(d)
    for bad in (None, [], "x", 3):
        with pytest.raises(wire.SnapshotError):
            wire.sanitize_snapshot(bad)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(phase=3),
    lambda d: d.update(sim_demo="yes"),
    lambda d: d.update(spoke_n=True),
    lambda d: d.update(spoke_n=1.5),
    lambda d: d["plan"].update(route="x" * 1001),
    lambda d: d["plan"].update(waypoints=[{"ident": "A"}] * 301),
    lambda d: d["plan"].update(cruise_alt="high"),
    lambda d: d["plan"].update(distance_nm=float("nan")),
    lambda d: d.update(log=["x"] * 31),
    lambda d: d.update(log=["x" * 301]),
    lambda d: d.update(fields={"Bad Key": "x"}),
    lambda d: d.update(fields={"ok": "x" * 41}),
    lambda d: d.update(fields={"k%d" % i: "x" for i in range(41)}),
    lambda d: d.update(flight=[["a", "b"]] * 13),
    lambda d: d.update(flight=[["a"]]),
    lambda d: d.update(monitor=[["g", [["n", "v", "x", "r"]] * 151]]),
    lambda d: d.update(monitor=[["g", []]] * 25),
    lambda d: d.update(monitor=[["g", [["n", 5, "", ""]]]]),
    lambda d: d["checklist"]["subphases"][0]["items"][0].update(text=5),
    lambda d: d["checklist"]["subphases"][0].update(items=[{"id": "x"}] * 101),
    lambda d: d["checklist"].update(subphases=[{"id": "p", "name": "", "items": [{"id": "x"}] * 60}] * 2),
    lambda d: d.update(clearance={"c": "x" * 401}),
    lambda d: d.update(wx_dest={"icao": 4}),
    lambda d: d.update(rwy_bad={"atis": 1}),
])
def test_type_or_limit_violations_are_errors(mutate):
    d = base()
    mutate(d)
    with pytest.raises(wire.SnapshotError):
        wire.sanitize_snapshot(d)


def test_depth_and_pathological_structures_do_not_crash():
    deep = []
    for _ in range(5000):
        deep = [deep]
    d = base()
    d["log"] = deep
    with pytest.raises(wire.SnapshotError):
        wire.sanitize_snapshot(d)
    d = base()
    d["fields"] = {"a": {"b": {"c": 1}}}
    with pytest.raises(wire.SnapshotError):
        wire.sanitize_snapshot(d)


def test_control_characters_become_spaces_and_keys_stay_strict():
    d = base()
    d["log"] = ["a\x00b\nc\x1bd"]
    d["clearance"] = {"r": "SRN\nUL613"}
    out = wire.sanitize_snapshot(d)
    assert out["log"] == ["a b c d"] and out["clearance"]["r"] == "SRN UL613"


def test_unknown_keys_inside_known_sections_are_dropped_not_errors():
    d = base()
    d["rwy_bad"]["extra"] = {"deep": 1}
    d["wx_dest"]["lat"] = 1.0
    d["checklist"]["phase"]["secret"] = "x"
    out = wire.sanitize_snapshot(d)
    assert out["rwy_bad"] == {"atis": False, "datis": True} and "lat" not in out["wx_dest"] and "secret" not in out["checklist"]["phase"]


def test_output_is_json_serialisable_and_stable():
    a = json.dumps(wire.sanitize_snapshot(base()), sort_keys=True)
    b = json.dumps(wire.sanitize_snapshot(base()), sort_keys=True)
    assert a == b


def test_constants():
    assert wire.SNAPSHOT_VERSION == 1 and 1 in wire.SUPPORTED_SNAPSHOT_VERSIONS and wire.MAX_SNAPSHOT_TARGET == 65536
    assert wire.T_SNAPSHOT == "snapshot" and {"position", "latitude", "longitude"} == set(wire.HIDDEN_ROW_LABELS)
    assert wire.MAX_SNAPSHOT_TARGET * 2 < wire.MAX_MSG


def test_wire_imports_only_the_standard_library():
    code = "import sys; import server.wire; bad=[m for m in ('fastapi','uvicorn','websockets','starlette') if m in sys.modules]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
