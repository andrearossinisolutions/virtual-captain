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
    assert (b.ta, b.thr_red_agl, b.accel_agl) == (5000, 1500, 1500)




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


if __name__ == "__main__":
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
