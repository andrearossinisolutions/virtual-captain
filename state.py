"""Stato condiviso tra il ciclo di lettura (PC) e la pagina web (tablet)."""
from __future__ import annotations

import json
import pathlib
import threading
import time
from collections import deque
from datetime import datetime

import checklist as cl
import checklist_eval as ceval
import monitor as mon
import thrust
from brain import Briefing, join_dep, parse_dep
from aao import AAO, normalize_url
from simbrief import fetch_latest, to_briefing
from weather import Weather

CFG = pathlib.Path.home() / ".virtual-captain.json"
# Campi del Briefing (pagina di configurazione). origin contiene "ICAO/RWY" (es. LIMC/17R): la pista si ricava da li'.
FIELDS = [("origin", "Departure"), ("destination", "Arrival"), ("dep_time", "Departure time (UTC)")]
# Dati di decollo: si inseriscono nelle voci della checklist (FMGS PERFORMANCE); stesse chiavi in `fields`, non nel Briefing.
INFLIGHT = [("runway", "T/O runway"), ("ta", "Transition altitude (ft)"), ("thr_red_agl", "Thrust reduction (ft AGL)"),
            ("accel_agl", "Acceleration (ft AGL)"), ("v1", "V1"), ("vr", "VR"), ("v2", "V2"), ("flap", "Flap")]
ALL_FIELDS = FIELDS + INFLIGHT
FROM_PLAN = ["origin", "destination", "dep_time", "ta", "v1", "vr", "v2", "flap"]  # runway deriva da origin; accel/thr_red solo manuali
PLAN_RESET = ["runway", "ta", "v1", "vr", "v2", "flap"]  # tornano al piano con "Reset to plan"
REMEMBERED = ["thr_red_agl", "accel_agl"]  # unico campo dei dati di volo che si ricorda
MODES = ("normal", "training")
CL_MODES = ("complete", "simplified")
CLR_KEYS = ("c", "r", "a", "f", "t")  # CRAFT: cleared to, route, altitude, frequency, transponder (solo memoria, mai salvato)
CLR_MAX = {"c": 40, "r": 400, "a": 20, "f": 20, "t": 4}
CALIB_STALE = 3.0  # secondi: oltre, le letture non valgono per registrare un passo
CALIB_ACTIONS = ("start", "next", "back", "skip", "cancel", "clear")


def _empty_clearance() -> dict:
    return {k: "" for k in CLR_KEYS}


def _clean_clr(k: str, v) -> str:
    v = str(v).strip()
    if k == "t":
        return v if len(v) <= CLR_MAX["t"] and all(ch in "01234567" for ch in v) else None
    return v[:CLR_MAX[k]]


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


def _mode(m):
    """Normal è il default. Valori vecchi (allenamento/silenziosa/silent) dal file di configurazione → nuovi."""
    return {"allenamento": "training", "silenziosa": "normal", "silent": "normal"}.get(m, m if m in MODES else "normal")


class State:
    def __init__(self, cfg_path: pathlib.Path = CFG, weather=None):
        self.lock = threading.RLock()
        self.weather = weather or Weather()  # senza rete di default; main.py passa quella reale
        self.cfg_path = cfg_path
        try:
            cfg = json.loads(cfg_path.read_text())
        except (OSError, ValueError):
            cfg = {}
        # All'apertura solo ID SimBrief, modalità e accelerazione: il resto viene dal piano o si inserisce.
        self.fields = {k: (str(cfg.get(k, "")) if k in REMEMBERED else "") for k, _ in ALL_FIELDS}
        self.plan_fields: dict = {}  # valori del piano per i campi di PLAN_RESET (vuoto senza piano)
        self.simbrief = str(cfg.get("simbrief", ""))
        self.mode = _mode(cfg.get("mode"))
        self.checklist_mode = cfg.get("checklist_mode") if cfg.get("checklist_mode") in CL_MODES else "complete"
        self.aao_url = normalize_url(cfg.get("aao_url") or None)
        self.phase = "briefing"  # "briefing" | "flying"
        self.info = ""
        self.plan: dict = {}
        self.clearance = _empty_clearance()  # NON persiste al riavvio
        self.rev = 0  # cambia quando il server modifica i campi (es. piano caricato)
        self.aao_ok: bool | None = None
        self.log: deque = deque(maxlen=30)
        self.spoke_at: float | None = None  # istante dell'ultima frase
        self.spoke_n = 0  # contatore frasi dette
        self.thrust_cal = cfg.get("thrust_cal") if thrust.valid_cal(cfg.get("thrust_cal")) else None
        self.calib = {"active": False, "step": 0, "snaps": {}, "error": "", "done": False}  # wizard di calibrazione (solo memoria)
        self.calib_raw: deque = deque(maxlen=3)  # ultime letture delle variabili thrust: (istante, {chiave: grezzo})
        self.clock = time.time
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
            for k, _ in ALL_FIELDS:
                if k in data and k not in ("origin", "runway"):
                    self.fields[k] = str(data[k]).strip()
            if "origin" in data:
                self._set_origin(str(data["origin"]))
            if "runway" in data and "origin" not in data:
                self._set_runway(str(data["runway"]))
            if isinstance(data.get("reset"), list):
                self.reset_to_plan([k for k in data["reset"] if k in PLAN_RESET])
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

    def _set_origin(self, text: str) -> None:
        """Departure scritto come "ICAO/RWY": la pista (se valida) diventa il T/O runway."""
        text = text.strip().upper()
        self.fields["origin"] = text
        self.fields["runway"] = parse_dep(text)[1]

    def _set_runway(self, text: str) -> None:
        """T/O runway dalla checklist: aggiorna la pista dentro Departure. Vuoto = pista tolta; non valida = ignorata."""
        rwy = text.strip().upper()
        if rwy and not parse_dep("AAAA/" + rwy)[1]:
            return
        icao = parse_dep(self.fields["origin"])[0]
        self.fields["origin"] = join_dep(icao, rwy) if (icao or rwy) else ""
        self.fields["runway"] = rwy

    def reset_to_plan(self, keys: list) -> None:
        """Riporta ai valori del piano (vuoti se il piano non li ha) i campi indicati (chiamare con il lock preso)."""
        for k in keys:
            v = self.plan_fields.get(k, "")
            if k == "runway":
                self._set_runway(v)
            else:
                self.fields[k] = v

    def set_clearance(self, data: dict) -> None:
        """Aggiorna i campi CRAFT presenti in data (chiavi sconosciute ignorate). Valore non valido => ValueError, nulla cambia."""
        if not isinstance(data, dict):
            raise ValueError("body must be an object")
        new = {}
        for k in CLR_KEYS:
            if k in data:
                if not isinstance(data[k], (str, int)) or isinstance(data[k], bool):
                    raise ValueError(f"invalid {k}")
                v = _clean_clr(k, data[k])
                if v is None:
                    raise ValueError("squawk must be up to 4 digits 0-7")
                new[k] = v
        with self.lock:
            self.clearance.update(new)

    def reset_clearance(self) -> None:
        """CRAFT: c e r tornano al piano (se c'e'), a/f/t vuoti."""
        with self.lock:
            self.clearance = _empty_clearance()
            self.clearance["c"] = _clean_clr("c", self.fields.get("destination", ""))
            self.clearance["r"] = _clean_clr("r", (self.plan or {}).get("route") or "")

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
                if phase == "flying" and self.phase != "flying":
                    self.cl_phase = self.cl_visible()[0]  # "Briefing complete": la checklist riparte dalla prima fase
                    self._save()
                self.phase = phase
                if phase == "briefing":
                    self.reset_clearance()  # ritorno in briefing: CRAFT riparte dal piano
                if phase != "flying":
                    self.monitor = []
                    self.labels = {}
                    self.raw = {}

    def set_monitor(self, raw: dict | None):
        """Riceve i grezzi {chiave: valore|None}, li etichetta e li tiene per la pagina (vuoto in briefing)."""
        with self.lock:
            live = raw is not None and self.phase == "flying"
            if live:
                raw = {**raw, **thrust.annotate(raw, self.thrust_cal)}
                self.set_calib_raw(raw)
            self.monitor = mon.build(raw) if live else []
            self.labels = mon.clean_labels(raw) if live else {}
            self.raw = dict(raw) if live and raw else {}

    # -- calibrazione thrust --
    def set_calib_raw(self, raw: dict | None, now: float | None = None) -> None:
        """Tiene le ultime 3 letture delle variabili thrust (solo se c'e' almeno un valore numerico)."""
        if not raw:
            return
        vals = {k: raw.get(k) for k in thrust.RECORDED}
        if all(thrust._num(v) is None for v in vals.values()):
            return
        with self.lock:
            self.calib_raw.append((self.clock() if now is None else now, vals))

    def calib_active(self) -> bool:
        with self.lock:
            return bool(self.calib["active"])

    def calib_action(self, action) -> None:
        """Azione del wizard o clear. Non valida o non ammessa => ValueError, nulla cambia."""
        if action not in CALIB_ACTIONS:
            raise ValueError("unknown action")
        with self.lock:
            getattr(self, "calib_" + action)()

    def calib_start(self) -> None:
        self.calib = {"active": True, "step": 0, "snaps": {}, "error": "", "done": False}

    def calib_cancel(self) -> None:
        self.calib = {"active": False, "step": 0, "snaps": {}, "error": "", "done": False}

    def calib_clear(self) -> None:
        self.thrust_cal = None
        self._save()

    def calib_back(self) -> None:
        c = self.calib
        if not c["active"] or c["done"]:
            raise ValueError("calibration not in progress")
        if c["step"] > 0:
            c["step"] -= 1
            c["snaps"].pop(thrust.DETENTS[c["step"]], None)
        c["error"] = ""

    def calib_skip(self) -> None:
        if not self.calib["active"] or self.calib["done"]:
            raise ValueError("calibration not in progress")
        if self.thrust_cal is None:
            raise ValueError("nothing to keep: not calibrated yet")
        self._calib_advance(None)

    def calib_next(self) -> None:
        c = self.calib
        if not c["active"] or c["done"]:
            raise ValueError("calibration not in progress")
        now = self.clock()
        fresh = [v for t, v in self.calib_raw if now - t <= CALIB_STALE]
        snap = {}
        for k in thrust.RECORDED:
            xs = [thrust._num(v.get(k)) for v in fresh]
            xs = [x for x in xs if x is not None]
            if xs:
                snap[k] = thrust.median(xs)
        cands = [k for ks in thrust.CANDIDATES.values() for k in ks]
        if not any(k in snap for k in cands):
            raise ValueError("no live data from AAO")
        prev = c["snaps"].get(thrust.DETENTS[c["step"] - 1]) if c["step"] > 0 else None
        if prev is not None:
            both = [k for k in cands if k in snap and k in prev]
            if both and all(abs(snap[k] - prev[k]) < 1e-9 for k in both):
                raise ValueError("lever not moved")
        self._calib_advance(snap)

    def _calib_advance(self, snap) -> None:
        """Registra il passo (snap=None: salta e tiene il vecchio valore) e, all'ultimo, costruisce e salva la calibrazione."""
        c = self.calib
        c["error"] = ""
        c["snaps"][thrust.DETENTS[c["step"]]] = snap
        if c["step"] < len(thrust.DETENTS) - 1:
            c["step"] += 1
            return
        old = (self.thrust_cal or {}).get("raw", {})
        raw = {d: (c["snaps"][d] if c["snaps"][d] is not None else old.get(d, {})) for d in thrust.DETENTS}
        cal, err = thrust.build_cal(raw, self.clock())
        if cal is None:
            c["snaps"].pop(thrust.DETENTS[c["step"]], None)  # resta sull'ultimo passo, nulla scritto
            c["error"] = err
            raise ValueError(err)
        self.thrust_cal = cal
        c["step"], c["done"] = len(thrust.DETENTS), True
        self._save()

    def _thr_cal_view(self):
        cal = self.thrust_cal
        if not cal:
            return None
        names = {i.key: i.name for i in mon.ITEMS}
        return {"at": cal["at"], "when": _when(cal["at"]), "guides": {s: names[cal["levers"][s]["var"]] for s in ("l", "r")}}

    def _calib_view(self):
        c = self.calib
        if not c["active"]:
            return None
        now, last = self.clock(), None
        if self.calib_raw and now - self.calib_raw[-1][0] <= CALIB_STALE:
            last = self.calib_raw[-1][1]
        done = [d for d in thrust.DETENTS if d in c["snaps"]]
        names = {i.key: i.name for i in mon.ITEMS}
        rows = []
        for k in thrust.RECORDED:
            vals = [("kept" if c["snaps"][d] is None else (mon.raw_text(c["snaps"][d].get(k)) or "-")) for d in done]
            rows.append({"name": names[k], "live": mon.raw_text(last.get(k)) if last else "", "vals": vals,
                         "guide": k in [x for ks in thrust.CANDIDATES.values() for x in ks]})
        step = c["step"]
        return {"active": True, "done": c["done"], "step": step, "total": len(thrust.DETENTS), "error": c["error"],
                "name": thrust.STEP_NAMES[step] if step < len(thrust.DETENTS) else "",
                "text": thrust.STEPS[step]["text"] if step < len(thrust.DETENTS) else "",
                "heads": thrust.STEP_SHORT[:len(done)], "rows": rows, "can_skip": self.thrust_cal is not None,
                "has_live": last is not None}

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
            icao, rwy = parse_dep(data.get("origin"))
            rwy = rwy or parse_dep("AAAA/" + str(data.get("runway") or ""))[1]
            data = {**data, "origin": join_dep(icao, rwy) if icao else data.get("origin")}
            for k in FROM_PLAN:
                v = data.get(k)
                self.fields[k] = "" if v is None else str(v)
                if v is None:
                    missing.append(dict(ALL_FIELDS)[k])
            self.fields["runway"] = rwy
            if not rwy:
                missing.append(dict(ALL_FIELDS)["runway"])
            self.plan_fields = {k: self.fields[k] for k in PLAN_RESET}
            self.plan = data.get("plan") or {}
            self.weather.seed(icao, self.plan.get("orig_metar"))
            self.weather.seed(data.get("destination"), self.plan.get("dest_metar"))
            self.reset_clearance()
            self.info = (f"{icao or data['origin']} → {data['destination']}  (generated: {_when(data['generated'])})"
                         + (f" — not in plan, enter manually: {', '.join(missing)}" if missing else ""))
            self.rev += 1
            self._save()

    def _save(self):
        try:
            self.cfg_path.write_text(json.dumps({"accel_agl": self.fields["accel_agl"],
                                                 "thr_red_agl": self.fields["thr_red_agl"],
                                                 "simbrief": self.simbrief, "mode": self.mode,
                                                 "aao_url": self.aao_url, "checklist_phase": self.cl_phase,
                                                 "checklist_mode": self.checklist_mode,
                                                 **({"thrust_cal": self.thrust_cal} if self.thrust_cal else {})}))
        except OSError:
            pass

    def _expected(self) -> dict:
        """Valori attesi per i riferimenti dei link (clr.*, fld.*, plan.*); chiamare con il lock preso."""
        e = {f"clr.{k}": v for k, v in self.clearance.items()}
        e["fld.runway"], e["fld.ta"] = self.fields.get("runway", ""), self.fields.get("ta", "")
        for k in ("v1", "vr", "v2"):
            v = self.fields.get(k)
            e[f"fld.{k}"] = "" if v in (None, "") else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v))
        e["fld.flap"] = ceval.norm_label("flap", self.fields.get("flap") or "") or ""  # non riconosciuto -> vuoto (solo NOW)
        for ref, k in (("wx.qnh_dep", "origin"), ("wx.qnh_dest", "destination")):
            q = self.weather.get(parse_dep(self.fields.get(k, ""))[0])
            e[ref] = f"{q:.0f} hPa" if q is not None else ""
        e["cal.thrust"] = "1" if self.thrust_cal else ""  # vuoto: i link {"detent"} mostrano solo NOW
        e["plan.sid"] = str((self.plan or {}).get("sid") or "")
        return e

    # -- letture --
    def briefing(self) -> Briefing:
        def num(k):  # vuoto o non numerico = dato non noto (None): il capitano non lo annuncia mai
            try:
                return int(self.fields[k])
            except ValueError:
                return None
        with self.lock:
            return Briefing(ta=num("ta"), thr_red_agl=num("thr_red_agl"), accel_agl=num("accel_agl"), mode=self.mode,
                            v1=num("v1"), vr=num("vr"))

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
                                                       self.raw if self.cl_trust_links else None, self._expected()),
                    "test_links": self.cl_trust_links, "clearance": dict(self.clearance),
                    "thr_cal": self._thr_cal_view(), "calib": self._calib_view()}
