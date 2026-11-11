"""Lettura variabili del simulatore via WebAPI di AAO (Axis and Ohs)."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from monitor import ITEMS, lvar_request, std_request, to_number

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


# Voci del monitor del cockpit: chiave -> sorgente standard e L-var Fenix affiancate (solo lettura).
MONITOR = {i.key: {"std": std_request(i), "lvar": lvar_request(i)} for i in ITEMS}
_ITEMS = {i.key: i for i in ITEMS}


RETRY_FULL = 30  # secondi tra un nuovo tentativo della richiesta piena e l'altro dopo un fallback
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
        self._core_only_until = 0.0  # dopo un fallback riuscito non si rifa' la richiesta piena fino a questo istante

    def _get(self, names, timeout):
        req = {"getvars": [{"var": v} for v in names]}
        url = f"{self.base}?{urllib.parse.urlencode({'json': json.dumps(req)})}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)

    def read_all(self, timeout=5) -> tuple:
        """Una sola richiesta con VARS+MONITOR. Se fallisce per un motivo diverso dalla rete (es. L-var
        sconosciute) ritenta col solo core e per RETRY_FULL s non riprova la richiesta piena."""
        core = list(VARS.values())
        if time.time() >= self._core_only_until:
            names = core + [v for m in MONITOR.values() for v in m.values() if v]
            try:
                return self._parse(self._get(names, timeout))
            except urllib.error.HTTPError:
                pass
            except OSError:
                raise  # AAO irraggiungibile o lenta: niente secondo tentativo (timeout non raddoppiato)
            except ValueError:
                pass
        values, _ = self._parse(self._get(core, timeout))
        if time.time() >= self._core_only_until:
            self._core_only_until = time.time() + RETRY_FULL
        return values, {k: None for k in MONITOR}

    def read(self, timeout=5) -> dict:
        return self.read_all(timeout)[0]

    def test(self) -> dict:
        """Prova di connessione per la pagina: esito, tempo di risposta e valori letti (o l'errore)."""
        t0 = time.time()
        try:
            values = self.read(timeout=8)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "url": self.base, "ms": int((time.time() - t0) * 1000), "error": str(e)}
        return {"ok": True, "url": self.base, "ms": int((time.time() - t0) * 1000), "values": values}

    @staticmethod
    def _parse(data) -> tuple:
        """-> (values, monitor). values: come prima (0 se manca, per il comandante); monitor: None se assente o non numerica."""
        # Formato risposta da verificare dal vivo: accetto lista di {var,value} o dict.
        if isinstance(data, dict):
            data = data.get("getvars", data.get("vars", data))
        by_var = {}
        if isinstance(data, list):
            for e in data:
                by_var[e.get("var")] = e.get("value")
        elif isinstance(data, dict):
            by_var = data
        values = {k: float(by_var.get(v, 0) or 0) for k, v in VARS.items()}
        return values, {k: _monitor_value(_ITEMS[k], m, by_var) for k, m in MONITOR.items()}


def _monitor_value(item, names, by_var):
    v = to_number(by_var.get(names["lvar"])) if names["lvar"] else None
    if v is None and item.std_fallback and names["std"]:
        v = to_number(by_var.get(names["std"]))
    if v is None and not item.lvar and names["std"]:
        v = to_number(by_var.get(names["std"]))
    return v
