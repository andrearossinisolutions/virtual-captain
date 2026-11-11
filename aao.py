"""Lettura variabili del simulatore via WebAPI di AAO (Axis and Ohs)."""
import json
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


class AAO:
    def __init__(self, base="http://localhost:43380/webapi"):
        self.base = base

    def read(self) -> dict:
        req = {"getvars": [{"var": v} for v in VARS.values()]}
        url = f"{self.base}?{urllib.parse.urlencode({'json': json.dumps(req)})}"
        with urllib.request.urlopen(url, timeout=2) as r:
            data = json.load(r)
        return self._parse(data)

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
