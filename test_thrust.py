import thrust as T

DET = T.DETENTS


def raw_for(vals_in_l, vals_in_r, pos1=None, pos2=None):
    """{detent: {chiave: valore}} con le variabili guida date (le altre assenti)."""
    out = {}
    for i, d in enumerate(DET):
        out[d] = {"thr_in_l": vals_in_l[i], "thr_in_r": vals_in_r[i], "thr_pos_1": (pos1 or [None] * 6)[i], "thr_pos_2": (pos2 or [None] * 6)[i]}
    return out


UP = [-20, -5, 0, 25, 50, 100]  # crescente (REVERSE ... TOGA)
DOWN = [100, 50, 25, 0, -5, -20]


def test_guide_choice():
    r = raw_for(UP, UP)
    assert T.choose_guide("l", r) == "thr_in_l" and T.choose_guide("r", r) == "thr_in_r"
    # non monotona: ripiego sulla successiva
    r = raw_for([0, 1, 2, 2, 4, 5], UP, pos1=[1, 2, 3, 4, 5, 6])
    assert T.choose_guide("l", r) == "thr_pos_1" and T.choose_guide("r", r) == "thr_in_r"
    # tutte uguali, assenti, gap troppo piccolo (< 2% dello span), non monotone
    assert T.choose_guide("l", raw_for([1] * 6, UP)) is None
    assert T.choose_guide("l", raw_for([0, 100, 100.5, 101, 102, 1000], UP)) is None
    assert T.choose_guide("l", raw_for([0, 1, 2, 3, 4, None], UP)) is None
    assert T.choose_guide("l", raw_for([0, 10, 5, 20, 30, 40], UP)) is None
    assert T.choose_guide("l", raw_for(DOWN, UP)) == "thr_in_l"  # anche decrescente
    # le variabili non candidate (pos_3, rev) non sono mai guida
    r = raw_for([1] * 6, [1] * 6)
    for d, v in zip(DET, UP):
        r[d]["thr_pos_3"] = v
        r[d]["thr_rev_l"] = v
    assert T.choose_guide("l", r) is None


def test_build_cal():
    cal, err = T.build_cal(raw_for(UP, DOWN), 1000)
    assert err == "" and T.valid_cal(cal)
    assert cal["levers"]["l"]["var"] == "thr_in_l" and cal["levers"]["r"]["var"] == "thr_in_r"
    assert cal["levers"]["r"]["v"]["TOGA"] == -20.0 and cal["raw"]["IDLE"]["thr_in_l"] == 0
    cal, err = T.build_cal(raw_for(UP, [3] * 6), 1000)
    assert cal is None and "lever R" in err and "thr_in_r=3,3,3,3,3,3" in err


def test_valid_cal():
    cal, _ = T.build_cal(raw_for(UP, UP), 5)
    assert T.valid_cal(cal)
    for bad in (None, {}, [], {"at": 1}, {**cal, "at": "x"}, {**cal, "raw": 3}):
        assert not T.valid_cal(bad)
    import copy
    c = copy.deepcopy(cal)
    c["levers"]["l"]["var"] = "thr_pos_2"  # non candidata della leva L
    assert not T.valid_cal(c)
    c = copy.deepcopy(cal)
    c["levers"]["r"]["v"]["IDLE"] = 99  # non monotona
    assert not T.valid_cal(c)
    c = copy.deepcopy(cal)
    del c["levers"]["r"]["v"]["TOGA"]
    assert not T.valid_cal(c)
    c = copy.deepcopy(cal)
    del c["levers"]["l"]
    assert not T.valid_cal(c)


def test_decode_both_orientations():
    for vals in (UP, DOWN):
        v = dict(zip(DET, [float(x) for x in vals]))
        for i, x in enumerate(vals):
            assert T.decode(v, x) == i, (vals, x)
        assert T.decode(v, None) is None and T.decode(v, "x") is None
    v = dict(zip(DET, [float(x) for x in UP]))
    # bande: gap IDLE = min(5, 25) = 5 -> banda 2; CLIMB gap min(25, 25) = 25 -> banda 10
    assert T.decode(v, 1.9) == 2 and T.decode(v, 2.1) == -1 and T.decode(v, -1.9) == 2
    assert T.decode(v, 35) == 3 and T.decode(v, 36) == -1 and T.decode(v, 14) == -1
    # estremi: un solo gap (REVERSE -> 15: banda 6; TOGA -> 50: banda 20)
    assert T.decode(v, -26) == 0 and T.decode(v, -27) == -1
    assert T.decode(v, 120) == 5 and T.decode(v, 121) == -1 and T.decode(v, 1000) == -1 and T.decode(v, -1000) == -1


def test_annotate():
    cal, _ = T.build_cal(raw_for(UP, DOWN), 5)
    out = T.annotate({"thr_in_l": 25, "thr_in_r": 25}, cal)
    assert out == {"thr_det_l": 3, "thr_det_r": 2}
    out = T.annotate({"thr_in_l": 100, "thr_in_r": 100}, cal)  # R e' decrescente: 100 = REVERSE
    assert out == {"thr_det_l": 5, "thr_det_r": 0}
    assert T.annotate({"thr_in_l": 12, "thr_in_r": None}, cal) == {"thr_det_l": -1, "thr_det_r": None}
    assert T.annotate({"thr_in_l": 25}, None) == {"thr_det_l": None, "thr_det_r": None}
    assert T.annotate(None, cal) == {"thr_det_l": None, "thr_det_r": None}
    assert T.annotate({"thr_in_l": 0}, {"bad": 1}) == {"thr_det_l": None, "thr_det_r": None}


def test_steps_and_median():
    assert [s["detent"] for s in T.STEPS] == DET and len(T.STEPS) == 6
    assert T.STEPS[0]["text"] == "Move both thrust levers to FULL REVERSE, then press Next"
    assert T.STEPS[4]["name"] == "FLEX/MCT"
    assert T.median([3, 1, 2]) == 2 and T.median([1, 3]) == 2 and T.median([5]) == 5


if __name__ == "__main__":
    test_guide_choice()
    test_build_cal()
    test_valid_cal()
    test_decode_both_orientations()
    test_annotate()
    test_steps_and_median()
    print("ok")
