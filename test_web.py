import json
import pathlib
import tempfile
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
                     "aao_url": "http://127.0.0.1:43380/webapi"}
        assert State(cfg).fields["ta"] == "" and State(cfg).fields["accel_agl"] == "1000"
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


if __name__ == "__main__":
    test_api_flow()
    test_bad_values_use_defaults()
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
