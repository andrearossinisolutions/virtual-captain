import json
import pathlib
import sys
import tempfile

import checklist as C
import monitor as mon
from state import State

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = pathlib.Path.home() / "Outbox-Alma/Sviluppo/captain/checklist-fasi/checklist-sorgente.txt"
PHASES = ["sim-preparation", "preliminary-cockpit-prep", "cockpit-preparation", "fmgs-initialization-d-i-f-s-r-i-p",
          "before-start", "engine-startup", "after-engine-startup", "taxi-checklist", "before-takeoff", "takeoff",
          "after-takeoff", "climb", "cruise", "descent-preparation", "descent-checklist", "ils-approach",
          "non-precision-approach", "go-around", "landing", "after-landing", "parking", "securing-the-aircraft"]


def test_files_valid():
    cl = C.load()  # solleva ValueError se id duplicati, tipi, chiavi o etichette non validi
    assert cl.ids == PHASES
    assert cl.data["version"] == 1 and cl.data["source"]
    items = [i for p in cl.phases for s in p["subphases"] for i in s["items"]]
    assert len(items) == len({i["id"] for i in items}) and len(items) > 300
    assert {i["type"] for i in items} <= set(C.TYPES) and {"atc", "announce", "note", "action"} <= {i["type"] for i in items}
    assert all(i["id"].startswith(p["id"] + ".") for p in cl.phases for s in p["subphases"] for i in s["items"])
    pre = cl.phase("preliminary-cockpit-prep")["subphases"]
    assert [s["name"][:1] for s in pre] == ["1", "2", "3", "4"]
    assert cl.phase("sim-preparation")["subphases"][0]["name"] == ""
    assert all(l["verified"] is False for l in cl.links.values())
    assert any(i["id"] == "engine-startup.engine-idle-parameters_2" for i in items)


def test_links_reference_real_things():
    cl = C.load()
    keys = {i.key for i in mon.ITEMS}
    assert len(cl.links) > 40

    def walk(check):
        for c in check.get("all", [check]):
            assert c["key"] in keys
    for link in cl.links.values():
        walk(link["check"])
    assert not any("smok" in i for i in cl.links)  # No smoking non e' monitorato


def test_validation_rejects_bad_data():
    cl = C.load()
    base = json.loads(json.dumps(cl.data))
    first = base["phases"][0]["subphases"][0]["items"][0]["id"]
    bad_links = [{first: {"check": {"key": "nope", "eq": "ON"}, "verified": False}},
                 {first: {"check": {"key": "apu_master", "eq": "MAYBE"}, "verified": False}},
                 {"no.such.id": {"check": {"key": "apu_master", "eq": "ON"}, "verified": False}}]
    for links in bad_links:
        try:
            C.Checklist(base, links)
            assert False, links
        except ValueError:
            pass
    dup = json.loads(json.dumps(cl.data))
    dup["phases"][0]["subphases"][0]["items"].append(dict(dup["phases"][0]["subphases"][0]["items"][0]))
    try:
        C.Checklist(dup, {})
        assert False
    except ValueError:
        pass
    wrong = json.loads(json.dumps(cl.data))
    wrong["phases"][0]["subphases"][0]["items"][0]["type"] = "weird"
    try:
        C.Checklist(wrong, {})
        assert False
    except ValueError:
        pass


def test_source_in_sync():
    if not SOURCE.exists():
        return
    sys.path.insert(0, str(HERE / "tools"))
    import build_checklist
    assert build_checklist.parse(SOURCE.read_text(encoding="utf-8")) == C.load().data


def test_evaluate_phase():
    cl = C.load()
    labels = mon.clean_labels({"pack1": 1, "pack2": 0, "parking": None})
    res = cl.evaluate_phase("before-start", labels)
    rows = {i["id"]: i for s in res["subphases"] for i in s["items"]}
    assert rows["before-start.packs-pb"]["status"] == "na" and rows["before-start.packs-pb"]["detail"] == "unverified"
    assert rows["before-start.final-loadsheet"]["status"] == "none"
    assert res["bad"] == 0
    demo = cl.evaluate_phase("before-start", labels, demo=True)
    rows = {i["id"]: i for s in demo["subphases"] for i in s["items"]}
    assert rows["before-start.packs-pb"]["status"] == "bad" and "Pack 1: expected OFF, actual ON" == rows["before-start.packs-pb"]["detail"]
    assert rows["before-start.parking-brake"]["status"] == "na" and demo["bad"] == 1
    atc = cl.evaluate_phase("before-takeoff", labels, demo=True)
    assert all(i["status"] == "none" for s in atc["subphases"] for i in s["items"] if i["type"] != "action")


def test_cl_phase_state():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st = State(cfg)
    assert st.cl_phase == PHASES[0]
    st.set_cl_phase("climb")
    assert st.cl_phase == "climb" and json.loads(cfg.read_text())["checklist_phase"] == "climb"
    assert State(cfg).cl_phase == "climb"  # persistenza
    st.set_cl_phase(1)
    assert st.cl_phase == "cruise"
    st.set_cl_phase(-1)
    st.set_cl_phase(-1)
    assert st.cl_phase == "after-takeoff"
    for bad in ("nope", 0, 2, -2, True, None, 1.0, ["climb"]):
        try:
            st.set_cl_phase(bad)
            assert False, bad
        except ValueError:
            pass
    assert st.cl_phase == "after-takeoff"
    st.set_cl_phase(PHASES[0])
    st.set_cl_phase(-1)
    assert st.cl_phase == PHASES[0]  # limite inferiore: resta
    st.set_cl_phase(PHASES[-1])
    st.set_cl_phase(1)
    assert st.cl_phase == PHASES[-1]
    cfg.write_text(json.dumps({"checklist_phase": "sconosciuta"}))
    assert State(cfg).cl_phase == PHASES[0]
    snap = st.snapshot()
    assert [p["id"] for p in snap["cl_phases"]] == PHASES and snap["cl_phase"] == PHASES[-1] and "phase" in snap
    assert snap["checklist"]["phase"]["id"] == PHASES[-1]
    assert all(i["id"].startswith(PHASES[-1] + ".") for s in snap["checklist"]["subphases"] for i in s["items"])


def test_snapshot_uses_live_labels_and_demo():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.set_cl_phase("before-start")
    st.set_phase("flying")
    st.set_monitor({"pack1": 1, "pack2": 1, "beacon": 0})
    rows = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}
    assert rows["before-start.packs-pb"]["status"] == "na"  # verified:false in volo reale
    st.cl_demo = True
    snap = st.snapshot()["checklist"]
    assert snap["bad"] >= 2
    st.set_phase("briefing")
    assert st.labels == {}


def test_live_values():
    cl = C.load()
    labels = {"parking": "ON", "eng1": "OFF", "eng2": None}
    assert C.check_keys({"all": [{"key": "eng1", "eq": "OFF"}, {"key": "eng2", "eq": "OFF"}]}) == ["eng1", "eng2"]
    assert C.live_values({"key": "parking", "eq": "ON"}, labels) == [["Parking brake", "ON"]]
    assert C.live_values({"all": [{"key": "eng1", "eq": "OFF"}, {"key": "eng2", "eq": "OFF"}]}, labels) == \
        [["Engine master 1", "OFF"], ["Engine master 2", "n/d"]]
    assert C.live_values(None, labels) == []
    rows = {r["id"]: r for sp in cl.evaluate_phase("preliminary-cockpit-prep", labels)["subphases"] for r in sp["items"]}
    assert rows["preliminary-cockpit-prep.parking-brake"]["live"] == [["Parking brake", "ON"]]
    assert all(r["live"] == [] for r in rows.values() if r["type"] != "action")
    assert 'id="livesw"' in __import__("web").PAGE and 'localStorage.getItem("showLive")==="1"' in __import__("web").PAGE
    gear = {i.key: i for i in mon.ITEMS}["gear"]
    assert gear.lvar == "S_MIP_GEAR" and gear.std_fallback and gear.std


if __name__ == "__main__":
    test_files_valid()
    test_links_reference_real_things()
    test_validation_rejects_bad_data()
    test_source_in_sync()
    test_evaluate_phase()
    test_cl_phase_state()
    test_snapshot_uses_live_labels_and_demo()
    test_live_values()
    print("ok")
