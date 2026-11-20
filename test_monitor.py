import monitor
from monitor import ITEMS, MAPS, label, build

BY = {i.key: i for i in ITEMS}


def test_maps_per_kind():
    assert label(BY["apu_master"], 1) == "ON" and label(BY["apu_master"], 0) == "OFF"
    assert [label(BY["landing_l"], v) for v in (0, 1, 2)] == ["RETRACT (0)", "OFF (1)", "ON (2)"]
    assert [label(BY["nose"], v) for v in (0, 1, 2)] == ["OFF (0)", "TAXI (1)", "T/O (2)"]
    assert [label(BY["strobe"], v) for v in (0, 1, 2)] == ["OFF", "AUTO", "ON"]
    assert [label(BY["navlogo"], v) for v in (0, 1, 2)] == ["OFF", "NAV", "LOGO"]
    assert [label(BY["flap"], v) for v in range(5)] == ["UP (0)", "1 (1)", "2 (2)", "3 (3)", "FULL (4)"]
    assert [label(BY["spoilers"], v) for v in (0, 1, 2, 3)] == ["ARM (0)", "RETRACT (1)", "HALF (2)", "FULL (3)"]
    assert [label(BY["engmode"], v) for v in (0, 1, 2)] == ["CRANK (0)", "NORM (1)", "START (2)"]
    assert label(BY["gear"], 1.0) == "DOWN" and label(BY["gear"], 0.0) == "UP"
    assert label(BY["parking"], 1.0) == "ON"


def test_missing_is_nd():
    for bad in (None, float("nan"), float("inf"), "x"):
        assert label(BY["landing_l"], bad) == "n/d", bad
        assert label(BY["apu_master"], bad) == "n/d", bad
    assert label(BY["spoilers"], 0.37) == "n/d (0.37)" and label(BY["apu_master"], 7) == "n/d (7)"
    assert label(BY["seatbelt"], -1) == "n/d (-1)"
    g = dict(build({"spoilers": 0.37, "pack1": None}))
    sp = [r for r in g["Flight controls"] if r[0] == "Ground spoilers"][0]
    assert sp[1] == "n/d (0.37)" and sp[3] == "0.37"
    pk = [r for r in g["APU / Air"] if r[0] == "Pack 1"][0]
    assert pk[1] == "n/d" and pk[3] == ""
    assert label(BY["apu_master"], "1") == "ON"


def test_items_are_valid():
    keys = [i.key for i in ITEMS]
    assert len(keys) == len(set(keys))
    for it in ITEMS:
        assert it.group and it.name and it.kind in MAPS, it.key
        assert it.std or it.lvar or it.kind == "detent" or it.key in ("zfw_calc", "fob_now", "vs_fpm", "autobrake_sel", "gnd"), it.key  # detent e zfw_calc: virtuali
        assert it.std_fallback is False or (it.std and it.lvar) or it.key == "gear"
        for v in it.expected.values():
            assert v
    assert "autobrake" not in keys and not any(i.uncertain for i in ITEMS if i.key == "squawk")
    for k in ("v1", "vr", "v2", "flex", "cstr1", "cstr2", "fd1", "fd2", "baro_std", "baro_hpa", "baro_inch"):
        assert k in keys, k
    assert BY["cstr1"].kind == "onoff" and BY["cstr1"].uncertain is False and BY["v1"].lvar == "N_MISC_PERF_TO_V1"
    assert BY["vs_fpm"].group == monitor.FLIGHT_GROUP and not BY["vs_fpm"].lvar and monitor.vs_annotate({}, {"vs": -700}) == {"vs_fpm": -700.0} and monitor.vs_annotate({}, {}) == {"vs_fpm": None}
    A = monitor.autobrake_state
    assert A({"autobrake_lo": 0, "autobrake_med": 0, "autobrake_max": 0}) == {"autobrake_sel": 0} and A({"autobrake_lo": 1, "autobrake_med": 0, "autobrake_max": 0}) == {"autobrake_sel": 1}
    assert A({"autobrake_lo": None, "autobrake_med": None, "autobrake_max": 1}) == {"autobrake_sel": 3} and A({"autobrake_med": 1}) == {"autobrake_sel": 2}
    assert A({"autobrake_lo": 1, "autobrake_med": 1, "autobrake_max": 0}) == {"autobrake_sel": None} and A({"autobrake_max": 0}) == {"autobrake_sel": None} and A(None) == {"autobrake_sel": None}
    assert monitor.clean_label(BY["autobrake_sel"], 2) == "MED" and monitor.clean_label(BY["autobrake_sel"], None) is None and BY["autobrake_lo"].uncertain
    assert BY["ls1"].lvar == "I_FCU_EFIS1_LS" and BY["ls2"].lvar == "I_FCU_EFIS2_LS" and BY["ls1"].uncertain and BY["ls1"].kind == "onoff"  # nome ipotizzato
    for k in ("apu_master", "apu_bleed", "pack1", "pack2", "landing_l", "landing_r", "nose", "strobe", "beacon",
              "navlogo", "rwy_turnoff", "seatbelt", "flap", "spoilers", "gear", "engmode", "eng1", "eng2", "parking"):
        assert k in keys, k


def test_tat_gnd_and_ai_constants():
    assert BY["tat"].std == "(A:TOTAL AIR TEMPERATURE, Celsius)" and BY["tat"].lvar is None and BY["tat"].group == "Anti-ice" and BY["sat"].group == "Anti-ice"
    from simvars import MONITOR, default_monitor
    assert MONITOR["tat"] == BY["tat"].std and MONITOR["gnd"] is None and default_monitor()["tat"] == 0.0 and default_monitor()["gnd"] is None  # tat assente = 0, gnd virtuale
    g = monitor.gnd_annotate
    assert g({}, {"on_ground": 1}) == {"gnd": 1} and g({}, {"on_ground": 0.0}) == {"gnd": 0} and g({}, {}) == {"gnd": None} and g(None, None) == {"gnd": None}
    assert (monitor.AI_ICING_TEMP_MAX, monitor.AI_VIS_MAX_M, monitor.AI_CRZ_SAT_MIN, monitor.AI_DESC_SAT_MAX) == (10, 1600, -40, -40)
    assert set(monitor.AI_WX_ALWAYS) >= {"RA", "SN", "FG", "TS"} and "BR" in monitor.AI_WX_VIS and "BR" not in monitor.AI_WX_ALWAYS


def test_weight_items_and_zfw_calc():
    from monitor import weight_annotate
    assert BY["fuel_total"].lvar == "N_FUEL_TOTAL_QTY" and BY["cfg_lbs"].lvar == "B_CONFIG_WEIGHT_LBS"
    assert BY["total_weight"].std == "(A:TOTAL WEIGHT, Pounds)" and BY["fuel_weight"].std == "(A:FUEL TOTAL QUANTITY WEIGHT, Pounds)"
    import simvars
    assert simvars.MONITOR["total_weight"] == "(A:TOTAL WEIGHT, Pounds)"  # std chiesta perche' senza L-var
    assert simvars.MONITOR["fuel_total"] == "(L:N_FUEL_TOTAL_QTY, Number)" and simvars.MONITOR["zfw_calc"] is None
    N = {"fob_now": None}
    assert weight_annotate({"total_weight": 150000, "fuel_weight": 30000}) == {**N, "zfw_calc": 120000}  # lbs (default)
    assert weight_annotate({"total_weight": 150000, "fuel_weight": 30000, "cfg_lbs": 1}) == {**N, "zfw_calc": 120000}
    assert weight_annotate({"total_weight": 150000, "fuel_weight": 30000, "cfg_lbs": 0}) == {**N, "zfw_calc": 54431}  # kg
    assert weight_annotate({"total_weight": 150000}) == {**N, "zfw_calc": None} and weight_annotate(None) == {**N, "zfw_calc": None}
    # dati reali del Fenix: N_FUEL_TOTAL_QTY in migliaia dell'unita' mostrata
    assert weight_annotate({"fuel_total": 1.17})["fob_now"] == 1170 and weight_annotate({"fuel_total": 6.6})["fob_now"] == 6600
    assert weight_annotate({"fuel_total": None})["fob_now"] is None


def test_build_groups():
    g = build({"apu_master": 1, "landing_l": 0})
    names = dict(g)
    assert "Lights" in names and "APU / Air" in names
    apu = names["APU / Air"][0]
    assert apu[0] == "APU master" and apu[1] == "ON" and apu[3] == "1" and "Prep" in apu[2]
    ll = [r for r in names["Lights"] if r[0] == "Landing L"][0]
    assert ll[1] == "RETRACT (0)" and ll[3] == "0"
    pk = [r for r in names["APU / Air"] if r[0] == "Pack 1"][0]
    assert pk[1] == "n/d" and pk[3] == ""


def test_detent_labels():
    d = BY["thr_det_l"]
    assert d.kind == "detent" and d.std is None and d.lvar is None and BY["thr_det_r"].group == "Thrust"
    names = ["REVERSE", "REVERSE IDLE", "IDLE", "CLIMB", "FLX", "TOGA"]
    assert [label(d, v) for v in range(6)] == names
    assert [monitor.clean_label(d, v) for v in range(6)] == names
    assert label(d, -1) == "BETWEEN" and monitor.clean_label(d, -1) == "BETWEEN"
    assert label(d, None) == "n/d" and monitor.clean_label(d, None) is None and label(d, 9) == "n/d (9)"
    assert monitor.lvar_request(d) is None and monitor.std_request(d) is None
    import simvars
    assert simvars.MONITOR["thr_det_l"] is None and simvars.default_monitor()["thr_det_r"] is None
    assert BY["thr_in_l"].lvar == "A_FC_THROTTLE_LEFT_INPUT" and BY["thr_gate_r"].lvar == "A_FC_THROTTLE_RIGHT_GATE"


def test_flight_items_std_only():
    keys = ["lat", "lon", "gs", "hdg_mag", "trk_mag", "pitch", "bank"]
    for k in keys:
        assert BY[k].group in ("Flight data",) and BY[k].kind == "numeric" and BY[k].std and BY[k].lvar is None
    from simvars import MONITOR, default_monitor
    assert all(MONITOR[k] and MONITOR[k].startswith("(A:") for k in keys)
    assert all(default_monitor()[k] == 0.0 for k in keys)  # assente = 0


def test_flight_panel_values_and_dashes():
    raw = {"lat": 45.6301, "lon": -8.7202, "gs": 150.4, "hdg_mag": 168.4, "trk_mag": 170.2, "pitch": -5.04, "bank": 3.26,
           "total_weight": 110231, "fuel_weight": 22046, "cfg_lbs": 0, "fob_now": 10000}
    core = {"alt": 5000, "ias": 250, "vs": 1500, "agl": 4800, "on_ground": 0, "gear": 0, "landing": 0, "seatbelt": 1}
    d = dict(monitor.flight_panel(raw, core))
    assert d["Position"] == "45.6301\u00b0 N  8.7202\u00b0 W" and d["Ground speed"] == "150 kt"
    assert d["Heading (mag)"] == "168\u00b0" and d["Track (mag)"] == "170\u00b0"
    assert d["Pitch"] == "+5.0\u00b0" and d["Bank"] == "-3.3\u00b0"  # segni invertiti
    assert "Alt 5000 ft" in d["Core"] and "VS +1500 fpm" in d["Core"] and "Ground AIR" in d["Core"] and "Gear UP" in d["Core"]
    assert [r[0] for r in monitor.flight_panel(raw, core)] == ["Position", "Ground speed", "Heading (mag)", "Track (mag)", "Pitch", "Bank", "Core"]
    assert dict(monitor.flight_panel({**raw, "hdg_mag": float("nan")}, core))["Heading (mag)"] == "\u2014"
    assert dict(monitor.flight_panel({k: v for k, v in raw.items() if k not in ("cfg_lbs", "fob_now")}, core))["Position"].startswith("45.6301")
    assert dict(monitor.flight_panel({**raw, "gs": 1.5}, core))["Track (mag)"] == "\u2014"  # sotto 2 kt
    bad = dict(monitor.flight_panel({"lat": None, "gs": float("nan"), "pitch": "x"}, {"alt": None, "vs": "a"}))
    assert all(bad[k] == "\u2014" for k in ("Ground speed", "Pitch", "Bank", "Track (mag)"))
    assert "0" not in bad["Position"] and " 0" not in bad["Core"].replace("Core", "")
    assert monitor.flight_pills({}, {}) == {"gs": "\u2014", "alt": "\u2014", "vs": "\u2014"}
    assert list(monitor.flight_pills({}, {})) == ["gs", "alt", "vs"] and [k for k, _, _ in monitor.PILLS] == monitor.ACTIVE_PILLS
    assert monitor.active_pill_defs() == [["gs", "GS"], ["alt", "ALT"], ["vs", "VS"]] and monitor.active_pill_defs(["vs", "gs"]) == [["gs", "GS"], ["vs", "VS"]]
    assert monitor.flight_pills({"gs": 120}, {"alt": 3000, "vs": -500}) == {"gs": "120 kt", "alt": "3000 ft", "vs": "-500 fpm"}


def test_no_key_in_both_cards():
    raw = {it.key: 1.0 for it in ITEMS}
    cockpit = {r[0] for _, rows in build(raw) for r in rows}
    flight_names = {BY[k].name for k in ("lat", "lon", "gs", "hdg_mag", "trk_mag", "pitch", "bank")}
    assert not (cockpit & flight_names) and "Flight data" not in [g for g, _ in build(raw)]
    assert {"Total weight (MSFS, lbs)", "FOB (Fenix, display unit)"} <= cockpit
    labels = " ".join(r[0] for r in monitor.flight_panel(raw, {}))
    assert "weight" not in labels.lower() and "FOB" not in labels
    from simvars import MONITOR
    assert all(MONITOR.get(k) for k in ("lat", "lon", "gs", "hdg_mag", "trk_mag", "pitch", "bank"))  # restano nella richiesta SimConnect


def test_read_all_missing_values_are_zero():
    import simvars
    from simconnect_backend import SimConnectBackend
    be = SimConnectBackend(dll=object(), clock=lambda: 0.0, use_thread=False)
    be._connected, be._core_ts = True, 0.0
    be._core = {"alt": 5000.0}
    core, m = be.read_all(0.1)
    assert core["alt"] == 5000.0 and core["vs"] == 0.0 and m["gs"] == 0.0 and m["bank"] == 0.0 and m["thr_det_l"] is None
    assert set(core) == set(simvars.VARS)


def test_gsx_states():
    assert [label(BY[k], 6) for k in ("gsx_refuel", "gsx_cater", "gsx_board")] == ["COMPLETED (6)"] * 3
    assert label(BY["gsx_board"], 5) == "IN PROGRESS (5)" and label(BY["gsx_board"], 7) == "n/d (7)" and label(BY["gsx_board"], 0) == "n/d (0)" and label(BY["gsx_board"], None) == "n/d"
    assert BY["gsx_refuel"].lvar == "FSDT_GSX_REFUELING_STATE" and BY["gsx_cater"].lvar == "FSDT_GSX_CATERING_STATE" and BY["gsx_board"].lvar == "FSDT_GSX_BOARDING_STATE"
    assert set(monitor.GSX_ACTIVE) <= set(monitor.GSX_STATES.values())
    assert monitor.gsx_live_text("gsx_board", 1) == "boarding 1 available" and monitor.gsx_live_text("gsx_board", None) == "boarding n/d"
    assert monitor.gsx_live_text("gsx_cater", 0) == "catering 0 n/d" and monitor.gsx_live_text("gsx_refuel", 5.0) == "refueling 5 in progress"
    diag = [i for i in monitor.ITEMS if i.group == "GSX" and i.key.startswith("gsx_d_")]
    assert len(diag) == 13 and all(i.kind == "numeric" and i.lvar.startswith("FSDT_GSX_") for i in diag)
    assert {i.lvar for i in diag} >= {"FSDT_GSX_COUATL_STARTED", "FSDT_GSX_HEARTBEAT", "FSDT_GSX_STATE", "FSDT_GSX_PUSHBACK_STATUS"}
    assert monitor.clean_label(BY["gsx_cater"], 6) == "COMPLETED" and monitor.clean_label(BY["gsx_cater"], 99) is None

if __name__ == "__main__":
    test_flight_items_std_only()
    test_flight_panel_values_and_dashes()
    test_read_all_missing_values_are_zero()
    test_no_key_in_both_cards()
    test_weight_items_and_zfw_calc()
    test_detent_labels()
    test_maps_per_kind()
    test_missing_is_nd()
    test_items_are_valid()
    test_build_groups()
    test_gsx_states()
    test_tat_gnd_and_ai_constants()
    print("ok")
