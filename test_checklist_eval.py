from checklist_eval import evaluate


def test_none_and_single():
    assert evaluate(None, {}) == {"status": "none", "kind": "none", "detail": ""}
    c = {"key": "apu_master", "eq": "ON"}
    assert evaluate(c, {"apu_master": "ON"})["status"] == "ok"
    r = evaluate(c, {"apu_master": "OFF"})
    assert r == {"status": "bad", "kind": "mismatch", "detail": "expected ON, actual OFF"}


def test_na_cases():
    c = {"key": "apu_master", "eq": "ON"}
    assert evaluate(c, {"apu_master": None})["status"] == "na"
    assert evaluate(c, {})["status"] == "na"
    assert evaluate(c, {"apu_master": "n/d (7)"})["status"] == "na"  # fuori mappa
    assert evaluate({"key": "spoilers", "eq": "ARM"}, {"spoilers": "ARM"})["status"] == "ok"  # verificato dal vivo 13/11
    assert evaluate({"key": "apu_bleed", "eq": "ON"}, {"apu_bleed": "ON"})["status"] == "ok"
    assert evaluate(c, {})["kind"] == "unreadable" and evaluate(c, {"apu_master": "ON"}, verified=False)["kind"] == "unverified"
    r = evaluate(c, {"apu_master": "ON"}, verified=False)
    assert r["status"] == "na" and r["detail"] == "unverified"
    assert evaluate(None, {}, verified=False)["status"] == "none"


def test_eq_list():
    c = {"key": "landing_l", "eq": ["OFF", "RETRACT"]}
    assert evaluate(c, {"landing_l": "RETRACT"})["status"] == "ok"
    assert evaluate(c, {"landing_l": "OFF"})["status"] == "ok"
    r = evaluate(c, {"landing_l": "ON"})
    assert r == {"status": "bad", "kind": "mismatch", "detail": "expected OFF / RETRACT, actual ON"}


def test_all_priority():
    c = {"all": [{"key": "landing_l", "eq": "ON"}, {"key": "landing_r", "eq": "ON"}]}
    assert evaluate(c, {"landing_l": "ON", "landing_r": "ON"}) == {"status": "ok", "kind": "ok", "detail": ""}
    r = evaluate(c, {"landing_l": "ON", "landing_r": "OFF"})
    assert r == {"status": "bad", "kind": "mismatch", "detail": "Landing R: expected ON, actual OFF"}
    r = evaluate(c, {"landing_l": "OFF", "landing_r": "OFF"})
    assert r["detail"] == "Landing L: expected ON, actual OFF; Landing R: expected ON, actual OFF"
    assert evaluate(c, {"landing_l": "ON", "landing_r": None})["status"] == "na"
    r = evaluate(c, {"landing_l": None, "landing_r": "OFF"})  # rosso su grigio: prevale il rosso, solo la sottovoce non conforme
    assert r == {"status": "bad", "kind": "mismatch", "detail": "Landing R: expected ON, actual OFF"}
    assert evaluate(c, {"landing_l": "ON", "landing_r": "ON"}, verified=False)["status"] == "na"


def test_lt_threshold():
    c = {"key": "vs_fpm", "lt": -100}  # soglia di Andrea: VS < -100 fpm
    assert evaluate(c, {"vs_fpm": "-700"})["status"] == "ok" and evaluate(c, {"vs_fpm": "-101"})["status"] == "ok"
    r = evaluate(c, {"vs_fpm": "-100"})  # stretto: -100 esatto e' rosso
    assert r == {"status": "bad", "kind": "mismatch", "detail": "expected < -100, actual -100"}
    assert evaluate(c, {"vs_fpm": "0"})["status"] == "bad"
    assert evaluate(c, {"vs_fpm": "300"})["status"] == "bad"
    assert evaluate(c, {"vs_fpm": None})["status"] == "na" and evaluate(c, {})["kind"] == "unreadable"
    assert evaluate(c, {"vs_fpm": "-700"}, verified=False)["status"] == "na"


def test_show_and_optional():
    s = {"show": ["wx_gain"]}
    assert evaluate(s, {"wx_gain": "0.5"}) == {"status": "none", "kind": "none", "detail": ""}
    assert evaluate(s, {}, verified=False)["status"] == "none"
    c = {"key": "flap", "eq": "FULL"}
    assert evaluate(c, {"flap": "FULL"}, optional=True)["status"] == "ok"
    r = evaluate(c, {"flap": "3"}, optional=True)
    assert r == {"status": "opt", "kind": "optional", "detail": "expected FULL, actual 3"}
    r = evaluate(c, {"flap": "UP"}, optional=True)
    assert r == {"status": "opt", "kind": "optional", "detail": "expected FULL, actual UP"}
    assert evaluate(c, {"flap": "UP"})["status"] == "bad"
    assert evaluate(c, {"flap": None}, optional=True)["status"] == "na"
    assert evaluate(c, {"flap": "UP"}, verified=False, optional=True)["status"] == "na"


def test_numeric():
    from monitor import ITEMS, clean_label, label, MAPS
    by = {i.key: i for i in ITEMS}
    assert MAPS["numeric"] == {}
    assert label(by["wx_gain"], 0.5) == "0.5" and label(by["wx_gain"], 12) == "12" and label(by["wx_gain"], None) == "n/d"
    assert clean_label(by["wx_gain"], 0.5) == "0.5" and clean_label(by["wx_mode"], None) is None
    assert label(by["wx_sys"], 2) == "SYS 2 (2)" and label(by["wx_sys"], 1) == "OFF (1)" and label(by["ir1"], 1) == "NAV (1)"
    assert clean_label(by["ir1"], 1) == "NAV" and clean_label(by["bat1"], 1) == "ON"


def test_clean_label():
    from monitor import ITEMS, clean_label
    by = {i.key: i for i in ITEMS}
    assert clean_label(by["landing_l"], 2) == "ON" and clean_label(by["nose"], 2) == "T/O"
    assert clean_label(by["flap"], 4) == "FULL" and clean_label(by["landing_l"], None) is None
    assert clean_label(by["landing_l"], 7) is None and clean_label(by["landing_l"], 0.5) is None
    assert clean_label(by["apu_master"], "x") is None


def test_expected_refs():
    c = {"key": "wx_gain", "eq_ref": "clr.t"}  # numeric senza mappa
    ev = lambda lab, exp, **kw: evaluate(c, {"wx_gain": lab}, expected=exp, **kw)
    assert ev("2000", {"clr.t": "2000"}) == {"status": "ok", "kind": "ok", "detail": ""}
    assert ev("2000", {"clr.t": "2100"}) == {"status": "bad", "kind": "mismatch", "detail": "expected 2100, actual 2000"}
    assert ev("2000", {"clr.t": "02000"})["status"] == "ok" and ev("0.5", {"clr.t": "0.50"})["status"] == "ok"  # normalizzazione numerica
    assert ev("2000", {"clr.t": " 2000 "})["status"] == "ok"
    for empty in ({}, {"clr.t": ""}, {"clr.t": "  "}, {"clr.t": None}):
        assert ev("2000", empty) == {"status": "none", "kind": "none", "detail": ""}  # atteso vuoto: solo NOW
    assert ev(None, {"clr.t": "2000"})["status"] == "na" and ev(None, {"clr.t": "2000"})["kind"] == "unreadable"
    assert ev("2000", {"clr.t": "2000"}, verified=False) == {"status": "na", "kind": "unverified", "detail": "unverified"}
    assert ev("2100", {"clr.t": "2000"}, optional=True)["status"] == "opt"
    # squawk: verificato dal vivo (13/11), esito normale
    sq = {"key": "squawk", "eq_ref": "clr.t"}
    assert evaluate(sq, {"squawk": "2000"}, expected={"clr.t": "2000"})["status"] == "ok"
    assert evaluate(sq, {"squawk": "7000"}, expected={"clr.t": "2000"})["status"] == "bad"
    assert evaluate(sq, {"squawk": None}, expected={"clr.t": "2000"})["kind"] == "unreadable"
    assert evaluate(sq, {"squawk": "2000"}, expected={})["status"] == "none"
    # solo-atteso: nessun esito, anche senza lettura
    assert evaluate({"show_ref": "clr.a"}, {}, expected={"clr.a": "FL100"}) == {"status": "none", "kind": "none", "detail": ""}
    assert evaluate({"show_ref": "clr.a"}, {}, verified=False)["status"] == "none"
    # eq e show esistenti invariati con expected passato
    assert evaluate({"key": "apu_master", "eq": "ON"}, {"apu_master": "OFF"}, expected={"clr.t": "1"})["status"] == "bad"


def test_flap_eq_ref():
    from checklist_eval import norm_label
    for t, w in (("1", "1"), ("CONF 1", "1"), ("flap 1", "1"), ("1+F", "1"), ("conf 2", "2"), ("3", "3"), ("FULL", "FULL"), ("4", "FULL"), ("full", "FULL"), ("0", "UP"), ("UP", "UP")):
        assert norm_label("flap", t) == w, t
    for t in ("", "5", "x", "CONF"):
        assert norm_label("flap", t) is None, t
    c = {"key": "flap", "eq_ref": "fld.flap"}
    ev = lambda lab, w: evaluate(c, {"flap": lab}, expected={"fld.flap": w})
    assert ev("1", "CONF 1")["status"] == "ok" and ev("1", "1+F")["status"] == "ok" and ev("FULL", "4")["status"] == "ok"
    r = ev("2", "1+F")
    assert r["status"] == "bad" and r["detail"] == "expected 1, actual 2"
    for w in ("", "  ", "9", "boh", None):
        assert ev("2", w)["status"] == "none"
    assert ev(None, "1")["kind"] == "unreadable"


def test_v_speeds_all_eq_ref():
    c = {"all": [{"key": k, "eq_ref": "fld." + k} for k in ("v1", "vr", "v2")]}
    exp = {"fld.v1": "140", "fld.vr": "145", "fld.v2": "150"}
    ev = lambda lab, e: evaluate(c, lab, expected=e)
    assert ev({"v1": "140", "vr": "145", "v2": "150"}, exp)["status"] == "ok"
    r = ev({"v1": "140", "vr": "0", "v2": "150"}, exp)
    assert r["status"] == "bad" and r["detail"] == "VR: expected 145, actual 0"
    assert ev({"v1": "140", "vr": None, "v2": "150"}, exp)["kind"] == "unreadable"
    assert ev({"v1": "0", "vr": "0", "v2": "0"}, {})["status"] == "none"  # briefing vuoto: solo NOW
    assert ev({"v1": "0", "vr": "0", "v2": "0"}, {"fld.v1": "", "fld.vr": " ", "fld.v2": None})["status"] == "none"
    assert ev({"v1": "140", "vr": "0", "v2": "0"}, {"fld.v1": "140"})["status"] == "ok"  # voce vuota saltata
    assert evaluate(c, {}, verified=False, expected=exp)["kind"] == "unverified"


def test_fcu_alt():
    from checklist_eval import altitude_ft
    for t, ft in (("5000", 5000), ("FL070", 7000), ("FL 70", 7000), ("fl70", 7000), ("7000 ft", 7000), ("070", 70), ("FL350", 35000)):
        assert altitude_ft(t) == ft, t
    assert altitude_ft("abc") is None and altitude_ft("") is None
    c = {"key": "fcu_alt", "eq_ref": "clr.a"}
    ev = lambda lab, want: evaluate(c, {"fcu_alt": lab}, expected={"clr.a": want})
    assert ev("7000", "FL070") == {"status": "ok", "kind": "ok", "detail": ""}
    assert ev("5000", "5000")["status"] == "ok" and ev("7000", "7000 ft")["status"] == "ok" and ev("7000", "FL 70")["status"] == "ok"
    assert ev("7000", "FL80") == {"status": "bad", "kind": "mismatch", "detail": "expected FL80, actual 7000"}
    assert ev("70", "FL070")["status"] == "bad"  # niente tolleranza, 70 ft != FL070
    assert ev("7000", "xyz")["status"] == "bad" and ev(None, "FL070")["kind"] == "unreadable"
    assert ev("7000", "")["status"] == "none"


def test_qnh():
    c = {"qnh": "wx.qnh_dep"}
    e = {"wx.qnh_dep": "1013 hPa"}

    def ev(lab, exp=e, ver=True):
        return evaluate(c, lab, verified=ver, expected=exp)
    base = {"baro_std": "0", "baro_hpa": "1013", "baro_inch": None}
    assert ev(base)["status"] == "ok"
    assert ev({**base, "baro_hpa": "1014"})["status"] == "ok" and ev({**base, "baro_hpa": "1012"})["status"] == "ok"
    r = ev({**base, "baro_hpa": "1015"})
    assert r["status"] == "bad" and r["detail"] == "expected 1013, actual 1015"
    r = ev({**base, "baro_std": "1"})
    assert r["status"] == "bad" and r["detail"] == "expected 1013, actual STD"
    assert ev(base, exp={})["status"] == "none"
    assert ev({"baro_std": "0", "baro_hpa": None, "baro_inch": "29.92"})["status"] == "ok"
    assert ev({"baro_std": "0", "baro_hpa": None, "baro_inch": None})["status"] == "na"
    assert ev({"baro_hpa": "1013"})["kind"] == "unreadable"
    assert ev(base, ver=False)["kind"] == "unverified"


def test_qnh_gated_by_transition_level():
    c = {"qnh": "wx.qnh_dest", "gate_tl": True}
    lab = {"baro_std": "0", "baro_hpa": "1019", "baro_inch": None}  # QNH sbagliato (atteso 1013)
    ev = lambda tl, alt: evaluate(c, lab, expected={"wx.qnh_dest": "1013 hPa (ATIS)", "fld.atis_tl": tl, "sys.alt": alt})
    r = ev("070", "8000")
    assert r["status"] == "wait" and r["kind"] == "wait" and r["detail"] == "TL 070"  # sopra il TL: non valutata
    assert ev("070", "7000")["status"] == "wait"  # alla quota del TL: ancora non sotto
    assert ev("070", "6900")["status"] == "bad"  # sotto il TL: controllo normale
    assert ev("070", "")["status"] == "wait" and ev("070", "abc")["status"] == "wait"  # quota non leggibile
    assert evaluate(c, {**lab, "baro_hpa": "1013"}, expected={"wx.qnh_dest": "1013 hPa", "fld.atis_tl": "070", "sys.alt": "5000"})["status"] == "ok"
    assert ev("", "8000")["status"] == "bad" and ev("", "")["status"] == "bad"  # TL assente: come oggi
    assert evaluate({"qnh": "wx.qnh_dest"}, lab, expected={"wx.qnh_dest": "1013 hPa", "fld.atis_tl": "070", "sys.alt": "8000"})["status"] == "bad"  # senza gate_tl: invariato


def test_qnh_source_in_detail():
    c = {"qnh": "wx.qnh_dep"}
    base = {"baro_std": "0", "baro_hpa": "1013", "baro_inch": None}
    ev = lambda lab, want: evaluate(c, lab, expected={"wx.qnh_dep": want})
    assert ev(base, "1013 hPa (ATIS)")["status"] == "ok" and ev(base, "1012 hPa (METAR)")["status"] == "ok"  # tolleranza 1 hPa invariata
    r = ev({**base, "baro_hpa": "1016"}, "1013 hPa (ATIS)")
    assert r["status"] == "bad" and r["detail"] == "expected 1013 (ATIS), actual 1016"
    assert ev({**base, "baro_std": "1"}, "1009 hPa (METAR)")["detail"] == "expected 1009 (METAR), actual STD"
    assert ev({**base, "baro_hpa": "1016"}, "1013 hPa")["detail"] == "expected 1013, actual 1016"  # senza fonte: come prima
    assert ev({"baro_std": "0", "baro_hpa": None, "baro_inch": "29.92"}, "1013 hPa (ATIS)")["status"] == "ok"  # QNH letto in inHg
    assert ev(base, "")["status"] == "none"


def test_detent():
    one, two = {"detent": "IDLE"}, {"detent": ["FLX", "TOGA"]}
    cal, none = {"cal.thrust": "1"}, {"cal.thrust": ""}
    both = lambda l, r: {"thr_det_l": l, "thr_det_r": r}
    assert evaluate(one, both("IDLE", "IDLE"), expected=none)["status"] == "none"  # non calibrato: solo NOW
    assert evaluate(one, both("IDLE", "IDLE"), expected=None)["status"] == "none"
    assert evaluate(one, both("IDLE", "IDLE"), expected=cal) == {"status": "ok", "kind": "ok", "detail": ""}
    r = evaluate(one, both("IDLE", "CLIMB"), expected=cal)
    assert r["status"] == "bad" and r["detail"] == "Thrust lever R detent: expected IDLE, actual CLIMB"
    assert evaluate(one, both("BETWEEN", "IDLE"), expected=cal)["status"] == "bad"
    assert evaluate(two, both("FLX", "TOGA"), expected=cal)["status"] == "ok"
    assert evaluate(two, both("FLX", "CLIMB"), expected=cal)["status"] == "bad"
    assert evaluate(one, both("IDLE", None), expected=cal)["kind"] == "unreadable"
    assert evaluate(one, {}, expected=cal)["status"] == "na"
    assert evaluate(one, both("IDLE", "IDLE"), verified=False, expected=cal)["kind"] == "unverified"
    assert evaluate(one, both("CLIMB", "CLIMB"), verified=False, expected=cal)["status"] == "na"


if __name__ == "__main__":
    test_qnh_gated_by_transition_level()
    test_qnh_source_in_detail()
    test_detent()
    test_expected_refs()
    test_fcu_alt()
    test_flap_eq_ref()
    test_v_speeds_all_eq_ref()
    test_none_and_single()
    test_na_cases()
    test_eq_list()
    test_all_priority()
    test_show_and_optional()
    test_numeric()
    test_clean_label()
    test_qnh()
    print("ok")

