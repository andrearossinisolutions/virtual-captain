import json
import pathlib
import sys
import tempfile

import checklist as C
from checklist_eval import REFS
import monitor as mon
from state import State

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = pathlib.Path.home() / "Outbox-Alma/Sviluppo/captain/checklist-fasi/checklist-sorgente.txt"
PHASES = ["sim-preparation", "preliminary-cockpit-prep", "cockpit-preparation", "fmgs-initialization", "departure-clearance",
          "fmgs-performance-glareshield", "before-start", "engine-startup", "after-engine-startup", "taxi-checklist", "before-takeoff", "takeoff",
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
        "before-start.atc-transponder", "ils-approach.flaps-full", "non-precision-approach.flaps-full",
        "fmgs-performance-glareshield.squawk-code", "fmgs-performance-glareshield.initial-climb"]
    assert any(i["id"] == "engine-startup.engine-idle-parameters_2" for i in items)


def test_links_reference_real_things():
    cl = C.load()
    keys = {i.key for i in mon.ITEMS}
    assert len(cl.links) > 40

    def walk(check):
        for c in check.get("all", [check]):
            if "show_ref" in c:
                assert c["show_ref"] in REFS
            elif "qnh" in c:
                assert c["qnh"] in REFS
            else:
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
    # ATC Transponder: confronto con lo squawk di clearance (verificato dal vivo), intero senza decimali, n/d se manca
    assert cl.links["before-start.atc-transponder"] == {"check": {"key": "squawk", "eq_ref": "clr.t"}, "verified": True}
    assert mon.BY_KEY["squawk"].lvar == "N_FREQ_XPDR_SELECTED" if hasattr(mon, "BY_KEY") else True
    for raw, txt in ((2000.0, "2000"), (None, "n/d")):
        r = cl.evaluate_phase("before-start", mon.clean_labels({"squawk": raw}))
        row = {i["id"]: i for s in r["subphases"] for i in s["items"]}["before-start.atc-transponder"]
        assert row["live"] == [["Squawk code", txt]] and row["status"] == "none"  # atteso vuoto: solo NOW
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


def _items(cl):
    return {i["id"]: i for p in cl.phases for s in p["subphases"] for i in s["items"]}


def test_departure_clearance():
    cl = C.load()
    assert cl.ids[3:6] == ["fmgs-initialization", "departure-clearance", "fmgs-performance-glareshield"]
    names = {p["id"]: p["name"] for p in cl.phases}
    assert names["fmgs-initialization"] == "FMGS INITIALIZATION" and names["departure-clearance"] == "Departure Clearance"
    assert names["fmgs-performance-glareshield"] == "FMGS PERFORMANCE & GLARESHIELD"
    clr = cl.phase("departure-clearance")["subphases"]
    assert [s["name"] for s in clr] == ["CLEARANCE"]
    ids = [i["id"] for i in clr[0]["items"]]
    assert ids == ["departure-clearance." + k for k in ("expected-rwy", "mcdu-departure-page", "available-sids",
                                                        "obtain-departure-clearance", "craft", "check-cleared-sid")]
    craft = _items(cl)["departure-clearance.craft"]
    assert craft["type"] == "craft" and [f["k"] for f in craft["fields"]] == ["c", "r", "a", "f", "t"]
    assert [s["name"] for s in cl.phase("fmgs-performance-glareshield")["subphases"]] == ["PERFORMANCE", "GLARESHIELD / PEDESTAL"]
    assert "fmgs-performance-glareshield.check-boarding-completed" in _items(cl)
    assert not any("d-i-f-s-r-i-p" in i for i in _items(cl)) and not any("d-i-f-s-r-i-p" in k for k in cl.links)
    # Simplified: craft e obtain-departure-clearance restano, la fase e' visibile
    assert "departure-clearance.craft" not in cl.excluded and "departure-clearance.obtain-departure-clearance" not in cl.excluded
    assert "departure-clearance" in cl.visible_ids(True)
    simp = cl.evaluate_phase("departure-clearance", {}, simplified=True)
    sids = [i["id"] for sp in simp["subphases"] for i in sp["items"]]
    assert "departure-clearance.craft" in sids and "departure-clearance.expected-rwy" not in sids
    row = cl.evaluate_phase("departure-clearance", {})["subphases"][0]["items"][4]
    assert row["type"] == "craft" and row["status"] == "none" and row["fields"] == craft["fields"] and "expected" not in row


def test_craft_validation():
    cl = C.load()
    first = cl.phases[0]["subphases"][0]["items"][0]["id"]
    # nessun link su craft
    try:
        C.Checklist(cl.data, {"departure-clearance.craft": {"check": {"show_ref": "clr.a"}, "verified": False}})
        assert False
    except ValueError:
        pass
    bad = [{"check": {"key": "squawk", "eq_ref": "clr.x"}, "verified": False},  # riferimento non chiuso
           {"check": {"key": "squawk", "eq_ref": "clr.t", "eq": "1"}, "verified": False},
           {"check": {"key": "apu_master", "eq_ref": "clr.t"}, "verified": False},  # kind a mappa di etichette
           {"check": {"all": [{"key": "squawk", "eq_ref": "clr.t"}, {"key": "apu_master", "eq": "ON"}]}, "verified": False},  # misto
           {"check": {"all": [{"key": "apu_master", "eq_ref": "clr.t"}]}, "verified": False},
           {"check": {"show_ref": "nope"}, "verified": False},
           {"check": {"show_ref": "clr.a", "show": ["squawk"]}, "verified": False}]
    for link in bad:
        try:
            C.Checklist(cl.data, {first: link})
            assert False, link
        except ValueError:
            pass
    C.Checklist(cl.data, {first: {"check": {"key": "squawk", "eq_ref": "clr.t"}, "verified": False}})
    data = json.loads(json.dumps(cl.data))
    for it in [i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "craft"]:
        it["fields"] = it["fields"][:-1]
    try:
        C.Checklist(data, {})
        assert False
    except ValueError:
        pass


def test_expected_links():
    cl = C.load()
    P = "fmgs-performance-glareshield."
    assert cl.links[P + "squawk-code"] == {"check": {"key": "squawk", "eq_ref": "clr.t"}, "verified": True}
    for k, ref in (("t-o-rwy", "fld.runway"), ("sid", "plan.sid"), ("transition-altitude", "fld.ta")):
        assert cl.links[P + k] == {"check": {"show_ref": ref}, "verified": False}, k
    assert cl.links[P + "initial-climb"] == {"check": {"key": "fcu_alt", "eq_ref": "clr.a"}, "verified": True}
    exp = {"clr.a": "FL100", "fld.runway": "35R", "plan.sid": "TOSNU", "fld.ta": "5000", "clr.t": "2000"}
    res = cl.evaluate_phase("fmgs-performance-glareshield", {"squawk": "2000"}, expected=exp)
    rows = {i["id"]: i for s in res["subphases"] for i in s["items"]}
    assert rows[P + "initial-climb"]["expected"] == "FL100" and rows[P + "initial-climb"]["status"] == "na"  # fcu_alt non letto
    assert rows[P + "t-o-rwy"]["expected"] == "35R" and rows[P + "sid"]["expected"] == "TOSNU" and rows[P + "transition-altitude"]["expected"] == "5000"
    assert rows[P + "initial-climb"]["live"] == [["FCU altitude", "n/d"]] and rows[P + "squawk-code"]["expected"] == "2000"
    assert rows[P + "squawk-code"]["status"] == "ok"
    assert "expected" not in rows[P + "flight-director-pb"] and res["bad"] == 0
    empty = {i["id"]: i for s in cl.evaluate_phase("fmgs-performance-glareshield", {})["subphases"] for i in s["items"]}
    assert empty[P + "sid"]["expected"] == "" and empty[P + "sid"]["status"] == "none"
    r = cl.evaluate_phase("fmgs-performance-glareshield", {"squawk": "7000"}, expected=exp)
    assert {i["id"]: i for s in r["subphases"] for i in s["items"]}[P + "squawk-code"]["status"] == "bad"
    # V1/VR/V2, lampade e show (link non verificati: con demo=True l'esito esce dal confronto)
    lab = {"v1": "140", "vr": "145", "v2": "0", "cstr1": "ON", "cstr2": "OFF", "fd1": "ON", "fd2": "ON", "flap": "1", "flex": "55"}
    r = cl.evaluate_phase("fmgs-performance-glareshield", lab, demo=True, expected={"fld.v1": "140", "fld.vr": "145", "fld.v2": "150"})
    rw = {i["id"]: i for s in r["subphases"] for i in s["items"]}
    assert rw[P + "v1-vr-v2"]["status"] == "bad" and rw[P + "v1-vr-v2"]["expected"] == "140 / 145 / 150"
    assert rw[P + "v1-vr-v2"]["live"] == [["V1", "140"], ["VR", "145"], ["V2", "0"]]
    assert rw[P + "constraint-pb"]["status"] == "bad" and rw[P + "flight-director-pb"]["status"] == "ok"
    for k in ("flex-toga", "t-o-flaps-conf-ths", "barometric-reference"):
        assert rw[P + k]["status"] == "none" and rw[P + k]["show"] is True, k
    assert rw[P + "barometric-reference"]["live"] == [["Baro", "n/d"]]
    assert rw[P + "flex-toga"]["live"] == [["Flex temp", "55"]] and rw[P + "t-o-flaps-conf-ths"]["live"] == [["Flaps", "1"]]
    assert cl.evaluate_phase("fmgs-performance-glareshield", lab, expected={})["bad"] == 0  # non verificati: nessun rosso
    emp = {i["id"]: i for s in cl.evaluate_phase("fmgs-performance-glareshield", {"v1": "0"}, demo=True, expected={})["subphases"] for i in s["items"]}
    assert emp[P + "v1-vr-v2"]["status"] == "none" and emp[P + "v1-vr-v2"]["show"] is True and emp[P + "v1-vr-v2"]["expected"] == " / "*2
    # Initial climb: quota FCU contro clearance (demo = link verificato)
    def ic(lab, clr):
        r = cl.evaluate_phase("fmgs-performance-glareshield", {"fcu_alt": lab}, demo=True, expected={"clr.a": clr})
        return {i["id"]: i for s in r["subphases"] for i in s["items"]}[P + "initial-climb"]
    assert ic("7000", "FL070")["status"] == "ok" and ic("7000", "FL070")["kind"] == "ok"
    row = ic("6000", "FL070")
    assert row["status"] == "bad" and row["detail"] == "expected FL070, actual 6000" and row["live"] == [["FCU altitude", "6000"]]
    row = ic("6000", "")  # atteso vuoto: solo NOW
    assert row["status"] == "none" and row["show"] is True and row["live"] == [["FCU altitude", "6000"]]


def test_clearance_state():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st = State(cfg)
    assert st.clearance == {k: "" for k in ["c", "r", "a", "f", "t"]} and st.snapshot()["clearance"] == st.clearance
    st.set_clearance({"a": " FL100 ", "t": "2000", "zzz": "x", "f": "126.5"})
    assert st.clearance["a"] == "FL100" and st.clearance["t"] == "2000" and "zzz" not in st.clearance
    st.set_clearance({"r": "x" * 900})
    assert len(st.clearance["r"]) == 400
    for bad in ({"t": "20000"}, {"t": "2008"}, {"t": "ab"}, {"a": ["x"]}, {"c": None}, [1]):
        try:
            st.set_clearance(bad)
            assert False, bad
        except ValueError:
            pass
    assert st.clearance["t"] == "2000"  # un valore non valido non cambia nulla
    st.set_clearance({"t": ""})
    assert st.clearance["t"] == ""
    assert not cfg.exists() or "clearance" not in cfg.read_text()  # mai salvato
    # reset: c e r dal piano, a/f/t vuoti
    st.fields["destination"], st.plan = "LIRF", {"route": "SID DCT ABC STAR"}
    st.set_clearance({"a": "5000", "t": "1234"})
    st.reset_clearance()
    assert st.clearance == {"c": "LIRF", "r": "SID DCT ABC STAR", "a": "", "f": "", "t": ""}
    st.set_clearance({"f": "121.7"})
    st.set_phase("flying")
    assert st.clearance["f"] == "121.7"
    st.set_phase("briefing")  # ritorno in briefing: riparte dal piano
    assert st.clearance == {"c": "LIRF", "r": "SID DCT ABC STAR", "a": "", "f": "", "t": ""}
    # i valori attesi arrivano dallo snapshot
    st.fields["runway"], st.fields["ta"], st.plan["sid"] = "35R", "5000", "TOSNU"
    st.set_clearance({"a": "FL100", "t": "2000"})
    st.set_cl_phase("fmgs-performance-glareshield")
    rows = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}
    P = "fmgs-performance-glareshield."
    assert (rows[P + "initial-climb"]["expected"], rows[P + "t-o-rwy"]["expected"], rows[P + "sid"]["expected"],
            rows[P + "transition-altitude"]["expected"], rows[P + "squawk-code"]["expected"]) == ("FL100", "35R", "TOSNU", "5000", "2000")
    # non persiste al riavvio
    assert State(cfg).clearance == {k: "" for k in ["c", "r", "a", "f", "t"]}
    # il caricamento del piano precompila c e r e svuota a/f/t
    import state as S
    orig = (S.fetch_latest, S.to_briefing)
    S.fetch_latest = lambda uid: {}
    S.to_briefing = lambda ofp: {"origin": "LIML", "destination": "LIPZ", "runway": "35R", "dep_time": "10:00", "ta": 6000, "generated": 1,
                                 "v1": None, "vr": None, "v2": None, "flap": None, "plan": {"route": "AAA DCT BBB", "sid": "S1"}}
    try:
        st.set_clearance({"a": "FL100", "f": "121.7", "t": "7000"})
        st.load_plan("x")
    finally:
        S.fetch_latest, S.to_briefing = orig
    assert st.clearance == {"c": "LIPZ", "r": "AAA DCT BBB", "a": "", "f": "", "t": ""}


def test_qnh_links():
    cl = C.load()
    for iid, ref in (("fmgs-performance-glareshield.barometric-reference", "wx.qnh_dep"), ("descent-checklist.barometric-reference", "wx.qnh_dest")):
        assert cl.links[iid]["check"] == {"qnh": ref} and cl.links[iid]["verified"] is False
    assert "climb.barometric-reference" not in cl.links
    assert C.check_keys({"qnh": "wx.qnh_dep"}) == ["baro_std", "baro_hpa", "baro_inch"]
    for bad in ({"qnh": "clr.a"}, {"qnh": "wx.qnh_dep", "key": "baro_hpa"}, {"all": [{"qnh": "wx.qnh_dep"}]}):
        try:
            C._check_keys(bad, "x")
        except ValueError:
            continue
        raise AssertionError(bad)
    assert C.live_values({"qnh": "wx.qnh_dep"}, {"baro_std": "1"}) == [["Baro", "STD"]]
    assert C.live_values({"qnh": "wx.qnh_dep"}, {"baro_std": "0", "baro_hpa": "1013"}) == [["Baro", "1013"]]
    from weather import Weather
    w = Weather()
    w.seed("LIRF", "Q1013")
    w.seed("LIML", "Q1009")
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json", weather=w)
    st.fields.update(origin="LIRF", destination="LIML")
    st.cl_demo = True
    st.labels = {"baro_std": "0", "baro_hpa": "1013"}
    s = st.snapshot()
    assert s["checklist"]["phase"]["id"] == st.cl_phase
    exp = st._expected()
    assert exp["wx.qnh_dep"] == "1013 hPa" and exp["wx.qnh_dest"] == "1009 hPa"
    r = cl.evaluate_phase("fmgs-performance-glareshield", st.labels, demo=True, expected=exp)
    row = {i["id"]: i for sp in r["subphases"] for i in sp["items"]}["fmgs-performance-glareshield.barometric-reference"]
    assert row["status"] == "ok" and row["expected"] == "1013 hPa" and row["live"] == [["Baro", "1013"]]
    assert State(pathlib.Path(tempfile.mkdtemp()) / "d.json")._expected()["wx.qnh_dep"] == ""


if __name__ == "__main__":
    test_files_valid()
    test_links_reference_real_things()
    test_new_links_show_optional()
    test_validation_rejects_bad_data()
    test_departure_clearance()
    test_craft_validation()
    test_expected_links()
    test_clearance_state()
    test_evaluate_phase()
    test_simplified()
    test_cl_phase_state()
    test_simplified_hides_empty_phases()
    test_snapshot_uses_live_labels_and_demo()
    test_trust_links_and_raw()
    test_live_values()
    test_qnh_links()
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
