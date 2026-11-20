import json
import pathlib
import sys
import tempfile

import checklist as C
import icing
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
    assert all(l["verified"] for l in cl.links.values())  # tutti considerati verificati: le discrepanze le segnala Andrea
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
            elif "detent" in c:
                assert c["detent"]
            elif "std" in c:
                assert c["std"] is True
            elif "ai" in c:
                assert c["ai"] in icing.RULES
            elif "gsx" in c:
                assert all(k in keys for k in c["gsx"])
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
    assert rows["before-start.packs-pb"]["status"] in ("ok", "bad")
    assert rows["before-start.final-loadsheet"]["status"] == "none"
    assert res["bad"] >= 0
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
    st.set_phase("flying")
    st.set_cl_phase("before-start")
    st.set_monitor({"pack1": 1, "pack2": 1, "beacon": 0})
    rows = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}
    assert rows["before-start.packs-pb"]["status"] in ("ok", "bad")
    st.cl_demo = True
    snap = st.snapshot()["checklist"]
    assert snap["bad"] >= 2
    st.set_phase("briefing")
    assert st.labels == {}


def test_snapshot_ai_with_metar():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.fields["origin"], st.fields["destination"] = "LIML", "LIPZ"
    st.weather.seed("LIML", "LIML 181550Z 20004KT 0800 BR BKN002 03/03 Q1022")
    st.set_phase("flying")
    st.set_core({"on_ground": 1})
    st.set_cl_phase("after-engine-startup")
    st.set_monitor({"eng1_ai": 0, "eng2_ai": 0, "sat": 4, "tat": 0})
    assert st.labels["gnd"] == "1" and st.raw["gnd"] == 1
    r = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}["after-engine-startup.engine-and-wing-anti-ice-pb"]
    assert r["status"] == "bad" and r["expected"].startswith("LIML: BR, vis 800 m, T 3") and r["response"] == "AS REQUIRED"
    st.set_cl_phase("descent-preparation")
    st.set_monitor({"eng1_ai": 0, "eng2_ai": 0, "sat": -20, "tat": 5})  # METAR di LIPZ assente
    r = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}["descent-preparation.engine-anti-ice"]
    assert r["status"] == "none" and r["expected"] == "no METAR, check manually"


def test_trust_links_and_raw():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.set_phase("flying")
    st.set_cl_phase("before-start")
    st.set_monitor({"pack1": 1, "pack2": 1, "beacon": 0})
    snap = st.snapshot()
    assert snap["test_links"] is False
    assert all("raw" not in i for s in snap["checklist"]["subphases"] for i in s["items"])  # normale: nessun raw
    assert snap["checklist"]["bad"] >= 0
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
        assert cl.links[P + k]["verified"] is True, k
    assert C.check_keys(cl.links[P + "battery-pb-1-2"]["check"]) == ["bat1", "bat2"]
    assert C.check_keys(cl.links[P + "all-ir-modes"]["check"]) == ["ir1", "ir2", "ir3"]
    assert cl.links[P + "gain-knob"]["check"] == {"show": ["wx_gain"]}
    labels = mon.clean_labels({"wx_gain": 0.5, "wx_mode": 2, "flap": 3})
    res = cl.evaluate_phase("preliminary-cockpit-prep", labels, raw={"wx_gain": 0.5, "wx_mode": 2})
    rows = {i["id"]: i for s in res["subphases"] for i in s["items"]}
    assert rows[P + "gain-knob"]["status"] == "none" and rows[P + "gain-knob"]["live"] == [["WX Gain knob", "0.5"]]
    assert rows[P + "gain-knob"]["raw"] == [["A_WR_GAIN", "0.5"]]
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
    assert 'nowNode(mk,"NOW: "' in page and ".now.nv{color:var(--mut)}" in page and "i.raw,i.show" in page
    for ph in ("ils-approach", "non-precision-approach"):
        assert cl.links[ph + ".flaps-full"]["optional"] is True and cl.links[ph + ".flaps-full"]["verified"] is True
        for raw, st in ((4, "ok"), (3, "opt")):
            r = cl.evaluate_phase(ph, mon.clean_labels({"flap": raw}))
            row = {i["id"]: i for s in r["subphases"] for i in s["items"]}[ph + ".flaps-full"]
            assert row["status"] == st, (ph, raw)
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
    assert craft["type"] == "craft" and [f["k"] for f in craft["fields"]] == ["c", "w", "r", "a", "f", "t"]
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
    assert [f["label"] for f in cl.phase("departure-clearance")["subphases"][0]["items"][4]["fields"]] == ["Cleared to", "RWY in use", "Route", "Initial Climb", "Frequency", "Squawk"]
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
        assert cl.links[P + k] == {"check": {"show_ref": ref}, "verified": True}, k
    assert cl.links[P + "initial-climb"] == {"check": {"key": "fcu_alt", "eq_ref": "clr.a"}, "verified": True}
    exp = {"clr.a": "FL100", "fld.runway": "35R", "plan.sid": "TOSNU", "fld.ta": "5000", "clr.t": "2000"}
    res = cl.evaluate_phase("fmgs-performance-glareshield", {"squawk": "2000"}, expected=exp)
    rows = {i["id"]: i for s in res["subphases"] for i in s["items"]}
    assert rows[P + "initial-climb"]["expected"] == "FL100" and rows[P + "initial-climb"]["status"] == "na"  # fcu_alt non letto
    assert rows[P + "t-o-rwy"]["expected"] == "35R" and rows[P + "sid"]["expected"] == "TOSNU" and rows[P + "transition-altitude"]["expected"] == "5000"
    assert rows[P + "initial-climb"]["live"] == [["FCU altitude", "n/d"]] and rows[P + "squawk-code"]["expected"] == "2000"
    assert rows[P + "squawk-code"]["status"] == "ok"
    assert "expected" not in rows[P + "flight-director-pb"]
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
    assert cl.evaluate_phase("fmgs-performance-glareshield", lab, expected={})["bad"] >= 0
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
    assert st.clearance == {k: "" for k in ["c", "w", "r", "a", "f", "t"]} and st.snapshot()["clearance"] == st.clearance
    st.set_clearance({"a": " FL100 ", "t": "2000", "zzz": "x", "f": "126.5"})
    assert st.clearance["a"] == "FL100" and st.clearance["t"] == "2000" and "zzz" not in st.clearance
    st.set_clearance({"w": " 17r  17l,x"})
    assert st.clearance["w"] == "17R/17L"
    st.set_clearance({"w": ""})
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
    assert st.clearance == {"c": "LIRF", "w": "", "r": "SID DCT ABC STAR", "a": "", "f": "", "t": ""}
    st.set_clearance({"f": "121.7"})
    st.set_phase("flying")
    assert st.clearance["f"] == "121.7"
    st.set_phase("briefing")  # ritorno in briefing: riparte dal piano
    assert st.clearance == {"c": "LIRF", "w": "", "r": "SID DCT ABC STAR", "a": "", "f": "", "t": ""}
    # i valori attesi arrivano dallo snapshot
    st.fields["runway"], st.fields["ta"], st.plan["sid"] = "35R", "5000", "TOSNU"
    st.set_clearance({"a": "FL100", "t": "2000"})
    st.set_cl_phase("fmgs-performance-glareshield")
    rows = {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}
    P = "fmgs-performance-glareshield."
    assert (rows[P + "initial-climb"]["expected"], rows[P + "t-o-rwy"]["expected"], rows[P + "sid"]["expected"],
            rows[P + "transition-altitude"]["expected"], rows[P + "squawk-code"]["expected"]) == ("FL100", "35R", "TOSNU", "5000", "2000")
    # non persiste al riavvio
    assert State(cfg).clearance == {k: "" for k in ["c", "w", "r", "a", "f", "t"]}
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
    assert st.clearance == {"c": "LIPZ", "w": "", "r": "AAA DCT BBB", "a": "", "f": "", "t": ""}


def test_qnh_links():
    cl = C.load()
    for iid, ref in (("fmgs-performance-glareshield.barometric-reference", "wx.qnh_dep"), ("descent-checklist.barometric-reference", "wx.qnh_dest")):
        assert cl.links[iid]["check"] == ({"qnh": ref, "gate_tl": True} if ref == "wx.qnh_dest" else {"qnh": ref}) and cl.links[iid]["verified"] is True
    assert cl.links["climb.barometric-reference"] == {"check": {"std": True}, "verified": True}
    assert cl.links["climb.engine-anti-ice"] == {"check": {"ai": "climb"}, "verified": True}
    assert C.check_keys({"std": True}) == ["baro_std", "baro_hpa", "baro_inch"]
    from checklist_eval import evaluate as _ev
    assert _ev({"std": True}, {"baro_std": "1", "baro_hpa": "1013", "baro_inch": None}, True)["status"] == "ok"
    assert _ev({"std": True}, {"baro_std": "0", "baro_hpa": "1013", "baro_inch": None}, True)["status"] == "bad"
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
    assert exp["wx.qnh_dep"] == "1013 hPa (METAR)" and exp["wx.qnh_dest"] == "1009 hPa (METAR)"
    r = cl.evaluate_phase("fmgs-performance-glareshield", st.labels, demo=True, expected=exp)
    row = {i["id"]: i for sp in r["subphases"] for i in sp["items"]}["fmgs-performance-glareshield.barometric-reference"]
    assert row["status"] == "ok" and row["expected"] == "1013 hPa (METAR)" and row["live"] == [["Baro", "1013"]]
    assert State(pathlib.Path(tempfile.mkdtemp()) / "d.json")._expected()["wx.qnh_dep"] == ""


def test_flap_and_taxi_alt_links():
    cl = C.load()
    assert cl.links["after-engine-startup.flap-levers"] == {"check": {"key": "flap", "eq_ref": "fld.flap"}, "verified": True}
    assert cl.links["taxi-checklist.cleared-altitude-on-fcu"] == {"check": {"key": "fcu_alt", "eq_ref": "clr.a"}, "verified": True}
    T = "taxi-checklist."
    assert cl.links[T + "t-o-data-runway"] == {"check": {"show_ref": "fld.runway"}, "verified": True}
    assert cl.links[T + "t-o-data-flaps-lever"] == cl.links["after-engine-startup.flap-levers"]
    assert cl.links[T + "t-o-data-v1-vr-v2"] == cl.links["fmgs-performance-glareshield.v1-vr-v2"]
    assert cl.links[T + "flight-director"] == cl.links["fmgs-performance-glareshield.flight-director-pb"]
    assert cl.links[T + "t-o-data-flx-to-temperature"] == {"check": {"show": ["flex"]}, "verified": True}
    assert cl.links[T + "autobrake-pb"] == {"check": {"key": "autobrake_max", "eq": "ON"}, "verified": True}
    from monitor import ITEMS
    ab = {i.key: i for i in ITEMS}["autobrake_max"]
    assert ab.lvar == "I_MIP_AUTOBRAKE_MAX_L" and ab.kind == "onoff" and ab.group == "Autobrake"
    for k in ("takeoff-briefing", "takeoff-config-pb", "ecam-takeoff-memo"):
        assert T + k not in cl.links
    r = cl.evaluate_phase("after-engine-startup", {"flap": "2"}, expected={"fld.flap": "2"})
    row = {i["id"]: i for s in r["subphases"] for i in s["items"]}["after-engine-startup.flap-levers"]
    assert row["status"] == "ok" and row["expected"] == "2"
    r = cl.evaluate_phase("taxi-checklist", {"fcu_alt": "5000"}, expected={"clr.a": "FL050"})
    assert {i["id"]: i for s in r["subphases"] for i in s["items"]}["taxi-checklist.cleared-altitude-on-fcu"]["status"] == "ok"
    for bad in ({"check": {"key": "flap", "eq_ref": "clr.t"}}, {"check": {"key": "apu_master", "eq_ref": "fld.flap"}}):
        try:
            C._check_keys(bad["check"], "x")
            assert False
        except ValueError:
            pass


def test_thrust_levers_show():
    cl = C.load()
    assert cl.links["takeoff.thrust-levers"] == {"check": {"detent": "BETWEEN"}, "verified": True}  # 50% N1: leve fuori dai detent (BETWEEN)
    det = {"before-start.thrust-levers": "IDLE", "takeoff.thrust-levers_2": ["FLX", "TOGA"], "takeoff.thrust-levers_3": "CLIMB",
           "go-around.thrust-levers": "TOGA", "go-around.thrust-levers_2": "CLIMB", "landing.thrust-levers": "IDLE",
           "landing.thrust-levers_2": "REVERSE IDLE", "landing.thrust-levers_3": "IDLE"}
    for i, d in det.items():
        assert cl.links[i] == {"check": {"detent": d}, "verified": True}, i
        assert C.check_keys(cl.links[i]["check"]) == ["thr_det_l", "thr_det_r"]
    assert [i.group for i in mon.ITEMS if i.key.startswith("thr_")] == ["Thrust"] * 6
    for bad in ({"detent": "NOPE"}, {"detent": []}, {"detent": "IDLE", "x": 1}, {"detent": ["IDLE", "BETWEEN"]}):  # BETWEEN solo da solo
        try:
            C._check_keys(bad, "x")
            assert False, bad
        except ValueError:
            pass
    C._check_keys({"detent": ["FLX", "TOGA"]}, "x")
    try:
        C._check_keys({"all": [{"detent": "IDLE"}]}, "x")
        assert False
    except ValueError:
        pass
    # righe: dettaglio dei detent (sempre attivo); detent non leggibile -> "na" con i grezzi gate
    raw2 = {"thr_gate_l": "0.1", "thr_gate_r": "0.2"}
    rows = {i["id"]: i for s in cl.evaluate_phase("landing", raw2, True)["subphases"] for i in s["items"]}
    r = rows["landing.thrust-levers"]
    assert r["status"] == "na" and [v[1] for v in r["live"]] == ["0.1", "0.2"]
    rows = {i["id"]: i for s in cl.evaluate_phase("landing", {"thr_det_l": "IDLE", "thr_det_r": "CLIMB", **raw2}, True)["subphases"] for i in s["items"]}
    r = rows["landing.thrust-levers"]
    assert r["status"] == "bad" and "show" not in r and r["live"] == [["Thrust lever L detent", "IDLE"], ["Thrust lever R detent", "CLIMB"]]
    assert "actual CLIMB" in r["detail"]
    raw = cl.evaluate_phase("landing", {}, True, raw={"thr_det_l": 2, "thr_gate_l": 0.5})
    names = [n for s in raw["subphases"] for i in s["items"] if i["id"] == "landing.thrust-levers" for n, _ in i["raw"]]
    assert names[:2] == ["Thrust lever L detent", "Thrust lever R detent"] and len(names) == 4


def test_inputs_items():
    cl = C.load()
    P = "fmgs-performance-glareshield."
    want = {"t-o-rwy": ["runway"], "v1-vr-v2": ["v1", "vr", "v2"], "t-o-flaps-conf-ths": ["flap"], "transition-altitude": ["ta"],
            "thrust-reduction-acc-altitude": ["thr_red_agl", "accel_agl"]}
    for simplified in (False, True):  # anche nella checklist semplificata
        rows = {i["id"]: i for s in cl.evaluate_phase("fmgs-performance-glareshield", {}, simplified=simplified)["subphases"] for i in s["items"]}
        for k, keys in want.items():
            assert [f["k"] for f in rows[P + k]["inputs"]] == keys, k
        assert "inputs" not in rows[P + "sid"] and "inputs" not in rows[P + "flex-toga"]
    assert [f.get("plan", False) for f in cl.evaluate_phase("fmgs-performance-glareshield", {})["subphases"][0]["items"][-1]["inputs"]] == [False, False]
    # i link (NOW / Expected) restano sulle voci con campi inline
    assert cl.links[P + "t-o-rwy"]["check"] == {"show_ref": "fld.runway"}
    rwy = {i["id"]: i for s in cl.evaluate_phase("after-landing", {})["subphases"] for i in s["items"]}["after-landing.rwy"]
    assert [f["k"] for f in rwy["inputs"]] == ["taxiway"] and rwy["inputs"][0]["prefix"] == "VACATED VIA" and rwy["response"] == "VACATED"
    assert rwy["status"] == "none"  # nessun link: stato invariato
    atis = {i["id"]: i for s in cl.evaluate_phase("descent-preparation", {})["subphases"] for i in s["items"]}["descent-preparation.check-arrival-atis"]
    assert [f["k"] for f in atis["inputs"]] == ["atis_letter", "atis_rwy", "atis_wind", "atis_qnh", "atis_tl"]
    assert [f.get("prefix") for f in atis["inputs"]] == ["INFO", "RWY", None, "QNH", "TL"] and atis["status"] == "none" and atis["response"] == "ANNOTATE INFOS & LETTER"
    assert "descent-preparation.check-arrival-atis" not in cl.links and "descent-preparation.check-arrival-atis" not in cl.excluded
    rows = {i["id"]: i for ph in cl.ids for s in cl.evaluate_phase(ph, {})["subphases"] for i in s["items"]}
    hints = {k: r["atis_hint"] for k, r in rows.items() if "atis_hint" in r}
    assert hints == {"descent-checklist.contact-approach": ["letter", "rwy", "qnh", "wind", "tl"],
                     "descent-checklist.barometric-reference": ["tl"], "ils-approach.obtain-landing-clearance": ["rwy", "wind"], "non-precision-approach.obtain-landing-clearance": ["rwy", "wind"]}
    assert {k: r["wx_hint"] for k, r in rows.items() if "wx_hint" in r} == {"descent-preparation.weather-landing-information": "dest", "departure-clearance.obtain-departure-clearance": "dep"}
    dep = rows["fmgs-initialization.check-departure-atis"]
    assert [f["k"] for f in dep["inputs"]] == ["datis_letter", "datis_rwy", "datis_wind", "datis_qnh"] and [f.get("prefix") for f in dep["inputs"]] == ["INFO", "RWY", None, "QNH"]
    assert dep["inputs"][1]["label"] == "Departure runway(s)" and dep["response"] == "ANNOTATE INFOS & LETTER" and "wx_hint" not in dep and dep["status"] == "none"
    assert "fmgs-initialization.check-departure-atis" not in cl.excluded  # visibile anche in Simplified
    sp = {i["id"] for sp_ in cl.evaluate_phase("fmgs-initialization", {}, simplified=True)["subphases"] for i in sp_["items"]}
    assert "fmgs-initialization.check-departure-atis" in sp
    for b in ({"x": 1}, "rwy", []):  # datis_hint non valido
        data = json.loads(json.dumps(cl.data))
        next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "atc")["datis_hint"] = b
        try:
            C.Checklist(data, {})
            assert False, b
        except ValueError:
            pass
    for iid in ("fmgs-initialization.check-departure-atis", "descent-preparation.check-arrival-atis"):
        assert rows[iid]["layout"] == "table" and rows[iid]["inputs"][0]["k"].endswith("atis_letter")
    assert "layout" not in rows["after-landing.rwy"]  # il taxiway resta inline
    for b in ("grid", "", None, ["table"]):  # layout non valido (solo "table")
        data = json.loads(json.dumps(cl.data))
        next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i.get("inputs"))["layout"] = b
        try:
            C.Checklist(data, {})
            assert False, b
        except ValueError:
            pass
    data = json.loads(json.dumps(cl.data))
    next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "action" and "inputs" not in i)["layout"] = "table"  # senza inputs
    try:
        C.Checklist(data, {})
        assert False
    except ValueError:
        pass
    for b in ("src", "", None, ["dest"]):  # wx_hint non valido (solo "dest")
        data = json.loads(json.dumps(cl.data))
        next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "action")["wx_hint"] = b
        try:
            C.Checklist(data, {})
            assert False, b
        except ValueError:
            pass
    data = json.loads(json.dumps(cl.data))
    next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "note")["wx_hint"] = "dest"
    try:
        C.Checklist(data, {})
        assert False
    except ValueError:
        pass
    for b in ("rwy", [], ["bogus"], ["rwy", "rwy"], [1]):  # atis_hint non valido
        data = json.loads(json.dumps(cl.data))
        next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "atc")["atis_hint"] = b
        try:
            C.Checklist(data, {})
            assert False, b
        except ValueError:
            pass
    data = json.loads(json.dumps(cl.data))
    next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "note")["atis_hint"] = ["rwy"]
    try:
        C.Checklist(data, {})
        assert False
    except ValueError:
        pass
    bad = [{"k": "ta", "label": "a", "prefix": 5}], [{"k": "ta", "label": "a", "prefix": ""}], [{"k": "ta", "label": "a", "prefix": "  "}], \
        [{"k": "bogus", "label": "x"}], [], [{"k": "ta", "label": ""}], [{"k": "ta", "label": "a"}, {"k": "ta", "label": "b"}], "x", [{"k": "ta", "label": "a", "plan": "y"}]
    for b in bad:
        data = json.loads(json.dumps(cl.data))
        data["phases"][0]["subphases"][0]["items"][0]["inputs"] = b
        try:
            C.Checklist(data, {})
            assert False, b
        except ValueError:
            pass
    data = json.loads(json.dumps(cl.data))  # solo sulle voci action
    it = next(i for p in data["phases"] for s in p["subphases"] for i in s["items"] if i["type"] == "note")
    it["inputs"] = [{"k": "ta", "label": "TA"}]
    try:
        C.Checklist(data, {})
        assert False
    except ValueError:
        pass


def test_fob_zfw_items_and_tolerance():
    cl = C.load()
    fob = {"key": "fob_now", "tol_ref": "fld.fob", "tol_pct": 2}
    for k in ("before-start.fob", "fmgs-initialization.fob"):
        assert cl.links[k]["check"] == fob and cl.links[k]["verified"] is True
    assert cl.links["before-start.zfw-zfwcg"]["check"] == {"key": "zfw_calc", "tol_ref": "fld.zfw", "tol_pct": 2}  # confrontato col ZFW del piano (loadsheet Fenix 58,101 kg = calcolo MSFS)
    rows = {i["id"]: i for s in cl.evaluate_phase("before-start", {})["subphases"] for i in s["items"]}
    assert [f["k"] for f in rows["before-start.zfw-zfwcg"]["inputs"]] == ["zfw", "zfwcg"] and [f["k"] for f in rows["before-start.fob"]["inputs"]] == ["fob"]
    from checklist_eval import evaluate
    ev = lambda lab, exp: evaluate(fob, lab, True, False, exp)
    # stessa unita' (piano kgs, Fenix kg), tolleranza 2%
    assert ev({"fob_now": "6500", "cfg_lbs": "0"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "ok"
    assert ev({"fob_now": "6620", "cfg_lbs": "0"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "ok"  # +1.8%
    r = ev({"fob_now": "6700", "cfg_lbs": "0"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})  # +3%
    assert r["status"] == "bad" and "actual 6700" in r["detail"]
    # conversione: piano kg, Fenix in lbs
    assert ev({"fob_now": "14330", "cfg_lbs": "1"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "ok"
    assert ev({"fob_now": "6500", "cfg_lbs": "1"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "bad"
    # piano lbs, Fenix in kg
    assert ev({"fob_now": "6500", "cfg_lbs": "0"}, {"fld.fob": "14330", "fld.fob_unit": "lbs"})["status"] == "ok"
    # unita' del piano ignota (campo a mano): nessuna conversione
    assert ev({"fob_now": "6500", "cfg_lbs": "1"}, {"fld.fob": "6500", "fld.fob_unit": ""})["status"] == "ok"
    # servirebbe la conversione ma il Fenix non dice l'unita': non leggibile; valore non letto: non leggibile
    assert ev({"fob_now": "6500"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "na"
    assert ev({"cfg_lbs": "0"}, {"fld.fob": "6500", "fld.fob_unit": "kgs"})["status"] == "na"
    # atteso vuoto: solo NOW
    assert ev({"fob_now": "6500"}, {"fld.fob": "", "fld.fob_unit": "kgs"})["status"] == "none"
    r = {i["id"]: i for s in cl.evaluate_phase("before-start", {"fob_now": "6500", "cfg_lbs": "0"}, expected={"fld.fob": ""})["subphases"] for i in s["items"]}["before-start.fob"]
    assert r["status"] == "none" and r["show"] is True and r["live"] == [["FOB (Fenix, display unit)", "6500"]]
    # ZFW: solo NOW, nessun colore
    r = {i["id"]: i for s in cl.evaluate_phase("before-start", {"zfw_calc": "120000"}, expected={})["subphases"] for i in s["items"]}["before-start.zfw-zfwcg"]
    assert r["status"] == "none" and r["live"] == [["ZFW calc. (MSFS total - fuel, Fenix unit)", "120000"]]
    for bad in ({"key": "fob_now", "tol_ref": "fld.fob", "tol_pct": 0}, {"key": "fob_now", "tol_ref": "fld.fob"},
                {"key": "pack1", "tol_ref": "fld.fob", "tol_pct": 2}, {"key": "fob_now", "tol_ref": "clr.a", "tol_pct": 2},
                {"all": [{"key": "fob_now", "tol_ref": "fld.fob", "tol_pct": 2}]}):
        try:
            C._check_keys(bad, "x")
        except ValueError:
            continue
        raise AssertionError(bad)


def test_briefing_complete_restarts_checklist():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.set_cl_phase("fmgs-performance-glareshield")
    st.set_phase("flying")
    assert st.cl_phase == st.cl.ids[0]
    st.set_cl_phase("fmgs-performance-glareshield")
    st.set_phase("flying")  # gia' in volo: non riparte
    assert st.cl_phase == "fmgs-performance-glareshield"
    st.set_phase("briefing")  # Back to briefing: non tocca la fase
    assert st.cl_phase == "fmgs-performance-glareshield"
    st.set_phase("flying")
    assert st.cl_phase == st.cl.ids[0]
    st.update({"checklist_mode": "simplified"})
    st.set_phase("briefing")
    st.set_cl_phase(st.cl_visible()[-1])
    st.set_phase("flying")
    assert st.cl_phase == st.cl_visible()[0]


def test_new_links_wellformed():
    cl = C.load()
    expect = {
        "securing-the-aircraft.battery-1-2": ["bat1", "bat2"], "securing-the-aircraft.oxygen-crew-supply": ["crew_oxy"],
        "parking.exterior-lights": ["landing_l", "landing_r", "nose", "strobe", "beacon", "rwy_turnoff", "navlogo"],
        "securing-the-aircraft.exterior-lights": ["landing_l", "landing_r", "nose", "strobe", "beacon", "rwy_turnoff", "navlogo"],
        "after-landing.weather-radar": ["wx_sys"], "taxi-checklist.radar": ["wx_sys"], "descent-checklist.efis-option": ["cstr1", "cstr2"],
        "ils-approach.appr-button-on-fcu": ["appr"], "ils-approach.autopilot-1-2": ["ap1", "ap2"], "landing.ground-spoilers": ["spoilers"], "after-landing.predictive-windshear-system": ["wx_pws"],
        "parking.anti-ice": ["eng1_ai", "eng2_ai"], "securing-the-aircraft.adirs-1-2-3": ["ir1", "ir2", "ir3"]}
    for iid, keys in expect.items():
        assert iid in cl.links and cl.links[iid]["verified"] is True, iid
        assert C.check_keys(cl.links[iid]["check"]) == keys, iid
    for iid in ("after-landing.predictive-windshear-system",):
        assert set(cl.links[iid]["check"]) == {"show"}
    ls = cl.links["descent-checklist.ls-button"]  # LS pb: solo "NOW", lvar ipotizzata; mai verde/rosso, anche senza lettura
    assert ls["check"] == {"show": ["ls1", "ls2"]}
    for lab in ({"ls1": "ON", "ls2": "OFF"}, {}):
        r = [i for sp in cl.evaluate_phase("descent-checklist", lab)["subphases"] for i in sp["items"] if i["id"] == "descent-checklist.ls-button"][0]
        assert r["status"] == "none" and r["show"] is True
    assert r["live"] == [["LS pb 1", "n/d"], ["LS pb 2", "n/d"]]
    assert cl.links["descent-checklist.descent"] == {"check": {"key": "vs_fpm", "lt": -100}, "verified": True}
    for lab, st in (({"vs_fpm": "-800"}, "ok"), ({"vs_fpm": "-101"}, "ok"), ({"vs_fpm": "-100"}, "bad"), ({"vs_fpm": "0"}, "bad"), ({}, "na")):
        r = [i for sp in cl.evaluate_phase("descent-checklist", lab)["subphases"] for i in sp["items"] if i["id"] == "descent-checklist.descent"][0]
        assert r["status"] == st and "show" not in r
    for bad in ({"key": "vs_fpm", "lt": "0"}, {"key": "fd1", "lt": 0}, {"key": "vs_fpm", "lt": 0, "eq": "ON"}):
        try:
            C._check_keys(bad, "x"); assert False, bad
        except ValueError:
            pass
    for iid in ("descent-preparation.auto-brake", "ils-approach.autobrakes", "non-precision-approach.autobrakes"):
        assert cl.links[iid]["check"] == {"key": "autobrake_sel", "eq": ["LO", "MED", "MAX"]}
        def row(lab, iid=iid):
            return [i for sp in cl.evaluate_phase(iid.split(".")[0], lab)["subphases"] for i in sp["items"] if i["id"] == iid][0]
        assert [row({"autobrake_sel": v})["status"] for v in ("LO", "MED", "MAX")] == ["ok"] * 3
        r = row({"autobrake_sel": "OFF"})
        assert r["status"] == "bad" and r["detail"] == "expected LO / MED / MAX, actual OFF" and r["live"] == [["Autobrake selected (from lamps)", "OFF"]]
        assert row({})["status"] == "na" and row({"autobrake_sel": None})["kind"] == "unreadable"
    assert cl.links["landing.autobrakes"]["check"] == {"show": ["autobrake_sel"]}
    r = [i for sp in cl.evaluate_phase("landing", {"autobrake_sel": "MED"})["subphases"] for i in sp["items"] if i["id"] == "landing.autobrakes"][0]
    assert r["status"] == "none" and r["show"] is True and r["live"] == [["Autobrake selected (from lamps)", "MED"]]
    assert cl.links["taxi-checklist.autobrake-pb"]["check"] == {"key": "autobrake_max", "eq": "ON"}
    r = [i for sp in cl.evaluate_phase("descent-preparation", {"eng1_ai": "ON", "eng2_ai": "OFF", "sat": "-45"})["subphases"] for i in sp["items"] if i["id"] == "descent-preparation.engine-anti-ice"][0]
    assert r["status"] == "bad" and "Engine 2 anti-ice sw" in r["detail"] and [v[1] for v in r["live"]][:3] == ["ON", "OFF", "-45"]  # attivo: SAT < -40 -> ON richiesto
    assert cl.links["descent-preparation.engine-anti-ice"] == {"check": {"ai": "descent"}, "verified": True}
    ai = cl.links["parking.anti-ice"]["check"]
    assert "all" in ai and [(c["key"], c["eq"]) for c in ai["all"]] == [("eng1_ai", "OFF"), ("eng2_ai", "OFF")]
    assert C.evaluate(ai, {"eng1_ai": "OFF", "eng2_ai": "OFF"})["status"] == "ok"
    assert C.evaluate(ai, {"eng1_ai": "OFF", "eng2_ai": "ON"})["status"] == "bad"
    assert C.evaluate(ai, {"eng1_ai": "ON", "eng2_ai": "OFF"})["status"] == "bad"
    assert C.evaluate(ai, {"eng1_ai": "OFF"})["status"] == "na" and C.evaluate(ai, {})["kind"] == "unreadable"
    ev = lambda iid, lab: C.evaluate(cl.links[iid]["check"], lab)
    ad = "securing-the-aircraft.adirs-1-2-3"
    assert cl.links[ad]["check"]["all"] == [{"key": k, "eq": "OFF"} for k in ("ir1", "ir2", "ir3")]
    assert ev(ad, {"ir1": "OFF", "ir2": "OFF", "ir3": "OFF"})["status"] == "ok" and ev(ad, {"ir1": "OFF", "ir2": "NAV", "ir3": "OFF"})["status"] == "bad"
    assert ev(ad, {"ir1": "OFF", "ir2": "OFF"})["status"] == "na"
    ap = "ils-approach.autopilot-1-2"
    assert cl.links[ap]["check"]["all"] == [{"key": "ap1", "eq": "ON"}, {"key": "ap2", "eq": "ON"}]
    assert ev(ap, {"ap1": "ON", "ap2": "ON"})["status"] == "ok" and ev(ap, {"ap1": "ON", "ap2": "OFF"})["status"] == "bad" and ev(ap, {"ap1": "ON"})["status"] == "na"
    ap_ = "ils-approach.appr-button-on-fcu"
    assert cl.links[ap_]["check"] == {"key": "appr", "eq": "ON"}
    assert next(i for i in mon.ITEMS if i.key == "appr").lvar == "I_FCU_APPR"
    npa = "non-precision-approach.manage-vert-guidance-appr-appr-button"
    assert cl.links[npa]["check"] == {"key": "appr", "eq": "ON"} and cl.links[npa]["verified"] is True
    assert ev(npa, {"appr": "ON"})["status"] == "ok" and ev(npa, {"appr": "OFF"})["status"] == "bad" and ev(npa, {})["status"] == "na"
    assert ev(ap_, {"appr": "ON"})["status"] == "ok" and ev(ap_, {"appr": "OFF"})["status"] == "bad" and ev(ap_, {})["status"] == "na"
    assert cl.links["takeoff.autopilot"]["check"] == {"show": ["ap1", "ap2"]} and "non-precision-approach.autopilot" not in cl.links
    allon = {k: "OFF" for k in expect["parking.exterior-lights"]}
    res = lambda iid, lab: C.evaluate(cl.links[iid]["check"], lab)["status"]
    assert res("securing-the-aircraft.exterior-lights", allon) == "ok"
    assert res("parking.exterior-lights", allon) == "bad"
    assert res("parking.exterior-lights", {**allon, "navlogo": "NAV"}) == "ok"
    assert res("taxi-checklist.radar", {"wx_sys": "SYS 2"}) == "ok" and res("taxi-checklist.radar", {"wx_sys": "OFF"}) == "bad"
    assert res("landing.ground-spoilers", {"spoilers": "FULL"}) == "ok" and res("landing.ground-spoilers", {"spoilers": "ARM"}) == "bad"


def test_ai_links():
    cl = C.load()
    for iid, rule in (("after-engine-startup.engine-and-wing-anti-ice-pb", "ground"), ("after-takeoff.anti-ice-protection", "takeoff"),
                      ("climb.engine-anti-ice", "climb"), ("descent-preparation.engine-anti-ice", "descent")):
        assert cl.links[iid] == {"check": {"ai": rule}, "verified": True}, iid
    assert C.check_keys({"ai": "descent"}) == ["eng1_ai", "eng2_ai", "sat", "tat", "gnd"]
    # validazione: 'ai' solo da solo, regola nota, mai dentro 'all'
    for bad in ({"ai": "xx"}, {"ai": "ground", "x": 1}, {"all": [{"ai": "ground"}]}, {"all": [{"key": "eng1_ai", "eq": "ON"}, {"ai": "ground"}]}):
        try:
            C._check_keys(bad, "x"); assert False, bad
        except ValueError:
            pass
    C._check_keys({"ai": "ground"}, "x")

    def row(phase, iid, lab, wx=None):
        return [i for sp in cl.evaluate_phase(phase, lab, wx=wx)["subphases"] for i in sp["items"] if i["id"] == iid][0]
    desc = "descent-preparation.engine-anti-ice"
    ON, OFF = {"eng1_ai": "ON", "eng2_ai": "ON"}, {"eng1_ai": "OFF", "eng2_ai": "OFF"}
    r = row("descent-preparation", desc, {**ON, "sat": "-45"})
    assert r["status"] == "ok" and "below -40" in r["expected"]
    r = row("descent-preparation", desc, {**OFF, "sat": "-45"}, {"dest": {"raw": "x", "temp": 20}})  # indipendente dal METAR
    assert r["status"] == "bad"
    r = row("descent-preparation", desc, {**OFF, "sat": "-30", "tat": "5"})  # SAT >= -40, METAR assente: nessun esito
    assert r["status"] == "none" and r["expected"] == "no METAR, check manually" and r["show"] is True
    r = row("descent-preparation", desc, {**OFF, "sat": "0"}, {"dest": {}})
    assert r["status"] == "none"
    dest = {"dest": {"icao": "LIPZ", "raw": "x", "temp": 3, "wx": ["RA"]}}
    r = row("descent-preparation", desc, {**OFF, "sat": "-20", "tat": "5"}, dest)
    assert r["status"] == "bad" and "LIPZ: RA, T 5" in r["expected"]
    assert row("descent-preparation", desc, {**ON, "sat": "-20", "tat": "5"}, dest)["status"] == "ok"
    assert row("descent-preparation", desc, {**OFF, "sat": "-20", "tat": "11"}, dest)["status"] == "none"  # TAT > 10: not required
    # a terra: METAR di partenza; ripiego SAT con gnd=1
    g = "after-engine-startup.engine-and-wing-anti-ice-pb"
    dep = {"dep": {"icao": "LIML", "raw": "x", "temp": 3, "vis": "0800", "wx": ["BR"]}}
    r = row("after-engine-startup", g, OFF, dep)
    assert r["status"] == "bad" and r["expected"] == "LIML: BR, vis 800 m, T 3 \u00b0C: icing conditions, ON"
    assert row("after-engine-startup", g, ON, dep)["status"] == "ok"
    assert row("after-engine-startup", g, OFF)["status"] == "none" and row("after-engine-startup", g, OFF)["expected"] == "no METAR, check manually"
    # promemoria: mai colorati
    for ph, iid in (("after-takeoff", "after-takeoff.anti-ice-protection"), ("climb", "climb.engine-anti-ice")):
        for lab in (OFF, ON, {**OFF, "sat": "-50", "tat": "-30"}):
            r = row(ph, iid, lab, {**dep, **dest})
            assert r["status"] == "none" and r["show"] is True and r["expected"], (iid, lab)
    # Simplified: le voci non sono escluse (comportamento invariato)
    assert not any(i.startswith(("climb.engine-anti-ice", "descent-preparation.engine-anti-ice")) for i in cl.excluded)


def _gated_state(phase):
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.fields.update({"ta": "6000", "thr_red_agl": "1500", "accel_agl": "1500"})
    st.set_phase("flying")
    st.set_cl_phase(phase)
    return st


def _rows(st):
    return {i["id"]: i for s in st.snapshot()["checklist"]["subphases"] for i in s["items"]}


def test_gates_file_validation_and_inventory():
    cl = C.load()
    g = cl.gates
    expected = {"takeoff.landing-gear", "takeoff.thrust-levers_3", "takeoff.packs-pb", "takeoff.ground-spoilers", "takeoff.nose-light-sw",
                "takeoff.runway-turn-off-lights-sw", "takeoff.at-f-speed-flaps-1", "takeoff.at-s-speed-flaps-0", "climb.barometric-reference",
                "climb.engine-anti-ice", "climb.landing-lights", "descent-checklist.landing-lights", "descent-checklist.efis-option",
                "go-around.thrust-levers_2", "ils-approach.flaps-2", "ils-approach.landing-gear", "ils-approach.flaps-3", "ils-approach.flaps-full",
                "non-precision-approach.flaps-2-at-s-speed", "non-precision-approach.landing-gear", "non-precision-approach.flaps-3",
                "non-precision-approach.flaps-full"}
    assert set(g) >= expected and all(i in cl.links for i in g)
    assert "delay_s" not in str(g["takeoff.packs-pb"]) and g["takeoff.packs-pb"] == {"agl_ge_ref": "fld.thr_red_agl"}  # i 10 s sono l'intervallo tra i pack
    assert "ils-approach.flaps-1-at-green-dot-speed" not in g and "takeoff.autopilot" not in g or "show" in cl.links["takeoff.autopilot"]["check"]
    gates_json = json.loads((HERE / "checklist_gates.json").read_text(encoding="utf-8"))
    bad = [{"subphases": {"nope": {"alt_ge": 1}}}, {"items": {"nope": {"alt_ge": 1}}}, {"subphases": {"climb.at-10000-ft": {"zzz": 1}}},
           {"subphases": {"climb.at-10000-ft": {"alt_ge_ref": "fld.v1"}}}, {"subphases": {"climb.at-10000-ft": {"agl_ge_ref": "fld.ta"}}},
           {"subphases": {"climb.at-10000-ft": {}}}, {"subphases": {"climb.at-10000-ft": {"alt_ge": "x"}}}, {"foo": {}},
           {"items": {"takeoff.at-s-speed-flaps-0": {"after_item": "nope"}}},
           {"items": {"takeoff.at-f-speed-flaps-1": {"after_item": "takeoff.at-s-speed-flaps-0"}}},  # non precedente
           {"items": {"climb.barometric-reference": {"after_item": "takeoff.at-s-speed-flaps-0"}}},  # altra fase
           {"items": {"takeoff.positive-climb": {"alt_ge": 1}}}]  # voce senza link
    for b in bad:
        try:
            C.Checklist(cl.data, cl.links, None, b)
            assert False, b
        except ValueError:
            pass
    C.Checklist(cl.data, cl.links, None, gates_json)


def test_gates_in_state_takeoff_and_climb():
    st = _gated_state("takeoff")
    st.set_core({"on_ground": 1, "agl": 0, "alt": 100, "vs": 0})
    st.set_monitor({"gear": 1})
    r = _rows(st)
    assert r["takeoff.landing-gear"]["status"] == "wait" and r["takeoff.landing-gear"]["gate"] == "airborne"
    assert r["takeoff.packs-pb"]["status"] == "wait" and r["takeoff.packs-pb"]["gate"] == "after thrust reduction 1500 ft AGL"
    assert st.snapshot()["checklist"]["bad"] == sum(i["status"] == "bad" for i in _rows(st).values())  # wait non conta in bad
    st.set_core({"on_ground": 0, "agl": 100, "alt": 200, "vs": 800})
    st.set_monitor({"gear": 1})
    r = _rows(st)
    assert r["takeoff.landing-gear"]["status"] == "bad" and r["takeoff.landing-gear"]["kind"] == "mismatch"  # gate aperto: valutata
    st.set_core({"on_ground": 0, "agl": 100, "alt": 200, "vs": 0})  # un gate aperto resta aperto (VS oscilla)
    st.set_monitor({"gear": 1})
    assert _rows(st)["takeoff.landing-gear"]["status"] == "bad"
    st.set_core({"on_ground": 0, "agl": 1600, "alt": 2000, "vs": 800})
    st.set_monitor({"gear": 0, "pack1": 1, "pack2": 1})
    r = _rows(st)
    assert r["takeoff.packs-pb"]["status"] == "ok"  # subito valutata, nessun ritardo
    assert r["takeoff.at-s-speed-flaps-0"]["status"] == "wait" and r["takeoff.at-s-speed-flaps-0"]["gate"].startswith("after ")
    st.set_monitor({"flap": 1})
    st.set_monitor({"flap": 1})
    assert _rows(st)["takeoff.at-f-speed-flaps-1"]["status"] == "ok"
    st.set_monitor({"flap": 0})
    assert _rows(st)["takeoff.at-s-speed-flaps-0"]["status"] in ("ok", "bad")  # dopo Flaps 1 il gate si apre
    st.set_cl_phase("climb")
    st.set_core({"on_ground": 0, "agl": 3000, "alt": 5000, "vs": 800})
    st.set_monitor({"baro_std": 0, "baro_hpa": 1013})
    r = _rows(st)
    assert r["climb.barometric-reference"]["status"] == "wait" and r["climb.barometric-reference"]["gate"] == "after TA 6000 ft"
    assert r["climb.landing-lights"]["gate"] == "above 10000 ft"
    st.set_core({"on_ground": 0, "agl": 7000, "alt": 7000, "vs": 800})
    st.set_monitor({"baro_std": 0, "baro_hpa": 1013})
    assert _rows(st)["climb.barometric-reference"]["status"] == "bad"
    st.fields["ta"] = ""
    st.set_cl_phase("takeoff")
    st.set_cl_phase("climb")  # reset dei gate con il cambio di fase
    st.set_monitor({"baro_std": 0, "baro_hpa": 1013})
    assert _rows(st)["climb.barometric-reference"]["gate"] == "TA not set"


def test_gates_descent_and_reset():
    st = _gated_state("descent-checklist")
    st.set_core({"on_ground": 0, "agl": 20000, "alt": 20000, "vs": -1500})
    st.set_monitor({"landing_l": 0, "landing_r": 0})
    assert _rows(st)["descent-checklist.landing-lights"]["gate"] == "below 10000 ft"
    st.set_core({"on_ground": 0, "agl": 9000, "alt": 9000, "vs": -1500})
    st.set_monitor({"landing_l": 0, "landing_r": 0})
    assert _rows(st)["descent-checklist.landing-lights"]["status"] == "bad"
    assert "descent-checklist.landing-lights" in st.gates["open"]
    st.set_phase("briefing")
    assert not st.gates["open"] and not st.gates["done"]
    st.set_phase("flying")
    st.set_cl_phase("descent-checklist")
    st.set_core({"on_ground": 0, "agl": 20000, "alt": 20000, "vs": -1500})
    st.set_monitor({"landing_l": 0, "landing_r": 0})
    assert _rows(st)["descent-checklist.landing-lights"]["status"] == "wait"


def test_gates_reset_only_when_going_back():
    st = _gated_state("takeoff")
    st.gates["open"].update({"takeoff.landing-gear", "climb.landing-lights"})
    st.gates["done"].add("takeoff.at-f-speed-flaps-1")
    st.set_cl_phase("takeoff")  # ricliccare la fase corrente
    st.set_cl_phase(1)
    st.set_cl_phase("climb")
    assert st.gates["open"] == {"takeoff.landing-gear", "climb.landing-lights"} and st.gates["done"] == {"takeoff.at-f-speed-flaps-1"}
    st.set_cl_phase("climb")
    assert len(st.gates["open"]) == 2
    st.set_cl_phase(-1)  # indietro: azzera destinazione e successive
    assert st.cl_phase == "after-takeoff" and st.gates["open"] == {"takeoff.landing-gear"} and st.gates["done"] == {"takeoff.at-f-speed-flaps-1"}
    st.set_cl_phase("takeoff")
    assert not st.gates["open"] and not st.gates["done"]


def test_gates_landing_and_go_around():
    cl = C.load()
    assert cl.gates["landing.thrust-levers"] == {"agl_le": 20} and cl.gates["landing.ground-spoilers"] == {"ground": True}
    assert cl.gates["landing.thrust-levers_2"] == {"ground": True, "ias_le": 70} and cl.gates["landing.thrust-levers_3"] == {"ground": True, "ias_le": 25}
    assert cl.gates["go-around.landing-gear"] == {"vs_gt": 0}
    st = _gated_state("landing")
    st.set_core({"on_ground": 0, "agl": 300, "alt": 400, "vs": -700, "ias": 140})
    st.set_monitor({"spoilers": 0})
    assert _rows(st)["landing.ground-spoilers"]["gate"] == "on ground"  # le voci thrust-levers sono detent: gate attivo anche sulle leve
    st.set_core({"on_ground": 1, "agl": 0, "alt": 100, "vs": 0, "ias": 60})
    st.set_monitor({"spoilers": 0})
    assert _rows(st)["landing.ground-spoilers"]["status"] in ("ok", "bad")


def test_gates_approach_ladder():
    st = _gated_state("ils-approach")
    st.set_core({"on_ground": 0, "agl": 3000, "alt": 3000, "vs": -700})
    st.set_monitor({"flap": 0, "gear": 1})
    r = _rows(st)
    assert r["ils-approach.flaps-1-at-green-dot-speed"]["status"] == "bad"  # primo gradino senza gate
    assert r["ils-approach.flaps-2"]["status"] == "wait" and r["ils-approach.landing-gear"]["status"] == "wait"
    assert r["ils-approach.flaps-2"]["gate"].startswith("after Flaps 1")
    st.set_monitor({"flap": 1, "gear": 1})
    r = _rows(st)
    assert r["ils-approach.flaps-1-at-green-dot-speed"]["status"] == "ok" and r["ils-approach.flaps-2"]["status"] == "bad"
    assert r["ils-approach.landing-gear"]["status"] == "wait" and r["ils-approach.flaps-3"]["status"] == "wait"


if __name__ == "__main__":
    test_gates_file_validation_and_inventory()
    test_gates_in_state_takeoff_and_climb()
    test_gates_landing_and_go_around()
    test_fob_zfw_items_and_tolerance()
    test_briefing_complete_restarts_checklist()
    test_inputs_items()
    test_flap_and_taxi_alt_links()
    test_files_valid()
    test_links_reference_real_things()
    test_new_links_wellformed()
    test_new_links_show_optional()
    test_thrust_levers_show()
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


def test_tcas_links():
    cl = C.load()
    for k, eq in (("before-takeoff.tcas-mode-selector", ["TA", "TA/RA"]), ("after-takeoff.tcas-mode-selector", "TA/RA"),
                  ("ils-approach.tcas", ["TA", "TA/RA"]), ("non-precision-approach.tcas", ["TA", "TA/RA"]),
                  ("after-landing.tcas-mode-selector", "STBY")):
        assert cl.links[k] == {"check": {"key": "tcas", "eq": eq}, "verified": True}, k
    by = {i.key: i for i in mon.ITEMS}
    assert by["tcas"].lvar == "S_XPDR_MODE" and by["tcas"].group == "Transponder"
    assert mon.label(by["tcas"], 2) == "TA/RA (2)" and mon.clean_label(by["tcas"], 1) == "TA"
    assert mon.label(by["xpdr_operation"], 3) == "3" and by["xpdr_atc"].lvar == "S_XPDR_ATC"


def test_cabin_fuel_links():
    cl = C.load()
    for k, key, eq in (("cockpit-preparation.no-smoking-elec-dev-signs-sw", "smoking", "AUTO"),
                       ("cockpit-preparation.emergency-exit-lights-sw", "emer_lt", "ARM"),
                       ("securing-the-aircraft.no-smoking", "smoking", "OFF")):
        assert cl.links[k]["check"] == {"key": key, "eq": eq} and cl.links[k]["verified"] is True, k
    for k, eq in (("cockpit-preparation.fuel-pump-pb", "ON"), ("parking.fuel-pumps", "OFF")):
        assert C.check_keys(cl.links[k]["check"]) == ["fuel_l1", "fuel_l2", "fuel_c1", "fuel_c2", "fuel_r1", "fuel_r2"]
        assert {c["eq"] for c in cl.links[k]["check"]["all"]} == {eq} and cl.links[k]["verified"] is True
    by = {i.key: i for i in mon.ITEMS}
    assert mon.label(by["smoking"], 1) == "AUTO (1)" and mon.clean_label(by["emer_lt"], 1) == "ARM"



def test_gsx_links():
    cl = C.load()
    fc, bd = "cockpit-preparation.start-fueling-catering", "cockpit-preparation.start-boarding-passengers"
    assert cl.links[fc]["check"] == {"gsx": ["gsx_refuel", "gsx_cater"]} and cl.links[bd]["check"] == {"gsx": ["gsx_board"]}
    cr, cp = "cockpit-preparation.check-refueling-completed", "cockpit-preparation.start-boarding-passengers"
    assert cl.links[cr]["check"] == {"gsx": ["gsx_refuel"], "set": "done"} and cr not in cl.excluded
    ids = [i["id"] for p in cl.phases for sp in p["subphases"] for i in sp["items"]]
    assert ids.index(cr) + 1 == ids.index(cp) and next(i for p in cl.phases for sp in p["subphases"] for i in sp["items"] if i["id"] == cr)["type"] == "announce"
    cb = "fmgs-performance-glareshield.check-boarding-completed"
    assert cl.links[cb]["check"] == {"gsx": ["gsx_board"], "set": "done"} and cb not in cl.excluded
    for chk in ({"gsx": ["gsx_board"], "set": "x"}, {"gsx": ["gsx_board"], "set": "done", "y": 1}):
        try:
            C.Checklist(cl.data, {cb: {"check": chk, "verified": True}})
            assert False, chk
        except ValueError:
            pass
    assert C.check_keys(cl.links[fc]["check"]) == ["gsx_refuel", "gsx_cater"]
    ph = [p["id"] for p in cl.phases if any(i["id"] == fc for sp in p["subphases"] for i in sp["items"])][0]
    labels = mon.clean_labels({"gsx_refuel": 6, "gsx_cater": 5, "gsx_board": 4})
    rows = {i["id"]: i for s in cl.evaluate_phase(ph, labels, raw={"gsx_refuel": 6, "gsx_cater": 5, "gsx_board": 4})["subphases"] for i in s["items"]}
    assert rows[fc]["status"] == "ok" and rows[fc]["raw"] == [["FSDT_GSX_REFUELING_STATE", "6"], ["FSDT_GSX_CATERING_STATE", "5"]]
    assert rows[bd]["status"] == "bad" and rows[bd]["type"] == "announce"
    # grezzo sempre accanto allo stato (live_raw, anche senza --test-links) e riga NOW sempre mostrata
    lv = {i["id"]: i for s in cl.evaluate_phase(ph, labels, live_raw={"gsx_refuel": 6, "gsx_cater": 5, "gsx_board": 1})["subphases"] for i in s["items"]}
    assert "raw" not in lv[bd] and lv[bd]["show"] is True and lv[bd]["live"] == [["GSX", "Boarding AVAIL"]]
    assert lv[fc]["live"] == [["GSX", "Refuel COMPLETED \u00b7 Catering IN PROGRESS"]]
    nr = {i["id"]: i for s in cl.evaluate_phase(ph, mon.clean_labels({}))["subphases"] for i in s["items"]}
    assert nr[bd]["live"] == [["GSX", "Boarding n/d"]]
    muto = {i["id"]: i for s in cl.evaluate_phase(ph, mon.clean_labels({}))["subphases"] for i in s["items"]}
    assert muto[fc]["status"] == "na"
    # check gsx ammesso solo su announce e solo con chiavi gsx; gli altri tipi restano senza link
    atc = next(i["id"] for p in cl.phases for sp in p["subphases"] for i in sp["items"] if i["type"] == "atc")
    act = next(i["id"] for p in cl.phases for sp in p["subphases"] for i in sp["items"] if i["type"] == "action")
    for iid, chk in ((atc, {"gsx": ["gsx_board"]}), (act, {"gsx": ["gsx_board"]}), (fc, {"gsx": ["apu_master"]}), (fc, {"gsx": []}), (fc, {"gsx": ["gsx_board"], "x": 1}),
                     (fc, {"key": "apu_master", "eq": "ON"}), (fc, {"all": [{"gsx": ["gsx_board"]}]})):
        try:
            C.Checklist(cl.data, {iid: {"check": chk, "verified": True}})
            assert False, (iid, chk)
        except ValueError:
            pass

if __name__ == "__main__":
    test_tcas_links()
    test_cabin_fuel_links()
    test_gsx_links()
    test_ai_links()
    test_snapshot_ai_with_metar()
    print("ok tcas/cabin")
