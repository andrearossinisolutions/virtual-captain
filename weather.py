"""QNH reale dai METAR (aviationweather.gov), con cache e ripiego sul METAR del piano SimBrief.

Parsing puro (qnh_from_json, qnh_from_raw); la rete e' iniettata (fetch) e non blocca mai chi legge: get() risponde
subito con la cache e rinfresca in un thread daemon. Nessun dato personale nelle richieste.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

URL = "https://aviationweather.gov/api/data/metar?ids={icao}&format=json"
ICAO = re.compile(r"^[A-Z]{4}$")
_Q = re.compile(r"(?:^|\s)Q(\d{4})(?=\s|$)")
_A = re.compile(r"(?:^|\s)A(\d{4})(?=\s|$)")


def inhg_to_hpa(v) -> float | None:
    """29.92 oppure 2992 (inHg) -> hPa; non numerico -> None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x > 100:
        x /= 100
    return x * 33.8639 if x > 0 else None


def qnh_from_raw(raw) -> float | None:
    """QNH in hPa da un METAR grezzo: Qnnnn (hPa) oppure Annnn (inHg)."""
    s = str(raw or "")
    m = _Q.search(s)
    if m:
        return float(m.group(1))
    m = _A.search(s)
    return int(m.group(1)) * 33.8639 / 100 if m else None


def qnh_from_json(data) -> float | None:
    """QNH in hPa dal JSON di aviationweather (primo elemento): `altim` (hPa; se <50 e' inHg), ripiego rawOb."""
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    d = data[0]
    try:
        v = float(d.get("altim"))
        if v > 0:
            return v * 33.8639 if v < 50 else v
    except (TypeError, ValueError):
        pass
    return qnh_from_raw(d.get("rawOb"))


def online_fetch(icao: str) -> float | None:
    """Richiesta reale (timeout 5 s). Errori di rete/parsing: eccezione, la gestisce Weather."""
    from simbrief import _ssl_context
    req = urllib.request.Request(URL.format(icao=icao), headers={"User-Agent": "virtual-captain"})
    with urllib.request.urlopen(req, timeout=5, context=_ssl_context()) as r:
        body = r.read().decode("utf-8", "replace")
    return qnh_from_json(json.loads(body)) if body.strip() else None


class Weather:
    def __init__(self, fetch=None, ttl: float = 600, retry: float = 60, stale: float = 3600, now=time.time, log=print):
        self.fetch, self.ttl, self.retry, self.stale, self.now, self.log = fetch, ttl, retry, stale, now, log
        self._lock = threading.Lock()
        self._online: dict = {}  # icao -> (qnh, t)
        self._seed: dict = {}  # icao -> qnh
        self._next: dict = {}  # icao -> istante del prossimo tentativo
        self._busy: set = set()
        self._failing: set = set()  # episodi di errore gia' loggati

    def seed(self, icao, raw_metar) -> None:
        """Ripiego dal METAR del piano SimBrief (l'online ha precedenza se non scaduto)."""
        icao = str(icao or "").strip().upper()
        q = qnh_from_raw(raw_metar)
        if ICAO.match(icao) and q is not None:
            with self._lock:
                self._seed[icao] = q

    def _value(self, icao):
        o = self._online.get(icao)
        if o and self.now() - o[1] <= self.stale:
            return o[0]
        return self._seed.get(icao)

    def get(self, icao) -> float | None:
        """QNH in hPa dalla cache (o dal ripiego); non blocca mai. Rinfresca in background se serve."""
        icao = str(icao or "").strip().upper()
        if not ICAO.match(icao):
            return None
        with self._lock:
            val = self._value(icao)
            o = self._online.get(icao)
            fresh = o is not None and self.now() - o[1] < self.ttl
            start = (self.fetch is not None and not fresh and icao not in self._busy
                     and self.now() >= self._next.get(icao, 0))
            if start:
                self._busy.add(icao)
        if start:
            threading.Thread(target=self.refresh, args=(icao,), daemon=True).start()
        return val

    def refresh(self, icao) -> None:
        """Rinfresco sincrono: cattura le eccezioni, una sola riga di log per episodio di errore."""
        icao = str(icao or "").strip().upper()
        if self.fetch is None or not ICAO.match(icao):
            return
        with self._lock:
            self._busy.add(icao)
        try:
            q = self.fetch(icao)
            if q is None:
                raise ValueError("no QNH in response")
            with self._lock:
                self._online[icao] = (float(q), self.now())
                self._next.pop(icao, None)
                self._failing.discard(icao)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._next[icao] = self.now() + self.retry
                first = icao not in self._failing
                self._failing.add(icao)
            if first:
                try:
                    self.log(f"weather: QNH {icao} not available ({type(e).__name__})")
                except Exception:  # noqa: BLE001
                    pass
        finally:
            with self._lock:
                self._busy.discard(icao)
