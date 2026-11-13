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
        assert it.std or it.lvar, it.key
        assert it.std_fallback is False or (it.std and it.lvar) or it.key == "gear"
        for v in it.expected.values():
            assert v
    assert "autobrake" not in keys and not any(i.uncertain for i in ITEMS if i.key == "squawk")
    for k in ("v1", "vr", "v2", "flex", "cstr1", "cstr2", "fd1", "fd2", "baro_std", "baro_hpa"):
        assert k in keys, k
    assert BY["cstr1"].kind == "onoff" and BY["cstr1"].uncertain is False and BY["v1"].lvar == "N_MISC_PERF_TO_V1"
    for k in ("apu_master", "apu_bleed", "pack1", "pack2", "landing_l", "landing_r", "nose", "strobe", "beacon",
              "navlogo", "rwy_turnoff", "seatbelt", "flap", "spoilers", "gear", "engmode", "eng1", "eng2", "parking"):
        assert k in keys, k


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


if __name__ == "__main__":
    test_maps_per_kind()
    test_missing_is_nd()
    test_items_are_valid()
    test_build_groups()
    print("ok")
