import json
import pathlib
import tempfile
import urllib.error
import urllib.request

import web
from state import State


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r) if "json" in r.headers["Content-Type"] else r.read().decode()


def test_api_flow():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "cfg.json"
    st = State(cfg)
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert "Virtual Captain" in call(base, "/")
        s = call(base, "/api/state")
        assert s["phase"] == "briefing" and s["fields"]["ta"] == ""
        s = call(base, "/api/fields", {"ta": "6000", "accel_agl": "1000", "thr_red_agl": "800", "mode": "training", "simbrief": "x"})
        assert s["fields"]["ta"] == "6000" and s["mode"] == "training"
        b = st.briefing()
        assert (b.ta, b.thr_red_agl, b.accel_agl, b.mode) == (6000, 800, 1000, "training")
        assert s["monitor"] == []  # vuoto in briefing
        assert call(base, "/api/phase", {"phase": "flying"})["phase"] == "flying"
        st.set_monitor({"apu_master": 1, "landing_l": 2, "pack1": None})
        mon = dict(call(base, "/api/state")["monitor"])
        assert mon["APU / Air"][0] == ["APU master", "ON", "Prep: ON · After start: OFF", "1"]
        assert [r for r in mon["APU / Air"] if r[0] == "Pack 1"][0][1] == "n/d"
        assert "Cockpit state" in call(base, "/") and "textContent" in call(base, "/")
        # si ricordano solo ID, modalità e accelerazione
        saved = json.loads(cfg.read_text())
        assert saved == {"accel_agl": "1000", "thr_red_agl": "800", "simbrief": "x", "mode": "training",
                     "aao_url": "http://127.0.0.1:43380/webapi", "checklist_phase": "sim-preparation",
                     "checklist_mode": "complete"}
        assert State(cfg).fields["ta"] == "" and State(cfg).fields["accel_agl"] == "1000"
    finally:
        srv.shutdown()


def test_checklist_mode_endpoint():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st = State(cfg)
    st.set_cl_phase("takeoff")  # fase visibile in entrambe le modalita'
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        s = call(base, "/api/state")
        assert s["checklist_mode"] == "complete" and s["cl_modes"] == ["complete", "simplified"]
        full = sum(len(sp["items"]) for sp in s["checklist"]["subphases"])
        s = call(base, "/api/fields", {"checklist_mode": "simplified"})
        assert s["checklist_mode"] == "simplified"
        assert sum(len(sp["items"]) for sp in s["checklist"]["subphases"]) < full
        assert json.loads(cfg.read_text())["checklist_mode"] == "simplified"
        assert State(cfg).checklist_mode == "simplified"  # persistenza
        assert call(base, "/api/fields", {"ta": "5000"})["checklist_mode"] == "simplified"  # assente: invariato
        assert call(base, "/api/fields", {"checklist_mode": "boh"})["checklist_mode"] == "complete"
        assert 'id="clmode"' in call(base, "/")
        cfg.write_text(json.dumps({"checklist_mode": "x"}))
        assert State(cfg).checklist_mode == "complete"
    finally:
        srv.shutdown()


def test_checklist_phase_endpoint():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body):
        try:
            return 200, call(base, "/api/checklist-phase", body)
        except urllib.error.HTTPError as e:
            return e.code, None
    try:
        code, s = post({"phase": "climb"})
        assert code == 200 and s["cl_phase"] == "climb" and s["checklist"]["phase"]["id"] == "climb" and s["phase"] == "briefing"
        assert post({"step": 1})[1]["cl_phase"] == "cruise" and post({"step": -1})[1]["cl_phase"] == "climb"
        for bad in ({"phase": "nope"}, {"phase": 3}, {"step": 2}, {"step": "x"}, {}, [1]):
            assert post(bad) == (400, None), bad
        assert st.cl_phase == "climb"
        html = call(base, "/")
        assert "Next phase" in html and "clnextbtn" in html and "/api/checklist-phase" in html
        try:
            call(base, "/api/checklist-phase")  # GET non scrive
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_phase_bar_hides_empty_phase_endpoint():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        ids = lambda s: [p["id"] for p in s["cl_phases"]]
        assert "sim-preparation" in ids(call(base, "/api/state"))
        s = call(base, "/api/fields", {"checklist_mode": "simplified"})
        assert "sim-preparation" not in ids(s) and s["cl_phase"] == ids(s)[0]
        assert call(base, "/api/checklist-phase", {"step": -1})["cl_phase"] == ids(s)[0]
        assert call(base, "/api/checklist-phase", {"step": 1})["cl_phase"] == ids(s)[1]
        try:
            call(base, "/api/checklist-phase", {"phase": "sim-preparation"})
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()


def test_monitor_cleared_outside_flight():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.set_monitor({"apu_master": 1})
    assert st.snapshot()["monitor"] == []
    st.set_phase("flying")
    st.set_monitor({"apu_master": 1})
    assert st.snapshot()["monitor"]
    st.set_phase("briefing")
    assert st.snapshot()["monitor"] == []


def test_aao_parse_missing_variable():
    from aao import AAO, VARS, MONITOR
    ll = MONITOR["landing_l"]["lvar"]
    data = {"getvars": [{"var": VARS["ias"], "value": 120}, {"var": ll, "value": 2},
                        {"var": MONITOR["nose"]["lvar"], "value": "abc"},
                        {"var": MONITOR["apu_master"]["std"], "value": 1}]}
    values, monitor = AAO._parse(data)
    assert values["ias"] == 120.0 and values["vs"] == 0.0 and set(values) == set(VARS)
    assert monitor["landing_l"] == 2.0
    assert monitor["landing_r"] is None and monitor["nose"] is None and monitor["pack1"] is None
    assert monitor["apu_master"] == 1.0  # L-var assente, ripiega sulla standard
    assert monitor["apu_bleed"] is None and monitor["eng1"] is None
    assert monitor["gear"] is None
    v2, m2 = AAO._parse({"getvars": [{"var": VARS["gear"], "value": 1}]})
    assert v2["gear"] == 1.0 and m2["gear"] == 1.0 and all(v is None for k, v in m2.items() if k != "gear")
    assert set(m2) == set(MONITOR)


def test_aao_read_falls_back_to_core():
    import aao as A
    a = A.AAO()
    calls = []

    def fake(names, timeout):
        calls.append(len(names))
        if len(names) > len(A.VARS):
            raise ValueError("lvar")
        return {"getvars": [{"var": v, "value": 1} for v in names]}
    a._get = fake
    values, monitor = a.read_all()
    assert calls == [calls[0], len(A.VARS)] and calls[0] > len(A.VARS)
    assert values["ias"] == 1.0 and all(v is None for v in monitor.values())
    a.read_all()  # entro 30 s: solo core, niente richiesta piena
    assert calls[2:] == [len(A.VARS)]
    a._core_only_until = 0.0  # scaduto: riprova la piena
    a.read_all()
    assert calls[3] > len(A.VARS)
    assert a.read() == values


def test_aao_unreachable_not_doubled():
    import aao as A
    a = A.AAO()
    n = []

    def fake(names, timeout):
        n.append(1)
        raise OSError("down")
    a._get = fake
    try:
        a.read_all()
        assert False
    except OSError:
        pass
    assert len(n) == 1


def test_aao_requests_only_used_vars():
    import aao as A
    from monitor import ITEMS
    for it in ITEMS:
        m = A.MONITOR[it.key]
        assert (m["std"] is not None) == bool(it.std and (it.std_fallback or not it.lvar)), it.key
    assert A.MONITOR["landing_l"]["std"] is None and A.MONITOR["flap"]["std"] is None
    assert A.MONITOR["gear"]["std"] and A.MONITOR["seatbelt"]["std"]


def test_aao_parse_dict_format_and_values_unchanged():
    import aao as A
    d = {A.VARS["ias"]: 150, A.VARS["on_ground"]: 1, A.VARS["gear"]: None}
    values, monitor = A.AAO._parse(d)
    assert values == {"on_ground": 1.0, "vs": 0.0, "agl": 0.0, "alt": 0.0, "ias": 150.0, "gear": 0.0,
                      "landing": 0.0, "seatbelt": 0.0}
    assert monitor["gear"] is None and monitor["apu_master"] is None
    v2, _ = A.AAO._parse({"getvars": [{"var": A.VARS["vs"], "value": "2000"}]})
    assert v2["vs"] == 2000.0 and v2["ias"] == 0.0


def test_bad_values_use_defaults():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.update({"ta": "abc", "accel_agl": ""})
    b = st.briefing()
    assert (b.ta, b.thr_red_agl, b.accel_agl) == (None, None, None)  # non noti: nessun default presunto


def test_briefing_fields_and_inflight():
    import state as S
    assert [k for k, _ in S.FIELDS] == ["origin", "destination", "dep_time"]
    assert [k for k, _ in S.INFLIGHT] == ["runway", "ta", "thr_red_agl", "accel_agl", "v1", "vr", "v2", "flap"]
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    s = st.snapshot()
    assert [k for k, _ in s["labels"]] == ["origin", "destination", "dep_time"] and set(s["fields"]) >= {"runway", "ta", "v1", "flap"}
    # origin "ICAO/RWY": la pista deriva da li' e viceversa
    st.update({"origin": "limc/17r"})
    assert st.fields["origin"] == "LIMC/17R" and st.fields["runway"] == "17R"
    st.update({"runway": "35l"})  # dalla checklist
    assert st.fields["origin"] == "LIMC/35L" and st.fields["runway"] == "35L"
    st.update({"runway": "zz"})  # non valida: ignorata
    assert st.fields["runway"] == "35L"
    st.update({"runway": ""})
    assert st.fields["origin"] == "LIMC" and st.fields["runway"] == ""
    st.update({"origin": "LIMC"})
    assert st.fields["runway"] == ""
    st.update({"v1": " 140 ", "vr": "141", "v2": "145", "flap": "1+F", "ta": "6000", "thr_red_agl": "800", "accel_agl": "1000"})
    assert (st.fields["v1"], st.fields["flap"]) == ("140", "1+F")
    b = st.briefing()
    assert (b.ta, b.thr_red_agl, b.accel_agl, b.v1, b.vr) == (6000, 800, 1000, 140, 141)
    # solo thr_red e accel si ricordano nel file; il resto no
    assert State(st.cfg_path).fields["thr_red_agl"] == "800" and State(st.cfg_path).fields["v1"] == ""
    # il QNH usa l'ICAO, non la pista
    st.update({"origin": "LIRF/16L"})
    got = []
    st.weather.get = lambda icao: got.append(icao) or 1013.0
    assert st._expected()["wx.qnh_dep"] == "1013 hPa" and got[0] == "LIRF"


def test_load_plan_dep_and_reset():
    import state as S
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    orig = (S.fetch_latest, S.to_briefing)
    S.fetch_latest = lambda uid: {}
    S.to_briefing = lambda ofp: {"origin": "LIMC/17R", "destination": "EDDF", "runway": "17R", "dep_time": "10:00", "ta": 6000, "generated": 1,
                                 "v1": 140, "vr": None, "v2": None, "flap": "1+F", "plan": {"orig_metar": "LIMC 1Z Q1011"}}
    try:
        st.load_plan("x")
        assert (st.fields["origin"], st.fields["runway"], st.fields["ta"], st.fields["v1"], st.fields["flap"]) == ("LIMC/17R", "17R", "6000", "140", "1+F")
        assert st.weather.get("LIMC") == 1011.0 and "T/O runway" not in st.info and "VR" in st.info
        st.update({"runway": "35L", "ta": "5000", "v1": "150", "thr_red_agl": "900"})
        st.update({"reset": ["runway", "ta", "v1", "bogus"]})
        assert (st.fields["origin"], st.fields["ta"], st.fields["v1"], st.fields["thr_red_agl"]) == ("LIMC/17R", "6000", "140", "900")
        S.to_briefing = lambda ofp: {"origin": "LIMC", "destination": "EDDF", "runway": None, "dep_time": None, "ta": None, "generated": 1,
                                     "v1": None, "vr": None, "v2": None, "flap": None, "plan": {}}
        st.load_plan("x")
        assert (st.fields["origin"], st.fields["runway"], st.fields["ta"]) == ("LIMC", "", "") and "T/O runway" in st.info
        st.update({"runway": "09"})
        st.update({"reset": ["runway"]})  # il piano non ha pista: torna vuota
        assert st.fields["runway"] == "" and st.fields["origin"] == "LIMC"
    finally:
        S.fetch_latest, S.to_briefing = orig


def test_to_briefing_dep_runway():
    from simbrief import to_briefing
    assert to_briefing({"origin": {"icao_code": "LIMC", "plan_rwy": "17R"}})["origin"] == "LIMC/17R"
    assert to_briefing({"origin": {"icao_code": "limc", "plan_rwy": "09"}})["origin"] == "LIMC/09"
    assert to_briefing({"origin": {"icao_code": "LIMC"}})["origin"] == "LIMC"
    assert to_briefing({})["origin"] is None


def test_inputs_markup():
    assert "adv()" in __import__("web").PAGE and r"/^\d{3}$/.test(i.value)" in __import__("web").PAGE  # 3 cifre su v1/vr/v2: focus al campo successivo
    p = web.PAGE
    for m in ("function inputsNode", "function syncInputs", "function inpSend", "function fStart", "function fShow", "dataset.f", "/api/fields", "reset:ks",
              "setTimeout(inpSend,400)", ".a.inpr .fld", "i.onblur=end", 'e.key==="Enter"', "i.remove();fShow(el)", 'b.title="Reset to plan"',
              'placeholder="LIMC/17R"', "el.dataset.r", '"---"'):
        assert m in p, m
    # il campo e' il valore atteso a destra (nessun chip a sinistra, nessun duplicato)
    assert "tx.appendChild(inputsNode(it))" in p and "e.appendChild(inputsNode" not in p and ".chip.fld" not in p
    assert 'querySelector(\'.fld[data-f="\'+keepF[0]' in p  # il campo in modifica sopravvive alla ricostruzione
    key = p[p.index("bk=JSON.stringify"):p.index("showLive,s.test_links")]
    assert "i.inputs" in key and "fields" not in key  # i valori scritti non fanno ricostruire la lista
    chips = p[p.index("function chips"):p.index("function cockpit")]
    assert '"TA"' not in chips and '"V1"' not in chips and '"FLAP"' not in chips and '"RWY"' not in chips




def test_firewall_noop_off_windows():
    import firewall
    import os
    if os.name != "nt":
        assert firewall.ensure(8080) is True
    assert "100.64.0.0/10" in firewall.manual_command(8080)


def test_plan_extraction():
    from simbrief import to_briefing
    ofp = {"general": {"icao_airline": "RXP", "flight_number": "101", "route": "ROBEL UN851 TOP", "sid_ident": "ROBE1A",
                       "star_ident": "TOP2B", "initial_altitude": "36000", "air_distance": "450"},
           "times": {"est_time_enroute": "5400"}, "alternate": {"icao_code": "EDDK"}, "aircraft": {"icaocode": "A320"},
           "navlog": {"fix": [{"ident": "LIMC", "via_airway": "DCT", "altitude_feet": "0"}, {"ident": "ROBEL", "via_airway": "ROBE1A", "altitude_feet": "12000"}]}}
    p = to_briefing(ofp)["plan"]
    assert (p["flight"], p["sid"], p["star"], p["cruise_alt"], p["ete"], p["alternate"]) == ("RXP101", "ROBE1A", "TOP2B", 36000, "1h30", "EDDK")
    assert p["waypoints"][1] == {"ident": "ROBEL", "via": "ROBE1A", "alt": 12000}
    assert to_briefing({})["plan"]["route"] is None


def test_old_mode_values_migrate():
    import state
    assert state._mode("silent") == "normal" and state._mode("silenziosa") == "normal"
    assert state._mode("allenamento") == "training" and state._mode(None) == "normal"


def test_test_links_markup():
    assert 'class="rw"' in web.PAGE or '"rw"' in web.PAGE
    assert "s.test_links" in web.PAGE and ".cli.wl" in web.PAGE


def test_optional_markup():
    p = web.PAGE
    assert ".cli.st-opt{border-left-color:var(--opt)}" in p and ".cli.st-opt .ic{color:var(--opt)}" in p and "--opt:#5bc0eb" in p
    assert 'it.status==="opt"?"\\u25cb"' in p


def test_layout_markers():
    p = web.PAGE
    for m in ("--sp-h", "env(safe-area-inset-bottom)", "viewport-fit=cover", "min-width:900px", "ResizeObserver",
              'id="speech"', 'id="more"', 'id="hdr"', 'id="clbody"', 'id="edit"', 'id="end"', "columns:420px 2"):
        assert m in p, m
    assert 'id="nowtxt"' not in p


def test_clearance_endpoint():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert call(base, "/api/state")["clearance"] == {k: "" for k in ["c", "r", "a", "f", "t"]}
        s = call(base, "/api/clearance", {"a": " FL100 ", "t": "2000", "r": "abc dct def", "bogus": "x"})
        assert s["clearance"] == {"c": "", "r": "abc dct def", "a": "FL100", "f": "", "t": "2000"}
        for bad in ({"t": "9"}, {"t": "12345"}, {"a": {"x": 1}}):
            try:
                call(base, "/api/clearance", bad)
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 400
        try:
            call(base, "/api/clearance", [1])
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400
        assert call(base, "/api/state")["clearance"]["t"] == "2000"
        st.fields["destination"], st.plan = "LIPZ", {"route": "A DCT B"}
        s = call(base, "/api/clearance", {"reset": True})
        assert s["clearance"] == {"c": "LIPZ", "r": "A DCT B", "a": "", "f": "", "t": ""}
    finally:
        srv.shutdown()


def test_craft_markup():
    p = web.PAGE
    for m in ("/api/clearance", "function craftNode", "function syncCraft", ".craft{background", 'inputMode="numeric"', "i.maxLength=4",
              "Reset to plan", "setTimeout(clrSend,400)", "document.activeElement!==i", "it.expected||it.response", ".cli .a.set", "scroll-margin-bottom"):
        assert m in p, m
    key = p[p.index("bk=JSON.stringify"):p.index("showLive,s.test_links")]
    assert "clearance" not in key and "i.expected" in key  # i valori scritti dall'utente non fanno ricostruire la pagina


def test_squawk_pill_markup():
    p = web.PAGE
    for m in ('id="sqk"', "function sqkStart", "function sqkShow", 'api("/api/clearance",{t:v})', 'ev.key==="Escape"', "i.onblur=()=>end(true)",
              "sqkVal=c.t", "SQK", ".chip input{"):
        assert m in p, m
    assert "prompt(" not in p and "alert(" not in p


def test_squawk_pill_roundtrip():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "q.json")
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        s = call(base, "/api/clearance", {"t": "4512"})
        assert s["clearance"]["t"] == "4512"
        assert call(base, "/api/state")["clearance"]["t"] == "4512"
        for bad in ("4812", "12345", "ab"):
            try:
                call(base, "/api/clearance", {"t": bad})
                assert False, bad
            except Exception:
                pass
        assert call(base, "/api/state")["clearance"]["t"] == "4512"
        assert call(base, "/api/clearance", {"t": ""})["clearance"]["t"] == ""
    finally:
        srv.shutdown()


def test_spoke_counters():
    with tempfile.TemporaryDirectory() as d:
        st = State(pathlib.Path(d) / "c.json")
        s = st.snapshot()
        assert s["spoke_n"] == 0 and s["spoke_ago"] is None and "now" in s
        st.add_log("one")
        st.add_log("two")
        s = st.snapshot()
        assert s["spoke_n"] == 2 and 0 <= s["spoke_ago"] < 5 and s["now"] >= st.spoke_at


def test_sim_finish_keeps_flying():
    import main
    src = iter([({"a": 1}, {"m": 1}), ({"a": 2}, {"m": 2})])
    last, done, said = None, False, []
    for _ in range(6):
        sample, phrase, done, finished = main.sim_read(src, last, done)
        if phrase:
            said.append(phrase)
        if not finished:
            last = sample
    assert said == ["Simulated flight finished."] and done and last == ({"a": 2}, {"m": 2}) and sample == last
    with tempfile.TemporaryDirectory() as d:
        st = State(pathlib.Path(d) / "c.json")
        st.set_phase("flying")
        st.set_monitor(None)
        assert st.phase == "flying"


def _cal_state(cfg):
    """State con orologio finto: clock[0] e' l'istante corrente."""
    st = State(cfg)
    clock = [1000.0]
    st.clock = lambda: clock[0]
    return st, clock


def _feed(st, clock, l, r, pos3=7, pos4=8):
    clock[0] += 1
    st.set_calib_raw({"thr_in_l": l, "thr_in_r": r, "thr_pos_1": l, "thr_pos_2": r, "thr_pos_3": pos3, "thr_pos_4": pos4,
                      "thr_gate_l": l / 100, "thr_gate_r": r / 100}, clock[0])


def test_thrust_cal_flow():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st, clock = _cal_state(cfg)
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(action):
        return call(base, "/api/thrust-cal", {"action": action})

    def err(body):
        try:
            call(base, "/api/thrust-cal", body)
        except urllib.error.HTTPError as e:
            assert e.code == 400
            return e.read().decode()
        raise AssertionError(body)
    try:
        s = call(base, "/api/state")
        assert s["thr_cal"] is None and s["calib"] is None
        assert "Not calibrated" in call(base, "/") or "thrcal" in call(base, "/")
        # errori
        assert err({"action": "boh"}) == "unknown action" and err({"action": "next"}) == "calibration not in progress"
        assert err({}) == "unknown action" and err([1]) == "body must be an object"
        assert err({"action": "skip"}) == "calibration not in progress"
        # start: nessun dato
        s = post("start")
        c = s["calib"]
        assert c["active"] and c["step"] == 0 and c["total"] == 6 and c["name"] == "FULL REVERSE" and not c["can_skip"]
        assert c["text"] == "Move both thrust levers to FULL REVERSE, then press Next" and not c["has_live"]
        assert err({"action": "next"}) == "no live data from AAO"
        assert err({"action": "skip"}) == "nothing to keep: not calibrated yet"
        # dati vecchi (oltre 3 s) rifiutati
        _feed(st, clock, -20, 15)
        clock[0] += 10
        assert err({"action": "next"}) == "no live data from AAO"
        # sei passi; le due leve sono opposte (L crescente, R decrescente)
        L = [-20, -5, 0, 25, 50, 100]
        R = [30, 25, 10, 0, -10, -30]
        for i in range(6):
            if i:  # leve ancora al passo precedente: rifiutato
                for _ in range(3):
                    _feed(st, clock, L[i - 1], R[i - 1])
                assert err({"action": "next"}) == "lever not moved"
                assert err({"action": "skip"}) == "nothing to keep: not calibrated yet"
            for _ in range(3):
                _feed(st, clock, L[i], R[i])
            c = call(base, "/api/state")["calib"]
            assert c["step"] == i and c["has_live"] and c["rows"][0]["live"] == str(L[i])
            s = post("next")
        c = s["calib"]
        assert c["done"] and c["step"] == 6 and c["heads"] == ["REV", "R.IDLE", "IDLE", "CLB", "FLX", "TOGA"]
        assert c["rows"][0]["vals"] == [str(x) for x in L] and c["rows"][4]["name"].endswith("pos 3 (raw)") and c["rows"][4]["vals"] == ["7"] * 6
        t = s["thr_cal"]
        assert t["guides"] == {"l": "Throttle input L (raw)", "r": "Throttle input R (raw)"} and t["when"]
        saved = json.loads(cfg.read_text())
        assert saved["thrust_cal"]["levers"]["l"]["var"] == "thr_in_l" and saved["thrust_cal"]["levers"]["r"]["v"]["TOGA"] == -30
        assert saved["thrust_cal"]["raw"]["TOGA"]["thr_pos_3"] == 7 and saved["thrust_cal"]["raw"]["TOGA"]["thr_pos_4"] == 8
        assert s["checklist"] is not None
        # persistenza e caricamento
        st2 = State(cfg)
        assert st2.thrust_cal == st.thrust_cal and st2._expected()["cal.thrust"] == "1"
        # _save non perde thrust_cal
        call(base, "/api/fields", {"ta": "5000"})
        st.set_cl_phase("takeoff")
        assert json.loads(cfg.read_text())["thrust_cal"] == saved["thrust_cal"]
        # chiusura del riepilogo (cancel dopo la fine non tocca la calibrazione)
        s = post("cancel")
        assert s["calib"] is None and s["thr_cal"] is not None
        # in volo i detent si leggono e finiscono nel monitor
        call(base, "/api/phase", {"phase": "flying"})
        st.set_monitor({"thr_in_l": 25, "thr_in_r": 0})
        mon = dict(call(base, "/api/state")["monitor"])
        rows = {r[0]: r for r in mon["Thrust"]}
        assert rows["Thrust lever L detent"][1] == "CLIMB" and rows["Thrust lever R detent"][1] == "CLIMB"
        assert st.labels["thr_det_l"] == "CLIMB" and st.raw["thr_det_r"] == 3
        st.set_monitor({"thr_in_l": 12, "thr_in_r": None})
        assert st.labels["thr_det_l"] == "BETWEEN" and st.labels["thr_det_r"] is None
        call(base, "/api/phase", {"phase": "briefing"})
        # modifica: skip tiene il vecchio valore; back; cancel non tocca la vecchia
        s = post("start")
        assert s["calib"]["can_skip"]
        _feed(st, clock, -20, 30)
        s = post("next")
        assert s["calib"]["step"] == 1
        s = post("back")
        assert s["calib"]["step"] == 0 and s["calib"]["heads"] == []
        assert post("back")["calib"]["step"] == 0
        for _ in range(6):
            s = post("skip")
        assert s["calib"]["done"] and s["thr_cal"]["guides"]["l"] == "Throttle input L (raw)"
        assert st.thrust_cal["levers"]["l"]["v"]["IDLE"] == 0.0 and s["calib"]["rows"][0]["vals"] == ["kept"] * 6
        post("cancel")
        old = json.loads(json.dumps(st.thrust_cal))
        post("start")
        _feed(st, clock, 1, 1)
        post("next")
        s = post("cancel")
        assert s["calib"] is None and st.thrust_cal == old
        # clear
        s = post("clear")
        assert s["thr_cal"] is None and "thrust_cal" not in json.loads(cfg.read_text()) and st._expected()["cal.thrust"] == ""
        st.set_monitor({"thr_in_l": 25})  # briefing: ignorato
    finally:
        srv.shutdown()


def test_thrust_cal_failure_and_invalid_file():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "c.json"
    st, clock = _cal_state(cfg)
    st.calib_start()
    vals = [(-20, 5), (-5, 5), (0, 5), (25, 5), (50, 5), (100, 5)]  # R non cambia mai (ma resta "mossa" perche' cambia L)
    for i, (l, r) in enumerate(vals):
        _feed(st, clock, l, r)
        if i < 5:
            st.calib_next()
        else:
            try:
                st.calib_next()
                assert False
            except ValueError as e:
                assert "lever R" in str(e) and "thr_in_r=5,5,5,5,5,5" in str(e)
    assert st.calib["step"] == 5 and not st.calib["done"] and st.thrust_cal is None and st.calib["error"]
    assert not cfg.exists() or "thrust_cal" not in json.loads(cfg.read_text())
    assert "Calibration failed" in st.snapshot()["calib"]["error"]
    # file di configurazione con calibrazione non valida: ignorata
    cfg.write_text(json.dumps({"thrust_cal": {"at": 1, "levers": {}, "raw": {}}}))
    assert State(cfg).thrust_cal is None
    cfg.write_text(json.dumps({"thrust_cal": "x"}))
    assert State(cfg).thrust_cal is None


def test_thrust_cal_markup_and_main():
    page = web.PAGE
    assert page.index('id="thrcal"') < page.index('id="aaourl"') and "/api/thrust-cal" in page and "Clear calibration" in page
    assert page.count("function calib(") == 1 and "Hold the levers still" in page
    import main
    import inspect
    assert "calib_active" in inspect.getsource(main.main) and "set_calib_raw" in inspect.getsource(main.main)


if __name__ == "__main__":
    test_thrust_cal_flow()
    test_thrust_cal_failure_and_invalid_file()
    test_thrust_cal_markup_and_main()
    test_clearance_endpoint()
    test_craft_markup()
    test_squawk_pill_markup()
    test_squawk_pill_roundtrip()
    test_layout_markers()
    test_test_links_markup()
    test_optional_markup()
    test_spoke_counters()
    test_sim_finish_keeps_flying()
    test_api_flow()
    test_bad_values_use_defaults()
    test_briefing_fields_and_inflight()
    test_load_plan_dep_and_reset()
    test_to_briefing_dep_runway()
    test_inputs_markup()
    test_checklist_mode_endpoint()
    test_checklist_phase_endpoint()
    test_phase_bar_hides_empty_phase_endpoint()
    test_monitor_cleared_outside_flight()
    test_aao_parse_missing_variable()
    test_aao_read_falls_back_to_core()
    test_aao_unreachable_not_doubled()
    test_aao_requests_only_used_vars()
    test_aao_parse_dict_format_and_values_unchanged()
    test_firewall_noop_off_windows()
    test_plan_extraction()
    test_old_mode_values_migrate()
    print("ok")
