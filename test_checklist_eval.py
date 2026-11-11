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
    assert evaluate({"key": "spoilers", "eq": "ARM"}, {"spoilers": "ARM"})["status"] == "na"  # uncertain
    assert evaluate({"key": "apu_bleed", "eq": "ON"}, {"apu_bleed": "ON"})["detail"] == "unverified"
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


def test_clean_label():
    from monitor import ITEMS, clean_label
    by = {i.key: i for i in ITEMS}
    assert clean_label(by["landing_l"], 2) == "ON" and clean_label(by["nose"], 2) == "T/O"
    assert clean_label(by["flap"], 4) == "FULL" and clean_label(by["landing_l"], None) is None
    assert clean_label(by["landing_l"], 7) is None and clean_label(by["landing_l"], 0.5) is None
    assert clean_label(by["apu_master"], "x") is None


if __name__ == "__main__":
    test_none_and_single()
    test_na_cases()
    test_eq_list()
    test_all_priority()
    test_clean_label()
    print("ok")
