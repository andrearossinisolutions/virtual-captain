import threading

import weather as W
from weather import Weather, inhg_to_hpa, qnh_from_json, qnh_from_raw


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
    test_invalid_icao_and_no_fetch()
    test_cache_ttl_and_retry_and_log()
    test_seed_precedence_and_stale()
    test_non_blocking()
    test_no_real_network()
    print("ok")
