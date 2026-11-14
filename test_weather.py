import threading

import weather as W
from weather import Weather, format_metar, inhg_to_hpa, metar_from_json, metar_from_raw, qnh_from_json, qnh_from_raw


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_parsing():
    assert qnh_from_json([{"altim": 1013, "rawOb": "X Q1000"}]) == 1013
    assert abs(qnh_from_json([{"altim": 29.92}]) - 1013.2) < 0.1
    assert qnh_from_json([{"rawOb": "LIRF 121250Z 18005KT Q1009 NOSIG"}]) == 1009
    assert qnh_from_json([]) is None and qnh_from_json(None) is None and qnh_from_json([{"altim": "x"}]) is None
    assert qnh_from_raw("KJFK 12Z A2992 RMK") == 2992 * 33.8639 / 100 and qnh_from_raw("LIRF 12Z") is None
    assert abs(inhg_to_hpa(29.92) - 1013.2) < 0.1 and abs(inhg_to_hpa("2992") - 1013.2) < 0.1 and inhg_to_hpa("x") is None


RAW = "EDDF 141050Z 27012G25KT 9999 FEW040 12/08 Q1013 NOSIG"
JSON = [{"icaoId": "EDDF", "altim": 1013, "wdir": 270, "wspd": 12, "wgust": 25, "visib": "6+", "temp": 12, "dewp": 8, "rawOb": RAW}]


def test_metar_parsing():
    m = metar_from_raw(RAW)
    assert (m["wdir"], m["wspd"], m["wgust"], m["vis"], m["temp"], m["dewp"], m["qnh"], m["raw"]) == (270, 12, 25, "9999", 12, 8, 1013, RAW)
    m = metar_from_raw("LIRF 141050Z VRB05KT CAVOK M02/M05 Q1009")
    assert (m["wdir"], m["wspd"], m["vis"], m["temp"], m["dewp"]) == ("VRB", 5, "CAVOK", -2, -5) and "wgust" not in m
    m = metar_from_raw("LIRF 141050Z 00000KT 4000 18/17 Q1009")
    assert (m["wdir"], m["wspd"], m["vis"]) == (0, 0, "4000")
    assert metar_from_raw("") == {} and metar_from_raw(None) == {} and set(metar_from_raw("garbage")) == {"raw"}
    assert "vis" not in metar_from_raw("LIRF 141050Z 27012KT Q1013")  # mancanti: solo quello che c'e'
    assert metar_from_raw("KJFK 12Z 18010KT 10SM A2992")["wspd"] == 10
    j = metar_from_json(JSON)
    assert (j["wdir"], j["wspd"], j["wgust"], j["vis"], j["temp"], j["qnh"]) == (270, 12, 25, "9999", 12, 1013)  # visibilita' dal grezzo (metri)
    j = metar_from_json([{"altim": 1000, "wdir": "VRB", "wspd": 3, "visib": 10, "temp": -1.4, "dewp": -3}])
    assert (j["wdir"], j["vis"], j["temp"], j["dewp"], j["qnh"]) == ("VRB", "10 SM", -1, -3, 1000)  # senza grezzo: miglia
    assert metar_from_json([]) == {} and metar_from_json(None) == {} and metar_from_json([{}]) == {}


def test_format_metar():
    f = format_metar("EDDF", metar_from_raw(RAW))
    assert f == {"icao": "EDDF", "line": "Wind 270/12G25 \u00b7 QNH 1013 \u00b7 Vis 9999 \u00b7 Temp 12/08", "raw": RAW}
    assert format_metar("LIRF", metar_from_raw("LIRF VRB05KT CAVOK M02/M05 Q1009"))["line"] == "Wind VRB/05 \u00b7 QNH 1009 \u00b7 Vis CAVOK \u00b7 Temp -02/-05"
    assert format_metar("LIRF", {"qnh": 1013.0}) == {"icao": "LIRF", "line": "QNH 1013", "raw": ""}
    assert format_metar("LIRF", {}) is None and format_metar("LIRF", None) is None
    assert format_metar("LIRF", {"raw": "A\x00B\n" + "x" * 400})["raw"].startswith("AB") and len(format_metar("LIRF", {"raw": "x" * 400})["raw"]) == 300


def test_get_metar_cache_and_fallback():
    clk = Clock()
    w = Weather(fetch=None, now=clk, stale=3600, log=lambda s: None)
    w.seed("EDDF", "EDDF 27005KT 5000 10/05 Q1000")
    assert w.get_metar("eddf")["wspd"] == 5 and w.get("EDDF") == 1000  # risposta = ripiego del piano
    w.fetch = lambda i: metar_from_json(JSON)
    w.refresh("EDDF")
    m = w.get_metar("EDDF")
    assert m["wspd"] == 12 and m["vis"] == "9999" and w.get("EDDF") == 1013  # online batte il piano
    clk.t += 4000
    w.fetch = None
    assert w.get_metar("EDDF")["wspd"] == 5  # online scaduto: ripiego
    assert Weather().get_metar("LIRF") == {} and Weather().get_metar("x") == {}
    w2 = Weather(fetch=lambda i: 1013.0, log=lambda s: None)  # fetch semplice (solo float): resta valido
    w2.refresh("LIRF")
    assert w2.get_metar("LIRF") == {"qnh": 1013.0} and w2.get("LIRF") == 1013


def test_invalid_icao_and_no_fetch():
    w = Weather(fetch=None)
    assert w.get("lirf1") is None and w.get("") is None and w.get(None) is None and w.get("LIRF") is None
    w.seed("LIRF", "LIRF Q1015")
    assert w.get("lirf") == 1015


def test_cache_ttl_and_retry_and_log():
    clk, calls, logs = Clock(), [], []
    state = {"fail": False}

    def fetch(icao):
        calls.append(icao)
        if state["fail"]:
            raise OSError("down")
        return 1013.0
    w = Weather(fetch=fetch, ttl=600, retry=60, stale=3600, now=clk, log=logs.append)
    assert w.get("LIRF") is None  # non bloccante: prima risposta dalla cache vuota
    w.refresh("LIRF")
    n = len(calls)
    assert w.get("LIRF") == 1013 and len(calls) == n  # fresco: niente rete
    clk.t += 601
    state["fail"] = True
    w.refresh("LIRF")
    w.refresh("LIRF")
    assert w.get("LIRF") == 1013  # errore: resta il valore vecchio
    assert len(logs) == 1  # una sola riga per episodio
    state["fail"] = False
    w.refresh("LIRF")
    state["fail"] = True
    clk.t += 601
    w.refresh("LIRF")
    assert len(logs) == 2  # nuovo episodio


def test_seed_precedence_and_stale():
    clk = Clock()
    w = Weather(fetch=lambda i: 1020.0, now=clk, stale=3600, log=lambda s: None)
    w.seed("LIRF", "Q1000")
    assert w.get("LIRF") == 1000  # parte il thread; la risposta e' il ripiego
    w.refresh("LIRF")
    assert w.get("LIRF") == 1020  # online batte seed
    clk.t += 4000
    w.fetch = None
    assert w.get("LIRF") == 1000  # online scaduto: ripiego
    w.seed("LIRF", "garbage")
    assert w.get("LIRF") == 1000


def test_non_blocking():
    gate, done = threading.Event(), threading.Event()

    def fetch(icao):
        gate.wait(5)
        return 1011.0
    w = Weather(fetch=fetch, log=lambda s: None)
    assert w.get("LIRF") is None  # ritorna subito, il fetch e' in attesa
    gate.set()
    for _ in range(200):
        if w.get("LIRF") == 1011:
            done.set()
            break
        threading.Event().wait(0.01)
    assert done.is_set()


def test_no_real_network():
    assert Weather().fetch is None and W.URL.startswith("https://aviationweather.gov/")


if __name__ == "__main__":
    test_parsing()
    test_metar_parsing()
    test_format_metar()
    test_get_metar_cache_and_fallback()
    test_invalid_icao_and_no_fetch()
    test_cache_ttl_and_retry_and_log()
    test_seed_precedence_and_stale()
    test_non_blocking()
    test_no_real_network()
    print("ok")
