"""Stato condiviso tra il ciclo di lettura (PC) e la pagina web (tablet)."""
from __future__ import annotations

import json
import pathlib
import threading
import time
from collections import deque
from datetime import datetime

import checklist as cl
import monitor as mon
from brain import Briefing
from aao import AAO, normalize_url
from simbrief import fetch_latest, to_briefing

CFG = pathlib.Path.home() / ".virtual-captain.json"
FIELDS = [("origin", "Departure"), ("destination", "Arrival"), ("dep_time", "Departure time (UTC)"), ("runway", "Runway"),
          ("ta", "TA (ft)"), ("thr_red_agl", "Thrust reduction (ft AGL)"), ("accel_agl", "Acceleration (ft AGL)"),
          ("v1", "V1"), ("vr", "VR"), ("v2", "V2"), ("flap", "Flap")]
FROM_PLAN = ["origin", "destination", "dep_time", "runway", "ta", "v1", "vr", "v2", "flap"]  # accel_agl è solo manuale
REMEMBERED = ["thr_red_agl", "accel_agl"]  # unico campo dei dati di volo che si ricorda
MODES = ("normal", "training")
CL_MODES = ("complete", "simplified")


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


def _mode(m):
    """Normal è il default. Valori vecchi (allenamento/silenziosa/silent) dal file di configurazione → nuovi."""
    return {"allenamento": "training", "silenziosa": "normal", "silent": "normal"}.get(m, m if m in MODES else "normal")


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
        self.checklist_mode = cfg.get("checklist_mode") if cfg.get("checklist_mode") in CL_MODES else "complete"
        self.aao_url = normalize_url(cfg.get("aao_url") or None)
        self.phase = "briefing"  # "briefing" | "flying"
        self.info = ""
        self.plan: dict = {}
        self.rev = 0  # cambia quando il server modifica i campi (es. piano caricato)
        self.aao_ok: bool | None = None
        self.log: deque = deque(maxlen=30)
        self.spoke_at: float | None = None  # istante dell'ultima frase
        self.spoke_n = 0  # contatore frasi dette
        self.cl = cl.load()
        saved = cfg.get("checklist_phase")
        self.cl_phase = saved if self.cl.has_phase(saved) else self.cl.ids[0]  # sconosciuta -> prima fase
        self._fix_cl_phase()
        self.cl_demo = False  # SOLO --sim: tratta i link come verificati per vedere verde/rosso/grigio (in volo reale restano verified:false)
        self.cl_trust_links = False  # SOLO --test-links: valuta i link come verified:true, senza altri effetti demo
        self.raw: dict = {}  # {chiave: grezzo|None} per mostrare i valori letti in --test-links
        self.labels: dict = {}  # {chiave: etichetta pulita|None} per la checklist (vuoto fuori dal volo)
        self.monitor: list = []  # gruppi gia' etichettati: [[gruppo, [[nome, etichetta, atteso, grezzo], ...]], ...]

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
            if "checklist_mode" in data:
                m = data["checklist_mode"]
                self.checklist_mode = m if m in CL_MODES else "complete"
                self._fix_cl_phase()
            if data.get("aao_url") is not None:
                self.aao_url = normalize_url(str(data["aao_url"]).strip() or None)
            self._save()

    def test_aao(self, url: str | None = None) -> dict:
        """Prova l'indirizzo indicato (o quello salvato); se risponde, lo adotta e lo salva."""
        res = AAO(url or self.aao_url).test()
        if res["ok"]:
            with self.lock:
                self.aao_url = res["url"]
                self._save()
        return res

    def set_phase(self, phase: str):
        if phase in ("briefing", "flying"):
            with self.lock:
                self.phase = phase
                if phase != "flying":
                    self.monitor = []
                    self.labels = {}
                    self.raw = {}

    def set_monitor(self, raw: dict | None):
        """Riceve i grezzi {chiave: valore|None}, li etichetta e li tiene per la pagina (vuoto in briefing)."""
        with self.lock:
            live = raw is not None and self.phase == "flying"
            self.monitor = mon.build(raw) if live else []
            self.labels = mon.clean_labels(raw) if live else {}
            self.raw = dict(raw) if live and raw else {}

    def cl_visible(self) -> list[str]:
        return self.cl.visible_ids(self.checklist_mode == "simplified")

    def _fix_cl_phase(self) -> None:
        """Se la fase attuale e' nascosta (simplified), passa alla prima visibile."""
        vis = self.cl_visible()
        if self.cl_phase not in vis:
            self.cl_phase = vis[0]

    def set_cl_phase(self, target) -> None:
        """target: id di fase (str) oppure passo +1/-1 (int). Valore non valido => ValueError; ai limiti il passo non fa nulla."""
        with self.lock:
            if isinstance(target, str):
                if target not in self.cl_visible():
                    raise ValueError("unknown checklist phase")
                self.cl_phase = target
            elif isinstance(target, int) and not isinstance(target, bool) and target in (-1, 1):
                vis = self.cl_visible()
                i = vis.index(self.cl_phase) + target
                self.cl_phase = vis[min(max(i, 0), len(vis) - 1)]
            else:
                raise ValueError("step must be +1 or -1")
            self._save()

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
                                                 "thr_red_agl": self.fields["thr_red_agl"],
                                                 "simbrief": self.simbrief, "mode": self.mode,
                                                 "aao_url": self.aao_url, "checklist_phase": self.cl_phase,
                                                 "checklist_mode": self.checklist_mode}))
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
            return Briefing(ta=num("ta", 5000), thr_red_agl=num("thr_red_agl", 1500),
                            accel_agl=num("accel_agl", 1500), mode=self.mode,
                            v1=num("v1", None), vr=num("vr", None))

    def add_log(self, text: str):
        with self.lock:
            self.log.appendleft(f"{datetime.now().strftime('%H:%M:%S')}  {text}")
            self.spoke_at = time.time()
            self.spoke_n += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {"phase": self.phase, "fields": dict(self.fields), "simbrief": self.simbrief,
                    "mode": self.mode, "checklist_mode": self.checklist_mode, "cl_modes": list(CL_MODES), "info": self.info, "plan": self.plan, "rev": self.rev,
                    "aao_ok": self.aao_ok, "aao_url": self.aao_url, "log": list(self.log),
                    "now": time.time(), "spoke_n": self.spoke_n,
                    "spoke_ago": None if self.spoke_at is None else max(0.0, time.time() - self.spoke_at),
                    "labels": [[k, l] for k, l in FIELDS], "modes": list(MODES),
                    "monitor": [[g, [list(r) for r in rows]] for g, rows in self.monitor],
                    "cl_phases": [{"id": p["id"], "name": p["name"]} for p in self.cl.phases
                                  if p["id"] in self.cl_visible()],
                    "cl_phase": self.cl_phase,
                    "checklist": self.cl.evaluate_phase(self.cl_phase, self.labels, self.cl_demo or self.cl_trust_links,
                                                       self.checklist_mode == "simplified",
                                                       self.raw if self.cl_trust_links else None),
                    "test_links": self.cl_trust_links}
