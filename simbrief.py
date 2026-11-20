"""Scarica l'ultimo piano SimBrief (API pubblica, nessuna chiave)."""
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

from brain import join_dep, parse_dep

URL = "https://www.simbrief.com/api/xml.fetcher.php"


def _ssl_context() -> ssl.SSLContext:
    """Contesto TLS con verifica attiva. Se il Python non ha certificati radice (capita con pyenv su Mac),
    usa certifi oppure il file di sistema di macOS/Linux: la verifica non si disattiva mai."""
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca"):
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ctx


def fetch_latest(user: str) -> dict:
    """user: Pilot ID numerico oppure username."""
    user = user.strip()
    key = "userid" if user.isdigit() else "username"
    qs = urllib.parse.urlencode({key: user, "json": 1})
    with urllib.request.urlopen(f"{URL}?{qs}", timeout=15, context=_ssl_context()) as r:
        return json.load(r)


def _g(d, *path, default=None):
    for p in path:
        if isinstance(d, dict) and p in d:
            d = d[p]
        else:
            return default
    return d


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_briefing(ofp: dict) -> dict:
    """Estrae i campi utili; ciò che non c'è resta None (da inserire a mano)."""
    rwy = _g(ofp, "origin", "plan_rwy")
    icao, prwy = parse_dep(f"{_g(ofp, 'origin', 'icao_code') or ''}/{rwy or ''}")
    out = {
        "origin": join_dep(icao, prwy) if icao else _g(ofp, "origin", "icao_code"),  # ICAO/RWY, es. LIMC/17R
        "destination": _g(ofp, "destination", "icao_code"),
        "runway": rwy,
        "dep_time": _utc_time(_g(ofp, "times", "sched_out")),
        "ta": _num(_g(ofp, "origin", "trans_alt")),
        "cruise_alt": _num(_g(ofp, "general", "initial_altitude")),
        "generated": _g(ofp, "params", "time_generated"),
        "v1": None, "vr": None, "v2": None, "flap": None,
        # Carburante al blocco (FOB) e ZFW stimato, nell'unita' del piano ("kgs" o "lbs"); ZFWCG non e' nel JSON.
        "fob": _num(_g(ofp, "fuel", "plan_ramp")), "zfw": _num(_g(ofp, "weights", "est_zfw")),
        "wt_unit": _unit(_g(ofp, "params", "units")),
    }
    out["plan"] = extract_plan(ofp)
    # Dati di decollo (TLR): presenti solo per alcuni profili, parsing difensivo.
    runways = _g(ofp, "tlr", "takeoff", "runway", default=[])
    if isinstance(runways, dict):
        runways = [runways]
    pick = next((r for r in runways if r.get("identifier") == rwy), None) or (runways[0] if runways else None)
    if pick:
        out["flap"] = pick.get("flap_setting")
        for name in ("v1", "vr", "v2"):
            out[name] = _find_speed(pick, name)
    return out


def _unit(v):
    """Unita' dei pesi del piano: "kgs" o "lbs" (altro/assente -> None, valori senza unita' nota)."""
    s = str(v or "").strip().lower()
    return "kgs" if s.startswith("kg") else "lbs" if s.startswith("lb") else None


def _utc_time(ts):
    n = _num(ts)
    if n is None:
        return None
    import datetime
    return datetime.datetime.fromtimestamp(n, datetime.timezone.utc).strftime("%H:%M")


def _hhmm(sec):
    n = _num(sec)
    return None if n is None else f"{n // 3600}h{(n % 3600) // 60:02d}"


def _metar(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


_PROC = re.compile(r"^[A-Z]{3,6}\d[A-Z]$")
_FIX = re.compile(r"^[A-Z]{5}$")


def route_procedures(route) -> tuple:
    """(sid, transizione, star) ricavati dalla stringa di rotta; None dove la rotta non li mostra.
    Ignora i token "ICAO/RWY" (LIMC/35R). La SID e' il primo token se ha forma di procedura (PEP2Q),
    la transizione il secondo se e' un fix di 5 lettere (PEPAG, non DCT); la STAR e' l'ultimo token
    con forma di procedura (KELI3G)."""
    toks = [x for x in str(route or "").upper().split() if "/" not in x]
    sid = trans = star = None
    if toks and _PROC.match(toks[0]):
        sid = toks[0]
        if len(toks) > 1 and _FIX.match(toks[1]) and toks[1] != "DCT":
            trans = toks[1]
    if len(toks) > 1 and _PROC.match(toks[-1]):
        star = toks[-1]
    return sid, trans, star


SIMBRIEF_HOME = "https://dispatch.simbrief.com/home"
SIMBRIEF_BRIEFING = "https://dispatch.simbrief.com/briefing/%s"
_OFP_ID = re.compile(r"[0-9]{1,20}")


def _ofp_id(v):
    """OFP id (params.request_id of the JSON) as a digit string, else None."""
    s = str(v) if isinstance(v, (str, int)) and not isinstance(v, bool) else ""
    return s if _OFP_ID.fullmatch(s) else None


def briefing_url(plan) -> str:
    """Page to open from the window: the briefing of the loaded plan, the SimBrief home without plan or with an invalid id."""
    oid = _ofp_id((plan or {}).get("ofp_id") if isinstance(plan, dict) else None)
    return SIMBRIEF_BRIEFING % oid if oid else SIMBRIEF_HOME


def extract_plan(ofp: dict) -> dict:
    """Piano di volo da mostrare. Campi assenti restano vuoti (parsing difensivo: formato non verificato)."""
    g = _g(ofp, "general", default={}) or {}
    flight = f"{g.get('icao_airline') or ''}{g.get('flight_number') or ''}".strip() or None
    fixes = _g(ofp, "navlog", "fix", default=[])
    if isinstance(fixes, dict):
        fixes = [fixes]
    wpts = [{"ident": f.get("ident"), "via": f.get("via_airway"), "alt": _num(f.get("altitude_feet"))}
            for f in fixes if isinstance(f, dict) and f.get("ident")]
    route = g.get("route") or _g(ofp, "atc", "route")
    r_sid, r_trans, r_star = route_procedures(route)
    return {
        "flight": flight,
        "ofp_id": _ofp_id(_g(ofp, "params", "request_id")),
        "aircraft": _g(ofp, "aircraft", "icaocode"),
        "route": route,
        # sid_ident/sid_trans/star_ident del JSON non sono affidabili (20/11/2026): prima la rotta, il JSON e' il ripiego
        "sid": r_sid or g.get("sid_ident") or None,
        "sid_trans": (r_trans if r_sid else g.get("sid_trans")) or None,
        "star": r_star or g.get("star_ident") or None,
        "cruise_alt": _num(g.get("initial_altitude")),
        "distance_nm": _num(g.get("air_distance") or g.get("route_distance")),
        "ete": _hhmm(_g(ofp, "times", "est_time_enroute")),
        "alternate": _g(ofp, "alternate", "icao_code"),
        "orig_metar": _metar(_g(ofp, "weather", "orig_metar")),
        "dest_metar": _metar(_g(ofp, "weather", "dest_metar")),
        "waypoints": wpts,
    }


def _find_speed(d, name):
    """Cerca una chiave che sia name o inizi/finisca con name (v1, v1_speed, speed_v1...), anche annidata."""
    if isinstance(d, dict):
        for k, v in d.items():
            kl = str(k).lower()
            if kl in (name, f"{name}_speed", f"speed_{name}") and not isinstance(v, (dict, list)):
                n = _num(v)
                if n is not None:
                    return n
        for v in d.values():
            r = _find_speed(v, name)
            if r is not None:
                return r
    return None


def explore(ofp: dict, words=("v1", "vr", "v2", "flap", "tlr", "takeoff", "flex", "speed", "route", "sid", "star", "alternate")):
    """Elenca percorso e valore dei campi che sembrano dati di decollo (per capire il formato)."""
    rows = []

    def walk(d, path):
        if isinstance(d, dict):
            for k, v in d.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(d, list):
            for i, v in enumerate(d[:3]):
                walk(v, f"{path}[{i}]")
        elif any(w in path.lower() for w in words):
            rows.append(f"{path} = {str(d)[:60]}")

    walk(ofp, "")
    return rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("uso: python simbrief.py <ID o username>")
    for r in explore(fetch_latest(sys.argv[1])):
        print(r)
