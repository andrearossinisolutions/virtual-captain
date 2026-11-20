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
                                                  "status": "ok", "kind": "none", "detail": "", "gate": "after TA 6000 ft", "latched": True, "was_ok": True, "bogus": "x", "raw": [["VAR", LAT]]}]}]},
        "clearance": {"c": "", "t": "1234"}, "log": ["12:00  hi"], "spoke_n": 3,
        # not allowed: must be dropped
        "simbrief": "SECRET-ID", "info": "x", "sim_error": "boom", "dll_msg": "m", "labels": [["a", "b"]], "now": 1.0,
        "spoke_ago": 2.0, "test_links": True, "rev": 5,
        "dll_confirm": "Download?", "modes": ["normal", "training"], "cl_modes": ["complete", "simplified"], "dll_missing": False,
    }


def test_allowlist_keeps_known_and_drops_the_rest():
    out = wire.sanitize_snapshot(base())
    assert set(out) <= set(wire.SNAPSHOT_FIELDS)
    for k in ("sim_error", "dll_msg", "dll_confirm", "now", "spoke_ago", "test_links"):
        assert k not in out
    # U3: the briefing view needs these (the user's own SimBrief ID goes back to the user's own page)
    assert out["simbrief"] == "SECRET-ID" and out["info"] == "x" and out["labels"] == [["a", "b"]] and out["rev"] == 5
    assert out["modes"] == ["normal", "training"] and out["cl_modes"] == ["complete", "simplified"] and out["dll_missing"] is False
    item = out["checklist"]["subphases"][0]["items"][0]
    assert "raw" not in item and item["live"] == [["l", "v"]]
    assert item["gate"] == "after TA 6000 ft" and item["latched"] is True and item["was_ok"] is True and "bogus" not in item  # gate (testo) ammesso, campi sconosciuti scartati
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


POS = "45.6301\u00b0 N  8.7202\u00b0 W"


def with_flight(rows, phase="flying"):
    d = base()
    d["phase"] = phase
    d["flight"] = rows
    return d


def test_position_row_is_kept_only_in_the_formatted_text_form():
    out = wire.sanitize_snapshot(with_flight([["Position", POS], ["Pitch", "1"], ["Latitude", "45.6301"]]))
    assert out["flight"] == [["Position", POS], ["Pitch", "1"]]
    assert wire.sanitize_snapshot(with_flight([["Position", "\u2014"]]))["flight"] == [["Position", "\u2014"]]
    assert "45.6301" not in json.dumps(wire.sanitize_snapshot(with_flight([["Latitude", "45.6301"]])))


@pytest.mark.parametrize("bad", [
    "45.6301 N  8.7202 W", "45.63\u00b0 N  8.72\u00b0 W", "91.0000\u00b0 N  8.7202\u00b0 W", "45.6301\u00b0 N  181.0000\u00b0 E",
    "45.6301\u00b0 X  8.7202\u00b0 W", "-45.6301\u00b0 N  8.7202\u00b0 W", POS + "\n", POS + " ", "x" * 41, "", 12.5, None,
    "45.6301\u00b0 N  8.7202\u00b0 W; 1.0000\u00b0 N  1.0000\u00b0 E"])
def test_malformed_position_row_is_dropped_without_error(bad):
    out = wire.sanitize_snapshot(with_flight([["Position", bad], ["Pitch", "1"]]))
    assert out["flight"] == [["Pitch", "1"]]


def test_position_row_with_odd_shape_or_label_is_dropped():
    rows = [["Position", POS, "extra"], ["position", POS], [" Position ", POS], ["Position"], "Position"]
    for r in rows:
        try:
            out = wire.sanitize_snapshot(with_flight([r, ["Pitch", "1"]]))
        except wire.SnapshotError:
            continue  # a non-row is a type error, never a leak
        assert out["flight"] == [["Pitch", "1"]], r


def test_position_never_leaves_in_the_briefing():
    out = wire.sanitize_snapshot(with_flight([["Position", POS], ["Pitch", "1"]], phase="briefing"))
    assert out["flight"] == [["Pitch", "1"]] and "45.6301" not in json.dumps(out)


def test_position_is_not_accepted_in_the_monitor_and_waypoints_have_no_coordinates():
    d = with_flight([])
    d["monitor"] = [["G", [["Position", POS], ["Latitude", "1"], ["GS", "1 kt"]]]]
    out = wire.sanitize_snapshot(d)
    assert out["monitor"] == [["G", [["GS", "1 kt"]]]] and "lat" not in json.dumps(out["plan"])


def test_extended_fields_are_bounded():
    for key, bad in (("simbrief", "x" * 41), ("info", "x" * 301), ("labels", [["a", "b"]] * 25), ("labels", [["a" * 33, "b"]]),
                     ("modes", ["a"] * 5), ("cl_modes", ["a" * 17]), ("rev", "1"), ("dll_missing", "yes")):
        d = base()
        d[key] = bad
        with pytest.raises(wire.SnapshotError):
            wire.sanitize_snapshot(d)


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
    assert wire.T_SNAPSHOT == "snapshot" and {"latitude", "longitude"} == set(wire.HIDDEN_ROW_LABELS)
    assert wire.MAX_SNAPSHOT_TARGET * 2 < wire.MAX_MSG


def test_wire_imports_only_the_standard_library():
    code = "import sys; import server.wire; bad=[m for m in ('fastapi','uvicorn','websockets','starlette') if m in sys.modules]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


# ---------------------------------------------------------------- deltas (U4)

import random

import snapshot


def _mutate(rng, d: dict) -> dict:
    d = copy.deepcopy(d)
    for _ in range(rng.randint(1, 4)):
        c = rng.randrange(9)
        if c == 0:
            d["phase"] = rng.choice(["flying", "briefing", "flying"])
        elif c == 1:
            d["flight"] = [r for r in ([["Position", POS]] if rng.random() < 0.6 else []) +
                           [["Ground speed", f"{rng.randint(100, 300)} kt"], ["Latitude", LAT]]]
        elif c == 2:
            d.pop(rng.choice(["wx_dest", "log", "plan", "clearance", "flight", "pills", "monitor", "spoke_n"]), None)
        elif c == 3:
            d["fields"] = dict(d.get("fields", {}), v1=str(rng.randint(100, 160)))
        elif c == 4:
            d["log"] = [f"12:{rng.randint(10, 59)}  line {i}" for i in range(rng.randint(0, 30))]
        elif c == 5:
            d["wx_dest"] = rng.choice([None, {"icao": "EDDF", "line": f"Wind {rng.randint(0, 359)}/12", "raw": "x"}])
        elif c == 6:
            d["spoke_n"] = rng.randint(0, 50)
        elif c == 7:
            d["plan"] = {"flight": "DLH1", "waypoints": [{"ident": f"W{i}", "alt": i} for i in range(rng.randint(0, 40))]}
        else:
            d["pills"] = {"gs": f"{rng.randint(100, 300)} kt"}
    return d


def test_full_and_full_plus_delta_give_the_same_state_on_random_sequences():
    rng = random.Random(20261120)
    raw = base()
    state = wire.sanitize_snapshot(raw)
    hashes = snapshot.section_hashes(state)
    sizes = []
    for step in range(300):
        raw = _mutate(rng, raw)
        if "phase" not in raw:
            raw["phase"] = "flying"
        full = wire.sanitize_snapshot(raw)
        # PC side: the delta is made from the sanitized view; server side: the same allowlist is applied again
        d, rm, hashes = snapshot.diff_sections(hashes, full)
        state = wire.apply_delta(state, json.loads(json.dumps(d)), rm)
        assert state == full, step
        sizes.append(len(json.dumps(d)))
    assert max(sizes) < len(json.dumps(full)) * 3 and min(sizes) <= 2


def test_delta_sections_are_sanitized_like_the_full_snapshot():
    state = wire.sanitize_snapshot(base())
    raw = copy.deepcopy(base())
    raw["log"] = ["x\x00y"]
    raw["plan"] = dict(raw["plan"], bogus=1)
    out = wire.apply_delta(state, {"log": raw["log"], "plan": raw["plan"], "evil": {"a": 1}, "sim_error": "boom"}, [])
    assert out["log"] == ["x y"] and "bogus" not in out["plan"] and "evil" not in out and "sim_error" not in out
    for bad in ({"fields": {"a": 5}}, {"log": ["x" * 301]}, {"plan": {"waypoints": [{}] * 301}}, {"flight": "no"}, {"monitor": [1]}):
        with pytest.raises(wire.SnapshotError):
            wire.apply_delta(state, bad, [])
    assert state == wire.sanitize_snapshot(base())  # the base is never modified


def test_delta_keeps_the_allowlist_for_position_and_coordinates():
    state = wire.sanitize_snapshot(with_flight([["Pitch", "1"]]))
    out = wire.apply_delta(state, {"flight": [["Position", POS], ["Latitude", LAT], ["Pitch", "2"]]}, [])
    assert out["flight"] == [["Position", POS], ["Pitch", "2"]]
    out = wire.apply_delta(state, {"flight": [["Position", "1.5 N 2.5 E"], ["Pitch", "2"]]}, [])
    assert out["flight"] == [["Pitch", "2"]]
    out = wire.apply_delta(state, {"monitor": [["g", [["Latitude", LAT], ["Position", POS], ["GS", "1", "", "1"]]]]}, [])
    assert out["monitor"] == [["g", [["GS", "1", "", "1"]]]]
    assert LAT not in json.dumps(out) and LON not in json.dumps(out)


def test_flight_follows_the_phase():
    state = wire.sanitize_snapshot(with_flight([["Position", POS], ["Pitch", "1"]]))
    assert state["flight"][0][0] == "Position"
    # the phase goes to the briefing: flight must travel with it, and the Position row is dropped
    with pytest.raises(wire.SnapshotError):
        wire.apply_delta(state, {"phase": "briefing"}, [])
    out = wire.apply_delta(state, {"phase": "briefing", "flight": [["Position", POS], ["Pitch", "1"]]}, [])
    assert out["phase"] == "briefing" and out["flight"] == [["Pitch", "1"]]
    assert wire.apply_delta(state, {"phase": "briefing"}, ["flight"]) == {k: v for k, v in dict(state, phase="briefing").items() if k != "flight"}
    # without a flight section in the base, a phase change alone is fine
    nf = {k: v for k, v in state.items() if k != "flight"}
    assert wire.apply_delta(nf, {"phase": "briefing"}, [])["phase"] == "briefing"
    # same phase: flight may stay untouched
    assert wire.apply_delta(state, {"spoke_n": 9}, [])["flight"] == state["flight"]
    # the sender always includes flight when the phase changes
    h = snapshot.section_hashes(state)
    nxt = dict(state, phase="briefing")
    d, rm, _ = snapshot.diff_sections(h, nxt)
    assert set(d) == {"phase", "flight"} and rm == []


def test_tombstones_remove_sections_and_phase_cannot_be_removed():
    state = wire.sanitize_snapshot(base())
    out = wire.apply_delta(state, {}, ["wx_dest", "log", "not_a_section"])
    assert "wx_dest" not in out and "log" not in out and out["plan"] == state["plan"]
    for rm in (["phase"], [5], "log"):
        with pytest.raises(wire.SnapshotError):
            wire.apply_delta(state, {}, rm)
    with pytest.raises(wire.SnapshotError):
        wire.apply_delta(state, {"log": []}, ["log"])  # changed and removed together: ambiguous
    d, rm, _ = snapshot.diff_sections(snapshot.section_hashes(state), {k: v for k, v in state.items() if k not in ("log", "wx_dest")})
    assert d == {} and rm == ["log", "wx_dest"]


def test_delta_limits_are_the_same_as_the_full_ones():
    state = wire.sanitize_snapshot(base())
    items = [{"id": f"a.{i}", "text": "t"} for i in range(60)]
    cl = {"bad": 0, "phase": {"id": "a", "name": "A"}, "subphases": [{"id": "x", "name": "", "items": items}, {"id": "y", "name": "", "items": items}]}
    with pytest.raises(wire.SnapshotError):
        wire.sanitize_snapshot(dict(base(), checklist=cl))
    with pytest.raises(wire.SnapshotError):
        wire.apply_delta(state, {"checklist": cl}, [])
    with pytest.raises(wire.SnapshotError):
        wire.apply_delta({}, {"log": []}, [])  # phase missing
    with pytest.raises(wire.SnapshotError):
        wire.apply_delta(state, [], [])


def test_worst_case_snapshot_and_its_delta_stay_under_the_target():
    raw = base()
    raw["plan"] = dict(raw["plan"], waypoints=[{"ident": f"W{i}", "via": "UL613", "alt": 30000} for i in range(300)])
    raw["log"] = ["x" * 300] * 30
    raw["monitor"] = [[f"g{i}", [["r" * 40, "v" * 100, "u", "w"] for _ in range(6)]] for i in range(24)]
    full = wire.sanitize_snapshot(raw)
    assert len(snapshot.dumps(full).encode()) <= wire.MAX_SNAPSHOT_TARGET
    d, rm, _ = snapshot.diff_sections({}, full)
    assert wire.apply_delta({"phase": "x"}, d, rm) == full and len(snapshot.dumps(d)) <= len(snapshot.dumps(full))


def test_welcome_announces_features_only_when_enabled():
    assert wire.welcome_message() == {"t": "welcome", "v": 1, "heartbeat": wire.HEARTBEAT, "max_msg": wire.MAX_MSG}
    assert "feat" not in wire.welcome_message(())
    assert wire.welcome_message((wire.FEAT_DELTA,))["feat"] == ["delta"]
    assert (wire.T_DELTA, wire.T_RESYNC, wire.FEAT_DELTA, wire.DELTA_VERSION) == ("delta", "resync", "delta", 1)
    import protocol
    assert protocol.T_DELTA == "delta" and protocol.apply_delta is wire.apply_delta
