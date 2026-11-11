"""Stato condiviso tra il ciclo di lettura (PC) e la pagina web (tablet)."""
from __future__ import annotations

import json
import pathlib
import threading
from collections import deque
from datetime import datetime

from brain import Briefing
from simbrief import fetch_latest, to_briefing

CFG = pathlib.Path.home() / ".virtual-captain.json"
FIELDS = [("origin", "Departure"), ("destination", "Arrival"), ("runway", "Runway"),
          ("ta", "TA (ft)"), ("accel_agl", "Acceleration (ft AGL)"),
          ("v1", "V1"), ("vr", "VR"), ("v2", "V2"), ("flap", "Flap")]
FROM_PLAN = ["origin", "destination", "runway", "ta", "v1", "vr", "v2", "flap"]  # accel_agl è solo manuale
REMEMBERED = ["accel_agl"]  # unico campo dei dati di volo che si ricorda
MODES = ("training", "silent")


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


def _mode(m):
    """Valori vecchi (italiano) dal file di configurazione → inglese."""
    return {"allenamento": "training", "silenziosa": "silent"}.get(m, m if m in MODES else "training")


class State:
    def __init__(self, cfg_path: pathlib.Path = CFG):
        self.lock = threading.RLock()
        self.cfg_path = cfg_path
        try:
            cfg = json.loads(cfg_path.read_text())
        except (OSError, ValueError):
            cfg = {}
        # All'apertura solo ID SimBrief, modalità e accelerazione: il resto viene dal piano o si inserisce.
        self.fields = {k: (str(cfg.get(k, "")) if k in REMEMBERED else "") for k, _ in FIELDS}
        self.simbrief = str(cfg.get("simbrief", ""))
        self.mode = _mode(cfg.get("mode"))
        self.phase = "briefing"  # "briefing" | "flying"
        self.info = ""
        self.plan: dict = {}
        self.rev = 0  # cambia quando il server modifica i campi (es. piano caricato)
        self.aao_ok: bool | None = None
        self.log: deque = deque(maxlen=30)

    # -- modifiche dalla pagina --
    def update(self, data: dict):
        with self.lock:
            for k, _ in FIELDS:
                if k in data:
                    self.fields[k] = str(data[k]).strip()
            if "simbrief" in data:
                self.simbrief = str(data["simbrief"]).strip()
            if data.get("mode") in MODES:
                self.mode = data["mode"]
            self._save()

    def set_phase(self, phase: str):
        if phase in ("briefing", "flying"):
            with self.lock:
                self.phase = phase

    def load_plan(self, user: str | None = None):
        """Scarica l'ultimo piano: sovrascrive i campi del piano e svuota quelli che il piano non ha."""
        if user is not None:
            with self.lock:
                self.simbrief = user.strip()
        data = to_briefing(fetch_latest(self.simbrief))
        with self.lock:
            missing = []
            for k in FROM_PLAN:
                v = data.get(k)
                self.fields[k] = "" if v is None else str(v)
                if v is None:
                    missing.append(dict(FIELDS)[k])
            self.plan = data.get("plan") or {}
            self.info = (f"{data['origin']} → {data['destination']}  (generated: {_when(data['generated'])})"
                         + (f" — not in plan, enter manually: {', '.join(missing)}" if missing else ""))
            self.rev += 1
            self._save()

    def _save(self):
        try:
            self.cfg_path.write_text(json.dumps({"accel_agl": self.fields["accel_agl"],
                                                 "simbrief": self.simbrief, "mode": self.mode}))
        except OSError:
            pass

    # -- letture --
    def briefing(self) -> Briefing:
        def num(k, d):
            try:
                return int(self.fields[k])
            except ValueError:
                return d
        with self.lock:
            return Briefing(ta=num("ta", 5000), accel_agl=num("accel_agl", 1500), mode=self.mode)

    def add_log(self, text: str):
        with self.lock:
            self.log.appendleft(f"{datetime.now().strftime('%H:%M:%S')}  {text}")

    def snapshot(self) -> dict:
        with self.lock:
            return {"phase": self.phase, "fields": dict(self.fields), "simbrief": self.simbrief,
                    "mode": self.mode, "info": self.info, "plan": self.plan, "rev": self.rev,
                    "aao_ok": self.aao_ok, "log": list(self.log),
                    "labels": [[k, l] for k, l in FIELDS], "modes": list(MODES)}
