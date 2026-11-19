"""Stato condiviso tra il ciclo di lettura (PC) e la pagina web (tablet)."""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time
from collections import deque
from datetime import datetime

import checklist as cl
import checklist_eval as ceval
from checklist_latch import Latch
import monitor as mon
import thrust
from brain import Briefing, join_dep, parse_dep
import dll_fetch
import simconnect_backend
from simbrief import fetch_latest, to_briefing
from weather import Weather, format_metar

CFG = pathlib.Path.home() / ".virtual-captain.json"
# Campi del Briefing (pagina di configurazione). origin contiene "ICAO/RWY" (es. LIMC/17R): la pista si ricava da li'.
FIELDS = [("origin", "Departure"), ("destination", "Arrival"), ("dep_time", "Departure time (UTC)")]
# Dati di decollo: si inseriscono nelle voci della checklist (FMGS PERFORMANCE); stesse chiavi in `fields`, non nel Briefing.
INFLIGHT = [("runway", "T/O runway"), ("ta", "Transition altitude (ft)"), ("thr_red_agl", "Thrust reduction (ft AGL)"),
            ("accel_agl", "Acceleration (ft AGL)"), ("v1", "V1"), ("vr", "VR"), ("v2", "V2"), ("flap", "Flap"),
            ("fob", "FOB"), ("zfw", "ZFW"), ("zfwcg", "ZFWCG"), ("taxiway", "Taxiway"),
            ("atis_letter", "ATIS letter"), ("atis_rwy", "Landing runway(s)"), ("atis_wind", "Wind"), ("atis_qnh", "QNH"), ("atis_tl", "Transition level"),
            ("datis_letter", "ATIS letter"), ("datis_rwy", "Departure runway(s)"), ("datis_wind", "Wind"), ("datis_qnh", "QNH")]
ALL_FIELDS = FIELDS + INFLIGHT
FROM_PLAN = ["origin", "destination", "dep_time", "ta", "v1", "vr", "v2", "flap", "fob", "zfw"]  # runway deriva da origin; accel/thr_red solo manuali
PLAN_RESET = ["runway", "ta", "v1", "vr", "v2", "flap", "fob", "zfw"]  # tornano al piano con "Reset to plan"
REMEMBERED = ["thr_red_agl", "accel_agl"]  # unico campo dei dati di volo che si ricorda
MODES = ("normal", "training")
CL_MODES = ("complete", "simplified")
CLR_KEYS = ("c", "w", "r", "a", "f", "t")  # CRAFT: cleared to, pista in uso (opzionale), route, altitude, frequency, transponder (solo memoria, mai salvato)
CLR_MAX = {"c": 40, "w": 12, "r": 400, "a": 20, "f": 20, "t": 4}


def _empty_clearance() -> dict:
    return {k: "" for k in CLR_KEYS}


def _clean_clr(k: str, v) -> str:
    v = str(v).strip()
    if k == "t":
        return v if len(v) <= CLR_MAX["t"] and all(ch in "01234567" for ch in v) else None
    if k == "w":
        return clean_field("atis_rwy", v)  # stesso sanificatore delle piste ("17R/17L")
    return v[:CLR_MAX[k]]


ATIS_PARTS = ("letter", "rwy", "wind", "qnh")
ATIS_KEYS = tuple("atis_" + p for p in ATIS_PARTS + ("tl",))  # annotazioni dell'ATIS di arrivo: solo memoria, azzerate col nuovo piano e al ritorno in briefing
DATIS_KEYS = tuple("datis_" + p for p in ATIS_PARTS)  # ATIS di partenza: solo memoria, azzerate solo col nuovo piano
QNH_OK = ((950.0, 1050.0), (28.0, 31.0))  # QNH da ATIS plausibile: hPa (4 cifre) o inHg (con punto)
QNH_WARN = 3.0  # hPa: oltre questa differenza tra QNH dell'ATIS e del METAR si avvisa (solo avviso)


def rwy_conflict(annotated, plan) -> bool:
    """True se la pista del piano NON e' tra quelle annotate nell'ATIS ("17R/17L"); confronto senza maiuscole/spazi; manca una delle due -> False."""
    ann = {r for r in re.split(r"[/\s,]+", str(annotated or "").upper()) if r}
    p = re.sub(r"\s+", "", str(plan or "")).upper()
    return bool(ann and p and p not in ann)


def atis_qnh_hpa(text) -> float | None:
    """QNH annotato dall'ATIS in hPa: 4 cifre (950-1050) oppure inHg con punto (28.0-31.0, x33.8639); non plausibile -> None."""
    s = str(text or "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    if "." in s:
        return v * 33.8639 if QNH_OK[1][0] <= v <= QNH_OK[1][1] else None
    return v if len(s) == 4 and QNH_OK[0][0] <= v <= QNH_OK[0][1] else None


def clean_field(k: str, v) -> str:
    """Sanifica i campi con regole proprie (valore non valido: scartato, resta vuoto); gli altri solo strip."""
    s = "" if v is None else str(v).strip()
    if k == "taxiway":
        return clean_taxiway(s)
    u = s.upper()
    if k.startswith(("atis_", "datis_")):
        k = "atis_" + k.split("_", 1)[1]  # stesse regole per arrivo (atis_) e partenza (datis_)
    if k == "atis_letter":
        return u[:1] if u[:1].isascii() and u[:1].isalpha() else ""
    if k == "atis_rwy":
        u = re.sub(r"[\s,]+", "/", u)  # spazi (anche tab), virgole -> "/"
        return re.sub(r"/+", "/", "".join(c for c in u if c in "0123456789LRC/"))[:12].strip("/")
    if k == "atis_wind":
        return "".join(c for c in u if c in "0123456789/GVRBKT")[:12]
    if k == "atis_qnh":
        out, dot = "", False
        for c in s:
            if c.isascii() and c.isdigit():
                out += c
            elif c == "." and not dot:
                out, dot = out + c, True
        return out[:5]
    if k == "atis_tl":  # transition level (solo arrivo): "070" / "FL70" -> prefisso FL scartato, solo cifre, max 3
        fl = u.startswith("FL")  # prefisso FL esplicito: riempito a 3 cifre ("FL70" -> "070"); senza FL nessun riempimento (digitazione in corso)
        d = "".join(c for c in re.sub(r"^FL", "", u) if c.isascii() and c.isdigit())[:3]
        return d.zfill(3) if fl and d else d
    return s


def clean_taxiway(text) -> str:
    """Taxiway di uscita: maiuscolo, solo A-Z e 0-9, max 4 caratteri (es. " b5 " -> "B5")."""
    return "".join(c for c in str(text).upper() if c.isascii() and c.isalnum())[:4]


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


def _mode(m):
    """Normal è il default. Valori vecchi (allenamento/silenziosa/silent) dal file di configurazione → nuovi."""
    return {"allenamento": "training", "silenziosa": "normal", "silent": "normal"}.get(m, m if m in MODES else "normal")


def _txt(v) -> str:
    n = mon.to_number(v)
    return "" if n is None else str(n)


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
        self.wt_unit = ""  # unita' dei pesi del piano SimBrief ("kgs"/"lbs"), vuota senza piano
        self.plan_fields: dict = {}  # valori del piano per i campi di PLAN_RESET (vuoto senza piano)
        self.simbrief = str(cfg.get("simbrief", ""))
        self.mode = _mode(cfg.get("mode"))
        self.checklist_mode = cfg.get("checklist_mode") if cfg.get("checklist_mode") in CL_MODES else "complete"
        self.phase = "briefing"  # "briefing" | "flying"
        self.info = ""
        self.plan: dict = {}
        self.clearance = _empty_clearance()  # NON persiste al riavvio
        self.rev = 0  # cambia quando il server modifica i campi (es. piano caricato)
        self.sim_ok: bool | None = None  # SimConnect: None = non ancora noto, True = dati freschi, False = non connesso
        self.sim_error = ""
        self.dll_missing = False  # SimConnect.dll non trovata: si puo' scaricare (dopo il consenso)
        self.sim_demo = False  # solo --sim: il simulatore non si legge
        self.sim_backend = None  # SimConnectBackend, assegnato da main.py (None in --sim e nei test)
        self.dll_fetching = False
        self.dll_msg = ""  # esito dell'ultimo download (vuoto se ok)
        self.log: deque = deque(maxlen=30)
        self.spoke_at: float | None = None  # istante dell'ultima frase
        self.spoke_n = 0  # contatore frasi dette
        self.clock = time.time
        self.cl = cl.load()
        saved = cfg.get("checklist_phase")
        self.cl_phase = saved if self.cl.has_phase(saved) else self.cl.ids[0]  # sconosciuta -> prima fase
        self._fix_cl_phase()
        self.latch = Latch()  # voci soddisfatte congelate (aggiornato solo da set_monitor; azzerato come i gate)
        self.gates = {"open": set(), "done": set()}  # gate aperti e voci viste ok (aggiornati solo da set_monitor; azzerati con la fase/il piano)
        self.cl_demo = False  # SOLO --sim: tratta i link come verificati per vedere verde/rosso/grigio (in volo reale restano verified:false)
        self.cl_trust_links = False  # SOLO --test-links: valuta i link come verified:true, senza altri effetti demo
        self.raw: dict = {}  # {chiave: grezzo|None} per mostrare i valori letti in --test-links
        self.labels: dict = {}  # {chiave: etichetta pulita|None} per la checklist (vuoto fuori dal volo)
        self.core: dict = {}  # ultimo campione del core (alt, ias, vs, ...), per i dati di volo
        self.flight: list = []  # righe [etichetta, testo] dei dati di volo (vuoto fuori da "flying")
        self.pills: dict = {}  # GS, quota, VS per l'header
        self.monitor: list = []  # gruppi gia' etichettati: [[gruppo, [[nome, etichetta, atteso, grezzo], ...]], ...]

    # -- modifiche dalla pagina --
    def update(self, data: dict):
        with self.lock:
            for k, _ in ALL_FIELDS:
                if k in data and k not in ("origin", "runway"):
                    self.fields[k] = clean_field(k, data[k])
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

    def clear_atis(self, keys=ATIS_KEYS) -> None:
        """Annotazioni ATIS vuote (default arrivo; chiamare con il lock preso)."""
        for k in keys:
            self.fields[k] = ""

    def reset_clearance(self) -> None:
        """CRAFT: c e r tornano al piano (se c'e'), a/f/t vuoti."""
        with self.lock:
            self.clearance = _empty_clearance()
            self.clearance["c"] = _clean_clr("c", self.fields.get("destination", ""))
            self.clearance["r"] = _clean_clr("r", (self.plan or {}).get("route") or "")

    def fetch_dll(self) -> None:
        """Scarica la SimConnect.dll in vendor/ (la conferma dell'utente e' gia' stata data dalla pagina) e la ricarica."""
        be = self.sim_backend
        if be is None or not be.dll_missing:
            raise ValueError("SimConnect.dll is not missing")

        def done(result) -> None:
            with self.lock:
                self.dll_fetching = False
                self.dll_msg = "" if result.ok else result.message
            if result.ok:
                be.retry_dll()

        with self.lock:
            self.dll_fetching = True
        if dll_fetch.start_fetch(simconnect_backend.vendor_dir(), done) is None:
            with self.lock:
                self.dll_fetching = True  # un download e' gia' in corso
            raise ValueError("download already in progress")

    def sim_retry(self) -> None:
        """Pulsante Retry: il backend riprova subito (DLL e apertura di SimConnect)."""
        if self.sim_backend is not None:
            self.sim_backend.retry()

    def set_sim(self, ok: bool | None, error: str = "", dll_missing: bool = False) -> None:
        with self.lock:
            self.sim_ok, self.dll_missing, self.sim_error = ok, dll_missing, error

    def set_phase(self, phase: str):
        if phase in ("briefing", "flying"):
            with self.lock:
                if phase == "flying" and self.phase != "flying":
                    self.cl_phase = self.cl_visible()[0]  # "Briefing complete": la checklist riparte dalla prima fase
                    self._save()
                self.phase = phase
                self._reset_gates()
                if phase == "briefing":
                    self.reset_clearance()  # ritorno in briefing: CRAFT riparte dal piano
                    self.clear_atis()
                if phase != "flying":
                    self.monitor = []
                    self.flight, self.pills = [], {}
                    self.labels = {}
                    self.raw = {}

    def _reset_gates(self, from_phase: str | None = None) -> None:
        """Azzera i gate: tutti (None) oppure quelli della fase indicata e delle successive (retrocessione di fase, go-around); lock preso."""
        if from_phase is None:
            self.gates["open"].clear()
            self.gates["done"].clear()
            self.latch.clear_all()
            return
        ids = {it["id"] for ph in self.cl.phases[self.cl.ids.index(from_phase):] for sp in ph["subphases"] for it in sp["items"]}
        self.gates["open"] -= ids
        self.gates["done"] -= ids
        self.latch.clear(ids)

    def set_core(self, values: dict | None) -> None:
        """Ultimo campione del core (da chiamare prima di set_monitor)."""
        with self.lock:
            self.core = dict(values) if values else {}

    def set_monitor(self, raw: dict | None):
        """Riceve i grezzi {chiave: valore|None}, li etichetta e li tiene per la pagina (vuoto in briefing)."""
        with self.lock:
            live = raw is not None and self.phase == "flying"
            if live:
                raw = {**raw, **thrust.annotate(raw), **mon.weight_annotate(raw), **mon.vs_annotate(raw, self.core), **mon.autobrake_state(raw), **mon.gnd_annotate(raw, self.core)}
            self.monitor = mon.build(raw) if live else []
            self.labels = mon.clean_labels(raw) if live else {}
            self.raw = dict(raw) if live and raw else {}
            self.flight = mon.flight_panel(raw, self.core) if live else []
            self.pills = mon.flight_pills(raw, self.core) if live else {}
            if live:  # i gate si aggiornano qui (~2 Hz, un solo thread), non dallo snapshot
                self.cl.evaluate_phase(self.cl_phase, self.labels, self.cl_demo or self.cl_trust_links, self.checklist_mode == "simplified",
                                       None, self._expected(), self._wx_data(), self.gates, update=True,
                                       latched=self.latch, now=self.clock())

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
            vis, old = self.cl_visible(), self.cl_phase
            if isinstance(target, str):
                if target not in vis:
                    raise ValueError("unknown checklist phase")
                self.cl_phase = target
            elif isinstance(target, int) and not isinstance(target, bool) and target in (-1, 1):
                i = vis.index(self.cl_phase) + target
                self.cl_phase = vis[min(max(i, 0), len(vis) - 1)]
            else:
                raise ValueError("step must be +1 or -1")
            if old in vis and vis.index(self.cl_phase) < vis.index(old):
                self._reset_gates(self.cl_phase)  # solo retrocedere (es. go-around) azzera i gate di destinazione e successive; avanzare o ricaricare la fase no
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
            self.clear_atis()
            self.clear_atis(DATIS_KEYS)  # l'ATIS di partenza si azzera solo col nuovo piano
            self.fields["taxiway"] = ""  # nuovo volo: il taxiway di uscita dell'atterraggio precedente non vale piu'
            if not rwy:
                missing.append(dict(ALL_FIELDS)["runway"])
            self.wt_unit = data.get("wt_unit") or ""
            self.plan_fields = {k: self.fields[k] for k in PLAN_RESET}
            self.plan = data.get("plan") or {}
            self.weather.seed(icao, self.plan.get("orig_metar"))
            self.weather.seed(data.get("destination"), self.plan.get("dest_metar"))
            self.reset_clearance()
            self._reset_gates()
            self.info = (f"{icao or data['origin']} → {data['destination']}  (generated: {_when(data['generated'])})"
                         + (f" — not in plan, enter manually: {', '.join(missing)}" if missing else ""))
            self.rev += 1
            self._save()

    def _save(self):
        try:
            self.cfg_path.write_text(json.dumps({"accel_agl": self.fields["accel_agl"],
                                                 "thr_red_agl": self.fields["thr_red_agl"],
                                                 "simbrief": self.simbrief, "mode": self.mode,
                                                 "checklist_phase": self.cl_phase,
                                                 "checklist_mode": self.checklist_mode}))
        except OSError:
            pass

    def _expected(self) -> dict:
        """Valori attesi per i riferimenti dei link (clr.*, fld.*, plan.*); chiamare con il lock preso."""
        e = {f"clr.{k}": v for k, v in self.clearance.items()}
        e["fld.runway"], e["fld.ta"] = self.fields.get("runway", ""), self.fields.get("ta", "")
        for k in ("v1", "vr", "v2"):
            v = self.fields.get(k)
            e[f"fld.{k}"] = "" if v in (None, "") else (str(int(v)) if isinstance(v, float) and v == int(v) else str(v))
        for k in ("fob", "zfw", "zfwcg"):
            e[f"fld.{k}"] = self.fields.get(k, "")
        e["fld.fob_unit"] = self.wt_unit  # solo per la conversione kgs/lbs del confronto FOB (non e' un riferimento dei link)
        e["fld.flap"] = ceval.norm_label("flap", self.fields.get("flap") or "") or ""  # non riconosciuto -> vuoto (solo NOW)
        for ref, k, pre in (("wx.qnh_dep", "origin", "datis"), ("wx.qnh_dest", "destination", "atis")):
            a = atis_qnh_hpa(self.fields.get(pre + "_qnh"))  # QNH annotato dall'ATIS (se plausibile) prima del METAR
            q = self.weather.get(parse_dep(self.fields.get(k, ""))[0])
            e[ref] = f"{a:.0f} hPa (ATIS)" if a is not None else (f"{q:.0f} hPa (METAR)" if q is not None else "")
        tl = self.fields.get("atis_tl", "")
        alt = self.core.get("alt")
        e["fld.atis_tl"] = tl  # livello di transizione dell'ATIS di arrivo: la voce baro a TL si valuta solo sotto
        e["sys.alt"] = "" if alt is None else str(alt)  # quota letta dal monitor (pill ALT), piedi
        e["fld.thr_red_agl"], e["fld.accel_agl"] = self.fields.get("thr_red_agl", ""), self.fields.get("accel_agl", "")
        e["sys.agl"], e["sys.ias"], e["sys.vs"] = (_txt(self.core.get(k)) for k in ("agl", "ias", "vs"))
        gnd = mon.to_number(self.core.get("on_ground"))
        e["sys.gnd"] = "" if gnd is None else ("1" if gnd >= 0.5 else "0")
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

    def _wx(self, field: str):
        """METAR dell'aeroporto di un campo (origin/destination) gia' formattato ({"icao","line","raw"}) o None; non blocca (cache/ripiego); lock preso."""
        icao = parse_dep(self.fields.get(field, ""))[0]
        return format_metar(icao, self.weather.get_metar(icao)) if icao else None

    def _wx_data(self) -> dict:
        """METAR (dati parsati, vuoti se assenti) di partenza e destinazione per i controlli dinamici (anti-ice); non blocca; lock preso."""
        out = {}
        for key, field in (("dep", "origin"), ("dest", "destination")):
            icao = parse_dep(self.fields.get(field, ""))[0]
            out[key] = dict(self.weather.get_metar(icao)) if icao else {}
            out[key]["icao"] = icao or ""
        return out

    def _rwy_bad(self) -> dict:
        """{"atis": bool, "datis": bool}: pista annotata nell'ATIS diversa da quella del piano (partenza: runway/origin; arrivo: pista in destination, se c'e'); solo avviso."""
        dep_rwy = self.fields.get("runway") or parse_dep(self.fields.get("origin", ""))[1]
        arr_rwy = parse_dep(self.fields.get("destination", ""))[1]
        return {"atis": rwy_conflict(self.fields.get("atis_rwy"), arr_rwy), "datis": rwy_conflict(self.fields.get("datis_rwy"), dep_rwy)}

    def _qnh_warn(self) -> dict:
        """{"atis": testo, "datis": testo}: avviso se il QNH annotato (arrivo/partenza) differisce dal METAR di piu' di QNH_WARN hPa (solo avviso)."""
        out = {}
        for pre, field in (("atis", "destination"), ("datis", "origin")):
            a, q = atis_qnh_hpa(self.fields.get(pre + "_qnh")), self.weather.get(parse_dep(self.fields.get(field, ""))[0])
            out[pre] = f"METAR QNH {q:.0f}" if a is not None and q is not None and abs(a - q) > QNH_WARN else ""
        return out

    def snapshot(self) -> dict:
        with self.lock:
            return {"wx_dest": self._wx("destination"), "wx_dep": self._wx("origin"), "atis_warn": self._qnh_warn(), "rwy_bad": self._rwy_bad(), "phase": self.phase, "fields": dict(self.fields), "simbrief": self.simbrief,
                    "mode": self.mode, "checklist_mode": self.checklist_mode, "cl_modes": list(CL_MODES), "info": self.info, "plan": self.plan, "rev": self.rev,
                    "sim_ok": self.sim_ok, "sim_error": self.sim_error, "dll_missing": self.dll_missing, "sim_demo": self.sim_demo,
                    "dll_fetching": self.dll_fetching, "dll_msg": self.dll_msg, "dll_confirm": dll_fetch.describe(), "log": list(self.log),
                    "now": time.time(), "spoke_n": self.spoke_n,
                    "spoke_ago": None if self.spoke_at is None else max(0.0, time.time() - self.spoke_at),
                    "labels": [[k, l] for k, l in FIELDS], "modes": list(MODES),
                    "flight": [list(r) for r in self.flight], "pills": dict(self.pills), "pill_defs": mon.active_pill_defs(),
                    "monitor": [[g, [list(r) for r in rows]] for g, rows in self.monitor],
                    "cl_phases": [{"id": p["id"], "name": p["name"]} for p in self.cl.phases
                                  if p["id"] in self.cl_visible()],
                    "cl_phase": self.cl_phase,
                    "checklist": self.cl.evaluate_phase(self.cl_phase, self.labels, self.cl_demo or self.cl_trust_links,
                                                       self.checklist_mode == "simplified",
                                                       self.raw if self.cl_trust_links else None, self._expected(), self._wx_data(), self.gates,
                                                       latched=self.latch),
                    "test_links": self.cl_trust_links, "clearance": dict(self.clearance)}
