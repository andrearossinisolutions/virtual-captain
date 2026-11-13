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
    verified = {"apu_bleed", "spoilers", "parking", "eng1", "eng2", "engmode", "gear", "apu_master", "navlogo", "strobe", "flap", "wx_sys"}
    for k, l in cl.links.items():
        v = {c["key"] for c in l["check"].get("all", [l["check"]]) if "key" in c}
        if k.startswith("preliminary-cockpit-prep."):
            assert l["verified"] is bool(v & verified) and (v <= verified or not l["verified"]), k
    assert [k for k, l in cl.links.items() if l["verified"] and not k.startswith("preliminary-cockpit-prep.")
            and "apu_bleed" not in json.dumps(l["check"]) and "spoilers" not in json.dumps(l["check"])] == [
        "ils-approach.flaps-full", "non-precision-approach.flaps-full"]
    assert any(i["id"] == "engine-startup.engine-idle-parameters_2" for i in items)


def test_links_reference_real_things():
    cl = C.load()
    keys = {i.key for i in mon.ITEMS}
    assert len(cl.links) > 40

    def walk(check):
        for c in check.get("all", [check]):
            assert all(k in keys for k in c["show"]) if "show" in c else c["key"] in keys
    for link in cl.links.values():
        walk(link["check"])


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


def test_simplified():
    cl = C.load()
    assert cl.excluded and all(i in {it["id"] for p in cl.phases for sp in p["subphases"] for it in sp["items"]} for i in cl.excluded)
    # default complete: identico a oggi
    assert cl.evaluate_phase("takeoff", {}) == cl.evaluate_phase("takeoff", {}, simplified=False)
    ids = lambda r: [i["id"] for sp in r["subphases"] for i in sp["items"]]
    full, simp = cl.evaluate_phase("takeoff", {}), cl.evaluate_phase("takeoff", {}, simplified=True)
    assert set(ids(full)) - set(ids(simp)) == {i for i in cl.excluded if i.startswith("takeoff.")} and "takeoff.chrono" not in ids(simp)
    assert "takeoff.landing-gear" in ids(simp) and "takeoff.takeoff-n1" in ids(full)
    assert all(sp["items"] for sp in simp["subphases"]) and len(simp["subphases"]) < len(full["subphases"])  # sottofasi vuote scartate
    fa, sa = cl.evaluate_phase("after-takeoff", {}), cl.evaluate_phase("after-takeoff", {}, simplified=True)
    assert set(ids(fa)) - set(ids(sa)) == {i for i in cl.excluded if i.startswith("after-takeoff.")}  # Engine Mode Selector (NORM)
    # bad conta solo le voci visibili; link su voce esclusa ignorato
    data = json.loads(json.dumps(cl.data))
    links = {"before-start.slides": {"check": {"key": "parking", "eq": "ON"}, "verified": True},
             "before-start.packs-pb": {"check": {"key": "pack1", "eq": "ON"}, "verified": True}}
    labels = {"parking": "OFF", "pack1": "OFF"}
    c2 = C.Checklist(data, links, {"version": 1, "exclude": ["before-start.slides"]})
    assert c2.evaluate_phase("before-start", labels)["bad"] == 2
    r = c2.evaluate_phase("before-start", labels, simplified=True)
    assert r["bad"] == 1 and "before-start.slides" not in ids(r)
    for bad in ({"exclude": ["no.such"]}, {"exclude": ["takeoff.chrono", "takeoff.chrono"]}, {"exclude": "x"}):
        try:
            C.Checklist(data, {}, bad)
            assert False, bad
        except ValueError:
            pass


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


def test_simplified_hides_empty_phases():
    cl = C.load()
    hidden = [p for p in cl.ids if p not in cl.visible_ids(True)]
    assert hidden == ["sim-preparation"] and cl.visible_ids(False) == cl.ids == PHASES
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st = State(cfg)
    assert "sim-preparation" in [p["id"] for p in st.snapshot()["cl_phases"]]  # complete: tutte
    st.update({"checklist_mode": "simplified"})
    snap = st.snapshot()
    assert "sim-preparation" not in [p["id"] for p in snap["cl_phases"]] and len(snap["cl_phases"]) == len(PHASES) - 1
    assert st.cl_phase == snap["cl_phases"][0]["id"] != "sim-preparation"  # fase corrente nascosta -> prima visibile
    assert json.loads(cfg.read_text())["checklist_phase"] == st.cl_phase  # persistenza
    first = st.cl_phase
    st.set_cl_phase(-1)
    assert st.cl_phase == first
    try:
        st.set_cl_phase("sim-preparation")
        assert False
    except ValueError:
        pass
    st.update({"checklist_mode": "complete"})
    st.set_cl_phase("sim-preparation")
    st.set_cl_phase(1)
    assert st.cl_phase == first  # in complete la fase c'e'
    # salto in avanti/indietro: simulo una fase nascosta in mezzo
    st.cl.excluded = frozenset(i["id"] for sp in st.cl.phase("climb")["subphases"] for i in sp["items"])
    st.update({"checklist_mode": "simplified"})
    st.set_cl_phase("after-takeoff")
    st.set_cl_phase(1)
    assert st.cl_phase == "cruise"
    st.set_cl_phase(-1)
    assert st.cl_phase == "after-takeoff"
    # fase ricordata nel JSON ma nascosta -> prima visibile
    cfg.write_text(json.dumps({"checklist_mode": "simplified", "checklist_phase": "sim-preparation"}))
    assert State(cfg).cl_phase == first
    cfg.write_text(json.dumps({"checklist_mode": "complete", "checklist_phase": "sim-preparation"}))
    assert State(cfg).cl_phase == "sim-preparation"


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


def test_trust_links_and_raw():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.set_cl_phase("before-start")
    st.set_phase("flying")
    st.set_monitor({"pack1": 1, "pack2": 1, "beacon": 0})
    snap = st.snapshot()
    assert snap["test_links"] is False
    assert all("raw" not in i for s in snap["checklist"]["subphases"] for i in s["items"])  # normale: nessun raw
    assert snap["checklist"]["bad"] == 0
    st.cl_trust_links = True
    snap = st.snapshot()
    rows = {i["id"]: i for s in snap["checklist"]["subphases"] for i in s["items"]}
    assert snap["test_links"] is True and snap["checklist"]["bad"] >= 2
    assert rows["before-start.packs-pb"]["status"] == "bad"
    raw = rows["before-start.packs-pb"]["raw"]
    assert raw and all(len(r) == 2 for r in raw) and any(r[1] == "1" for r in raw)
    assert all("raw" not in i for i in rows.values() if i["live"] == [])  # solo voci con link


def test_new_links_show_optional():
    cl = C.load()
    P = "preliminary-cockpit-prep."
    for k in ("windshear-pws-sw", "gain-knob", "wx-mode-selector", "battery-pb-1-2", "external-power-pb",
              "crew-supply-pb", "all-ir-modes"):
        assert cl.links[P + k]["verified"] is False, k
    assert C.check_keys(cl.links[P + "battery-pb-1-2"]["check"]) == ["bat1", "bat2"]
    assert C.check_keys(cl.links[P + "all-ir-modes"]["check"]) == ["ir1", "ir2", "ir3"]
    assert cl.links[P + "gain-knob"]["check"] == {"show": ["wx_gain"]}
    labels = mon.clean_labels({"wx_gain": 0.5, "wx_mode": 2, "flap": 3})
    res = cl.evaluate_phase("preliminary-cockpit-prep", labels, raw={"wx_gain": 0.5, "wx_mode": 2})
    rows = {i["id"]: i for s in res["subphases"] for i in s["items"]}
    assert rows[P + "gain-knob"]["status"] == "none" and rows[P + "gain-knob"]["live"] == [["WX Gain knob", "0.5"]]
    assert rows[P + "gain-knob"]["raw"] == [["A_WR_GAIN", "0.5"]] and res["bad"] == 0
    assert rows[P + "wx-mode-selector"]["live"] == [["WX Mode", "2"]]
    assert rows[P + "gain-knob"]["show"] is True and "show" not in rows[P + "battery-pb-1-2"]
    # ATC Transponder: show su squawk, intero senza decimali, n/d se manca
    assert cl.links["before-start.atc-transponder"]["check"] == {"show": ["squawk"]}
    assert mon.BY_KEY["squawk"].lvar == "N_FREQ_XPDR_SELECTED" if hasattr(mon, "BY_KEY") else True
    for raw, txt in ((2000.0, "2000"), (None, "n/d")):
        r = cl.evaluate_phase("before-start", mon.clean_labels({"squawk": raw}))
        row = {i["id"]: i for s in r["subphases"] for i in s["items"]}["before-start.atc-transponder"]
        assert row["live"] == [["Squawk code", txt]] and row["status"] == "none" and row["show"] is True
    page = __import__("web").PAGE
    assert '"now nv","NOW: "' in page and ".now.nv{color:var(--mut)}" in page and "i.raw,i.show" in page
    for ph in ("ils-approach", "non-precision-approach"):
        assert cl.links[ph + ".flaps-full"]["optional"] is True and cl.links[ph + ".flaps-full"]["verified"] is True
        for raw, st in ((4, "ok"), (3, "opt")):
            r = cl.evaluate_phase(ph, mon.clean_labels({"flap": raw}))
            row = {i["id"]: i for s in r["subphases"] for i in s["items"]}[ph + ".flaps-full"]
            assert row["status"] == st and r["bad"] == 0, (ph, raw)
    for bad in ({"check": {"show": ["nope"]}, "verified": False}, {"check": {"show": []}, "verified": False},
                {"check": {"key": "flap", "eq": "FULL"}, "optional": "yes", "verified": True}):
        try:
            C.Checklist(cl.data, {P + "gain-knob": bad})
            assert False, bad
        except ValueError:
            pass


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
    test_new_links_show_optional()
    test_validation_rejects_bad_data()
    test_source_in_sync()
    test_evaluate_phase()
    test_simplified()
    test_cl_phase_state()
    test_simplified_hides_empty_phases()
    test_snapshot_uses_live_labels_and_demo()
    test_trust_links_and_raw()
    test_live_values()
    print("ok")


def test_cabin_fuel_links():
    cl = C.load()
    for k, key, eq in (("cockpit-preparation.no-smoking-elec-dev-signs-sw", "smoking", "AUTO"),
                       ("cockpit-preparation.emergency-exit-lights-sw", "emer_lt", "ARM"),
                       ("securing-the-aircraft.no-smoking", "smoking", "OFF")):
        assert cl.links[k]["check"] == {"key": key, "eq": eq} and cl.links[k]["verified"] is False, k
    for k, eq in (("cockpit-preparation.fuel-pump-pb", "ON"), ("parking.fuel-pumps", "OFF")):
        assert C.check_keys(cl.links[k]["check"]) == ["fuel_l1", "fuel_l2", "fuel_c1", "fuel_c2", "fuel_r1", "fuel_r2"]
        assert {c["eq"] for c in cl.links[k]["check"]["all"]} == {eq} and cl.links[k]["verified"] is False
    by = {i.key: i for i in mon.ITEMS}
    assert mon.label(by["smoking"], 1) == "AUTO (1)" and mon.clean_label(by["emer_lt"], 1) == "ARM"
