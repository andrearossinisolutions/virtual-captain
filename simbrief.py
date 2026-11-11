"""Scarica l'ultimo piano SimBrief (API pubblica, nessuna chiave)."""
import json
import urllib.parse
import urllib.request

URL = "https://www.simbrief.com/api/xml.fetcher.php"


def fetch_latest(user: str) -> dict:
    """user: Pilot ID numerico oppure username."""
    user = user.strip()
    key = "userid" if user.isdigit() else "username"
    qs = urllib.parse.urlencode({key: user, "json": 1})
    with urllib.request.urlopen(f"{URL}?{qs}", timeout=15) as r:
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
    out = {
        "origin": _g(ofp, "origin", "icao_code"),
        "destination": _g(ofp, "destination", "icao_code"),
        "runway": rwy,
        "ta": _num(_g(ofp, "origin", "trans_alt")),
        "cruise_alt": _num(_g(ofp, "general", "initial_altitude")),
        "generated": _g(ofp, "params", "time_generated"),
        "v1": None, "vr": None, "v2": None, "flap": None,
    }
    # Dati di decollo (TLR): presenti solo per alcuni profili, parsing difensivo.
    runways = _g(ofp, "tlr", "takeoff", "runway", default=[])
    if isinstance(runways, dict):
        runways = [runways]
    pick = next((r for r in runways if r.get("identifier") == rwy), None) or (runways[0] if runways else None)
    if pick:
        out["v1"] = _num(pick.get("v1_speed"))
        out["vr"] = _num(pick.get("vr_speed"))
        out["v2"] = _num(pick.get("v2_speed"))
        out["flap"] = pick.get("flap_setting")
    return out
