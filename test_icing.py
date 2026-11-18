import icing
import monitor as mon
from icing import ai_rule, icing_assessment, moisture
from weather import metar_from_raw as M

LIML_BR = M("LIML 181550Z 20004KT 0800 BR BKN002 03/03 Q1022")


def test_moisture_rules():
    assert moisture(M("X 1Z 27012KT 9999 -RA 12/08 Q1013"), False) == ["RA"]
    for code in mon.AI_WX_ALWAYS:
        assert moisture({"wx": [code], "vis": "9999"}, False) == [code]
    assert moisture({"wx": ["BR"], "vis": "1600"}, False) == ["BR", "vis 1600 m"]  # BR conta solo con vis <= 1600
    assert moisture({"wx": ["BR"], "vis": "1601"}, False) == [] and moisture({"wx": ["BR"], "vis": "9999"}, True) == []
    assert moisture({"vis": "0800"}, False) == ["vis 800 m"]  # vis bassa anche senza codice
    for code in ("HZ", "FU", "DU", "SA", "IC"):
        assert moisture({"wx": [code], "vis": "9999"}, True) == []  # escluse
    assert moisture({"vis": "CAVOK"}, True) == [] and moisture({}, True) == [] and moisture(None, False) == []
    ck = lambda cover, typ=None: {"clouds": [{"cover": cover, "base_ft": 1000, "type": typ}], "vis": "9999"}
    assert moisture(ck("BKN"), True) == ["BKN"] and moisture(ck("OVC"), True) == ["OVC"] and moisture(ck("BKN"), False) == [] and moisture(ck("OVC"), False) == []
    assert moisture(ck("FEW"), True) == [] and moisture(ck("SCT"), True) == [] and moisture(ck("FEW", "CB"), False) == ["CB"] and moisture(ck("SCT", "TCU"), False) == ["TCU"]
    assert moisture(ck("VV"), False) == ["VV"]


def test_assessment_thresholds():
    wet = {"raw": "x", "wx": ["RA"], "vis": "9999"}
    assert icing_assessment(None, 5, True) == {"icing": None, "text": "no METAR, check manually"}
    assert icing_assessment({}, 5, False)["icing"] is None and icing_assessment({"icao": "LIML"}, 5, False)["icing"] is None  # mai "no icing" senza dati
    assert icing_assessment(wet, None, False)["icing"] is None
    assert icing_assessment(wet, 10, False) == {"icing": True, "text": "RA, T 10 \u00b0C: icing conditions, ON"}  # limite incluso
    assert icing_assessment(wet, 10.1, False) == {"icing": False, "text": "not required (OFF)"}
    assert icing_assessment(wet, 11, True)["icing"] is False
    assert icing_assessment({"raw": "x", "vis": "9999"}, 3, True) == {"icing": False, "text": "no visible moisture: not required"}
    r = icing_assessment(LIML_BR, 3, False, "LIML")
    assert r == {"icing": True, "text": "LIML: BR, vis 800 m, T 3 \u00b0C: icing conditions, ON"}
    assert icing_assessment({"raw": "x", "wx": ["BR"], "vis": "1601"}, 3, False)["icing"] is False
    assert icing_assessment({"raw": "x", "vis": "1600"}, 3, False)["icing"] is True and icing_assessment({"raw": "x", "vis": "1601"}, 3, False)["icing"] is False


def test_partial_metar_is_absent():
    for partial in ({"qnh": 1013.0}, {"temp": 3}, {"vis": "9999", "temp": 3}, {"icao": "LIML", "temp": 3}):  # senza raw: dato parziale
        assert icing_assessment(partial, 3, False, "LIML") == {"icing": None, "text": "no METAR, check manually"}
    assert icing_assessment({"raw": "x", "temp": 3}, 3, False)["icing"] is False  # con raw: dati sufficienti
    assert ai_rule("descent", {"sat": "-20", "tat": "5"}, {"dest": {"qnh": 1000.0}}) == {"on": False, "text": "no METAR, check manually"}
    assert ai_rule("ground", {"gnd": "1", "sat": "5"}, {"dep": {"qnh": 1000.0}}) == {"on": False, "text": "no METAR, check manually"}


def test_metar_temp_zero_not_replaced_by_sat():
    dep = {"raw": "x", "temp": 0, "wx": ["RA"], "icao": "LIML"}
    r = ai_rule("ground", {"gnd": "1", "sat": "20"}, {"dep": dep})  # temp 0 legittima: niente ripiego SAT (20 darebbe "not required")
    assert r["on"] is True and "T 0 " in r["text"]


def test_rule_ground():
    lab = {"gnd": "1", "sat": "8"}
    r = ai_rule("ground", lab, {"dep": {**LIML_BR, "icao": "LIML"}})
    assert r["on"] is True and r["text"].startswith("LIML: BR")
    r = ai_rule("ground", lab, {"dep": {"raw": "x", "wx": ["RA"], "vis": "9999", "icao": "LIML"}})  # niente temp nel METAR: ripiego SAT con gnd=1
    assert r["on"] is True and "T 8" in r["text"]
    assert ai_rule("ground", {"gnd": "1", "sat": "12"}, {"dep": {"raw": "x", "wx": ["RA"], "vis": "9999"}}) == {"on": False, "text": "not required (OFF)"}
    r = ai_rule("ground", {"gnd": "0", "sat": "8"}, {"dep": {"raw": "x", "wx": ["RA"], "vis": "9999"}})  # gnd=0: nessun ripiego
    assert r["on"] is False and "temperature" in r["text"]
    assert ai_rule("ground", {"gnd": "1", "sat": "0"}, {"dep": {"raw": "x", "wx": ["RA"]}})["on"] is False  # SAT = 0: non letta
    assert ai_rule("ground", lab, None) == {"on": False, "text": "no METAR, check manually"} and ai_rule("ground", lab, {"dep": {"icao": "LIML"}})["on"] is False
    assert ai_rule("ground", lab, {"dep": {"raw": "x", "temp": 3, "wx": ["RA"], "clouds": [{"cover": "OVC", "base_ft": 100, "type": None}]}})["text"].count("OVC") == 0  # a terra BKN/OVC non contano


def test_rule_takeoff_climb():
    assert ai_rule("takeoff", {"tat": "10"}, None) == {"on": False, "text": "TAT 10 \u00b0C: ON if visible moisture"}
    assert ai_rule("takeoff", {"tat": "10.5"}, None) == {"on": False, "text": "not required (OFF)"} and ai_rule("takeoff", {}, None)["text"].startswith("TAT 0")  # assente = 0
    assert ai_rule("climb", {"sat": "-41", "tat": "-30"}, None) == {"on": False, "text": "may be OFF (ON only in Cb / ICE DETECTED)"}
    assert ai_rule("climb", {"sat": "-40", "tat": "-30"}, None)["text"].startswith("TAT -30") and ai_rule("climb", {"sat": "5", "tat": "20"}, None)["text"] == "not required (OFF)"
    assert not any(ai_rule(r, {"sat": "-20", "tat": "-5"}, {"dep": LIML_BR, "dest": LIML_BR})["on"] for r in ("takeoff", "climb"))  # sempre promemoria


def test_rule_descent():
    wet = {"dest": {"icao": "LIPZ", "raw": "x", "wx": ["RA"], "vis": "9999"}}
    for wx in (None, {}, {"dest": {}}, wet):
        r = ai_rule("descent", {"sat": "-45", "tat": "-30"}, wx)  # SAT < -40: ON richiesto, indipendente dal METAR
        assert r["on"] is True and "below -40" in r["text"]
    assert ai_rule("descent", {"sat": "-40", "tat": "-30"}, None) == {"on": False, "text": "no METAR, check manually"}  # SAT = -40 non e' < -40
    assert ai_rule("descent", {"sat": "-20", "tat": "5"}, wet)["on"] is True and ai_rule("descent", {"sat": "-20", "tat": "10"}, wet)["on"] is True
    assert ai_rule("descent", {"sat": "-20", "tat": "10.5"}, wet) == {"on": False, "text": "not required (OFF)"}
    assert ai_rule("descent", {"sat": "-20", "tat": "5"}, {"dest": {"raw": "x", "vis": "9999"}}) == {"on": False, "text": "no visible moisture: not required"}
    bkn = {"dest": {"raw": "x", "vis": "9999", "clouds": [{"cover": "BKN", "base_ft": 3000, "type": None}]}}
    assert ai_rule("descent", {"sat": "-20", "tat": "5"}, bkn)["on"] is True  # in volo BKN conta
    assert ai_rule("descent", {}, wet)["on"] is True  # SAT/TAT assenti = 0: TAT 0 <= 10, nessun falso rosso da SAT
    try:
        ai_rule("x", {}, None); assert False
    except ValueError:
        pass


if __name__ == "__main__":
    test_moisture_rules()
    test_assessment_thresholds()
    test_partial_metar_is_absent()
    test_metar_temp_zero_not_replaced_by_sat()
    test_rule_ground()
    test_rule_takeoff_climb()
    test_rule_descent()
    print("ok")
