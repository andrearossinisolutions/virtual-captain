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
        assert it.std or it.lvar or it.kind == "detent" or it.key in ("zfw_calc", "fob_now"), it.key  # detent e zfw_calc: virtuali
        assert it.std_fallback is False or (it.std and it.lvar) or it.key == "gear"
        for v in it.expected.values():
            assert v
    assert "autobrake" not in keys and not any(i.uncertain for i in ITEMS if i.key == "squawk")
    for k in ("v1", "vr", "v2", "flex", "cstr1", "cstr2", "fd1", "fd2", "baro_std", "baro_hpa", "baro_inch"):
        assert k in keys, k
    assert BY["cstr1"].kind == "onoff" and BY["cstr1"].uncertain is False and BY["v1"].lvar == "N_MISC_PERF_TO_V1"
    for k in ("apu_master", "apu_bleed", "pack1", "pack2", "landing_l", "landing_r", "nose", "strobe", "beacon",
              "navlogo", "rwy_turnoff", "seatbelt", "flap", "spoilers", "gear", "engmode", "eng1", "eng2", "parking"):
        assert k in keys, k


def test_weight_items_and_zfw_calc():
    from monitor import weight_annotate
    assert BY["fuel_total"].lvar == "N_FUEL_TOTAL_QTY" and BY["cfg_lbs"].lvar == "B_CONFIG_WEIGHT_LBS"
    assert BY["total_weight"].std == "(A:TOTAL WEIGHT, Pounds)" and BY["fuel_weight"].std == "(A:FUEL TOTAL QUANTITY WEIGHT, Pounds)"
    import aao
    assert aao.MONITOR["total_weight"] == {"std": "(A:TOTAL WEIGHT, Pounds)", "lvar": None}  # std chiesta perche' senza L-var
    assert aao.MONITOR["fuel_total"] == {"std": None, "lvar": "(L:N_FUEL_TOTAL_QTY, Number)"} and aao.MONITOR["zfw_calc"] == {"std": None, "lvar": None}
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
    import aao
    assert aao.MONITOR["thr_det_l"] == {"std": None, "lvar": None}
    assert aao.AAO._parse({"getvars": []})[1]["thr_det_r"] is None
    assert BY["thr_pos_3"].lvar.endswith("POS_3") and BY["thr_pos_4"].lvar.endswith("POS_4")


if __name__ == "__main__":
    test_weight_items_and_zfw_calc()
    test_detent_labels()
    test_maps_per_kind()
    test_missing_is_nd()
    test_items_are_valid()
    test_build_groups()
    print("ok")
