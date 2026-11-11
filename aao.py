"""Lettura variabili del simulatore via WebAPI di AAO (Axis and Ohs)."""
import json
import os
import time
import urllib.parse
import urllib.request

VARS = {
    "on_ground": "(A:SIM ON GROUND, Bool)",
    "vs": "(A:VERTICAL SPEED, Feet per minute)",
    "agl": "(A:PLANE ALT ABOVE GROUND, Feet)",
    "alt": "(A:INDICATED ALTITUDE, Feet)",
    "ias": "(A:AIRSPEED INDICATED, Knots)",
    "gear": "(A:GEAR HANDLE POSITION, Bool)",
    "landing": "(A:LIGHT LANDING, Bool)",
    "seatbelt": "(A:CABIN SEATBELTS ALERT SWITCH, Bool)",
}


DEFAULT_URL = "http://127.0.0.1:43380/webapi"


def normalize_url(base=None) -> str:
    """Accetta anche solo "IP:porta"; aggiunge http:// e /webapi se mancano."""
    base = (base or os.environ.get("CAPTAIN_AAO_URL") or DEFAULT_URL).strip()
    if not base.startswith("http"):
        base = "http://" + base
    if not base.rstrip("/").endswith("/webapi"):
        base = base.rstrip("/") + "/webapi"
    return base


class AAO:
    def __init__(self, base=None):
        self.base = normalize_url(base)

    def read(self, timeout=5) -> dict:
        req = {"getvars": [{"var": v} for v in VARS.values()]}
        url = f"{self.base}?{urllib.parse.urlencode({'json': json.dumps(req)})}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.load(r)
        return self._parse(data)

    def test(self) -> dict:
        """Prova di connessione per la pagina: esito, tempo di risposta e valori letti (o l'errore)."""
        t0 = time.time()
        try:
            values = self.read(timeout=8)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "url": self.base, "ms": int((time.time() - t0) * 1000), "error": str(e)}
        return {"ok": True, "url": self.base, "ms": int((time.time() - t0) * 1000), "values": values}

    @staticmethod
    def _parse(data) -> dict:
        # Formato risposta da verificare dal vivo: accetto lista di {var,value} o dict.
        if isinstance(data, dict):
            data = data.get("getvars", data.get("vars", data))
        by_var = {}
        if isinstance(data, list):
            for e in data:
                by_var[e.get("var")] = e.get("value")
        elif isinstance(data, dict):
            by_var = data
        return {k: float(by_var.get(v, 0) or 0) for k, v in VARS.items()}
