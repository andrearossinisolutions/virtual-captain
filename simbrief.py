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
        out["flap"] = pick.get("flap_setting")
        for name in ("v1", "vr", "v2"):
            out[name] = _find_speed(pick, name)
    return out


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


def explore(ofp: dict, words=("v1", "vr", "v2", "flap", "tlr", "takeoff", "flex", "speed")):
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
