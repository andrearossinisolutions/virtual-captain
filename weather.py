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
_WIND = re.compile(r"(?:^|\s)(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT(?=\s|$)")
_VIS = re.compile(r"(?:^|\s)(CAVOK|\d{4})(?=\s|$)")
_TEMP = re.compile(r"(?:^|\s)(M?\d{2})/(M?\d{2})(?=\s|$)")
_VIS_SM = re.compile(r"(?:^|\s)[PM]?(\d+ \d/\d|\d+/\d+|\d+)SM(?=\s|$)")  # visibilita' US in miglia: "10SM", "1/2SM", "1 1/2SM", "P6SM"
_VIS_SM_TXT = re.compile(r"^[PM]?\s*(\d+ \d/\d|\d+/\d+|\d+(?:\.\d+)?)\+?\s*SM$")  # testo di vis: "10 SM", "6+ SM", "1/2 SM"
M_PER_SM = 1609.34
_WX_TOKEN = re.compile(r"^(?:[-+])?(?:MI|PR|BC|DR|BL|SH|TS|FZ)*(?:DZ|RA|SN|SG|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS|IC)+$")
_WX_CODE = re.compile(r"DZ|RA|SN|SG|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS|IC|TS")
_CLOUD = re.compile(r"^(FEW|SCT|BKN|OVC)(\d{3}|///)(CB|TCU|///)?$")
_VV = re.compile(r"^VV(\d{3}|///)$")


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


def _int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _t(s: str) -> int:
    return -int(s[1:]) if s.startswith("M") else int(s)


def vis_m(vis) -> float | None:
    """Visibilita' in metri da un testo di `vis`: CAVOK = 10000, "9999"/"0800" metri, "n SM" (anche "1/2 SM", "1 1/2 SM", "6+ SM") x 1609; altro -> None."""
    s = str(vis or "").strip().upper()
    if s == "CAVOK":
        return 10000.0
    if re.fullmatch(r"\d{4}", s):
        return float(int(s))
    m = _VIS_SM_TXT.match(s)
    if not m:
        return None
    t, total = m.group(1), 0.0
    try:
        for part in t.split():
            if "/" in part:
                a, b = part.split("/")
                total += int(a) / int(b)
            else:
                total += float(part)
    except (ValueError, ZeroDivisionError):
        return None
    return total * M_PER_SM


_END = ("RMK", "TEMPO", "BECMG", "NOSIG", "PROB30", "PROB40")


def _body(s: str) -> list:
    """Gruppi del corpo del METAR: dopo il gruppo orario (ddhhmmZ, se c'e') e fino a RMK/TEMPO/BECMG/NOSIG/PROBnn (esclusi)."""
    toks = s.split()
    start = next((i + 1 for i, t in enumerate(toks) if re.fullmatch(r"\d{6}Z", t)), 0)
    out = []
    for t in toks[start:]:
        if t in _END:
            break
        out.append(t)
    return out


def wx_from_raw(s: str) -> list:
    """Fenomeni significativi del METAR (codici senza intensita' ne' descrittori: TSRA -> TS, RA; -SHRA -> RA), nell'ordine, senza doppioni.
    Solo il corpo (fermo a RMK/TEMPO/BECMG/NOSIG, dopo il gruppo orario); le "vicinanze" (VC...) non contano."""
    out: list = []
    for t in _body(s):
        if t.startswith("VC") or not _WX_TOKEN.match(t):
            continue
        for c in _WX_CODE.findall(t.lstrip("+-")):
            if c not in out:
                out.append(c)
    return out


def clouds_from_raw(s: str) -> list:
    """Nuvole del METAR: [{"cover": FEW|SCT|BKN|OVC|VV, "base_ft": int|None, "type": CB|TCU|None}, ...] (stesso limite di wx_from_raw)."""
    out: list = []
    for t in _body(s):
        m = _CLOUD.match(t)
        if m:
            base = None if m.group(2) == "///" else int(m.group(2)) * 100
            out.append({"cover": m.group(1), "base_ft": base, "type": m.group(3) if m.group(3) in ("CB", "TCU") else None})
            continue
        m = _VV.match(t)
        if m:
            out.append({"cover": "VV", "base_ft": None if m.group(1) == "///" else int(m.group(1)) * 100, "type": None})
    return out


def metar_from_raw(raw) -> dict:
    """Dati dal METAR grezzo (parsing puro). Chiavi: qnh (hPa), wdir (int|"VRB"), wspd, wgust (kt), vis (testo: metri o CAVOK),
    temp, dewp (gradi), wx (lista di codici fenomeno), clouds (lista di {cover, base_ft, type}), raw. Solo quelle trovate (dizionario vuoto se il testo e' vuoto)."""
    s = " ".join(str(raw or "").split())
    out: dict = {}
    q = qnh_from_raw(s)
    if q is not None:
        out["qnh"] = q
    m = _WIND.search(s)
    if m:
        out["wdir"] = "VRB" if m.group(1) == "VRB" else int(m.group(1))
        out["wspd"] = int(m.group(2))
        if m.group(3):
            out["wgust"] = int(m.group(3))
    body = " ".join(_body(s))
    m = _VIS.search(body)
    if m:
        out["vis"] = m.group(1)
    else:
        m = _VIS_SM.search(body)
        if m:
            out["vis"] = f"{m.group(1)} SM"
    m = _TEMP.search(s)
    if m:
        out["temp"], out["dewp"] = _t(m.group(1)), _t(m.group(2))
    wx, cl = wx_from_raw(s), clouds_from_raw(s)
    if wx:
        out["wx"] = wx
    if cl:
        out["clouds"] = cl
    if s:
        out["raw"] = s
    return out


def metar_from_json(data) -> dict:
    """Dati dal JSON di aviationweather (primo elemento). Come metar_from_raw; la visibilita' del JSON e' in miglia (statute):
    si usa solo se il METAR grezzo non la da' (poi "<n> SM")."""
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return {}
    d = data[0]
    out = metar_from_raw(d.get("rawOb"))
    q = qnh_from_json(data)
    if q is not None:
        out["qnh"] = q
    wd = d.get("wdir")
    if wd is not None and "wdir" not in out:
        if isinstance(wd, str) and wd.strip().upper() == "VRB":
            out["wdir"] = "VRB"
        elif _int(wd) is not None:
            out["wdir"] = _int(wd)
    for k, j in (("wspd", "wspd"), ("wgust", "wgust"), ("temp", "temp"), ("dewp", "dewp")):
        v = _int(d.get(j))
        if v is not None and k not in out:
            out[k] = v
    vis = d.get("visib")
    if vis is not None and "vis" not in out and str(vis).strip():
        out["vis"] = f"{str(vis).strip()} SM"
    return out


def _tt(v: int) -> str:
    return f"-{abs(v):02d}" if v < 0 else f"{v:02d}"


def format_metar(icao: str, m: dict | None) -> dict | None:
    """Testo gia' pronto per la pagina: {"icao", "line": "Wind 270/12G25 · QNH 1013 · Vis 9999 · Temp 12/08", "raw"} (solo i dati presenti)
    oppure None se non c'e' nessun dato. Il grezzo e' ripulito (spazi, caratteri di controllo, max 300)."""
    if not m:
        return None
    p = []
    if m.get("wdir") is not None and m.get("wspd") is not None:
        d = m["wdir"] if m["wdir"] == "VRB" else f"{int(m['wdir']):03d}"
        p.append(f"Wind {d}/{int(m['wspd']):02d}" + (f"G{int(m['wgust']):02d}" if m.get("wgust") else ""))
    if m.get("qnh") is not None:
        p.append(f"QNH {m['qnh']:.0f}")
    if m.get("vis"):
        p.append(f"Vis {m['vis']}")
    if m.get("temp") is not None and m.get("dewp") is not None:
        p.append(f"Temp {_tt(int(m['temp']))}/{_tt(int(m['dewp']))}")
    raw = "".join(c for c in str(m.get("raw") or "") if c.isprintable())[:300].strip()
    if not p and not raw:
        return None
    return {"icao": icao, "line": " \u00b7 ".join(p), "raw": raw}


def online_fetch(icao: str) -> dict | None:
    """Richiesta reale (timeout 5 s). Errori di rete/parsing: eccezione, la gestisce Weather."""
    from simbrief import _ssl_context
    req = urllib.request.Request(URL.format(icao=icao), headers={"User-Agent": "virtual-captain"})
    with urllib.request.urlopen(req, timeout=5, context=_ssl_context()) as r:
        body = r.read().decode("utf-8", "replace")
    if not body.strip():
        return None
    return metar_from_json(json.loads(body)) or None  # dizionario completo (qnh, vento, ...); Weather accetta anche un solo float


class Weather:
    def __init__(self, fetch=None, ttl: float = 600, retry: float = 60, stale: float = 3600, now=time.time, log=print):
        self.fetch, self.ttl, self.retry, self.stale, self.now, self.log = fetch, ttl, retry, stale, now, log
        self._lock = threading.Lock()
        self._online: dict = {}  # icao -> (dati METAR, t)
        self._seed: dict = {}  # icao -> dati METAR dal piano
        self._next: dict = {}  # icao -> istante del prossimo tentativo
        self._busy: set = set()
        self._failing: set = set()  # episodi di errore gia' loggati

    def seed(self, icao, raw_metar) -> None:
        """Ripiego dal METAR del piano SimBrief (l'online ha precedenza se non scaduto)."""
        icao = str(icao or "").strip().upper()
        m = metar_from_raw(raw_metar)
        if ICAO.match(icao) and set(m) - {"raw"}:  # almeno un dato leggibile
            with self._lock:
                self._seed[icao] = m

    def _value(self, icao) -> dict:
        """Dati METAR: l'online non scaduto e' l'UNICO dato (nessuna fusione per chiave: un campo assente, es. wx/clouds a cielo sereno,
        non va riempito da un METAR del piano piu' vecchio); il ripiego del piano vale solo se l'online manca o e' scaduto
        (il METAR del piano non ha altra scadenza)."""
        o = self._online.get(icao)
        if o and self.now() - o[1] <= self.stale:
            return {k: v for k, v in o[0].items() if v is not None}
        return dict(self._seed.get(icao) or {})

    def get(self, icao) -> float | None:
        """QNH in hPa dalla cache (o dal ripiego); non blocca mai. Rinfresca in background se serve."""
        return self.get_metar(icao).get("qnh")

    def get_metar(self, icao) -> dict:
        """Dati METAR (dizionario, vuoto se non ci sono) dalla cache o dal ripiego; stessa cache e rinfresco di get(), non blocca mai."""
        icao = str(icao or "").strip().upper()
        if not ICAO.match(icao):
            return {}
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
            r = self.fetch(icao)
            m = {"qnh": float(r)} if isinstance(r, (int, float)) and not isinstance(r, bool) else r  # un float: solo QNH (fetch semplici)
            if not isinstance(m, dict) or not m:
                raise ValueError("no QNH in response")
            with self._lock:
                self._online[icao] = (m, self.now())
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
