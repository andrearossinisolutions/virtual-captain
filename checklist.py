"""Caricamento e validazione di checklist.json e checklist_links.json, e valutazione delle voci di una fase."""
from __future__ import annotations

import json
import pathlib

import icing
import monitor as mon
import thrust
from checklist_eval import _res, BY_KEY, GATE_AGL_REFS, GATE_ALT_REFS, GATE_RULES, REFS, _refs, evaluate, gate_ctx, qnh_actual

HERE = pathlib.Path(__file__).resolve().parent
ATIS_HINT = ("letter", "rwy", "qnh", "wind", "tl")  # dati ATIS annotati mostrati sotto una voce (ordine canonico della riga)
TYPES = ("action", "announce", "atc", "note", "craft")
CRAFT_KEYS = ("c", "w", "r", "a", "f", "t")
INPUT_KEYS = ("runway", "ta", "thr_red_agl", "accel_agl", "v1", "vr", "v2", "flap", "fob", "zfw", "zfwcg", "taxiway", "atis_letter", "atis_rwy", "atis_wind", "atis_qnh", "atis_tl", "datis_letter", "datis_rwy", "datis_wind", "datis_qnh")  # campi di State.fields modificabili dalle voci


def _check_keys(check: dict, where: str, inner: bool = False) -> None:
    if "show_ref" in check:
        if inner or set(check) != {"show_ref"} or check["show_ref"] not in REFS:
            raise ValueError(f"{where}: 'show_ref' non valido")
        return
    if "show" in check:
        keys = check["show"]
        if set(check) != {"show"} or not isinstance(keys, list) or not keys or any(k not in BY_KEY for k in keys):
            raise ValueError(f"{where}: 'show' non valido")
        return
    if "gsx" in check:  # solo voci announce, solo chiavi con mappa gsx
        keys = check["gsx"]
        if inner or not set(check) <= {"gsx", "set"} or check.get("set", "active") not in mon.GSX_SETS or not isinstance(keys, list) or not keys or any(k not in BY_KEY or BY_KEY[k].kind != "gsx" for k in keys):
            raise ValueError(f"{where}: 'gsx' non valido")
        return
    if "ai" in check:  # anti-ice dinamico: solo da solo (mai dentro 'all'), regola nota
        if inner or set(check) != {"ai"} or check["ai"] not in icing.RULES:
            raise ValueError(f"{where}: 'ai' non valido")
        return
    if "detent" in check:
        d = check["detent"]
        names = [d] if isinstance(d, str) else d
        if inner or set(check) != {"detent"} or not isinstance(names, list) or not names or (names != ["BETWEEN"] and any(n not in thrust.DETENTS for n in names)):  # BETWEEN ammesso solo da solo
            raise ValueError(f"{where}: 'detent' non valido")
        return
    if "std" in check:
        if inner or check != {"std": True}:
            raise ValueError(f"{where}: 'std' non valido")
        return
    if "qnh" in check:
        if inner or set(check) - {"qnh", "gate_tl"} or check.get("gate_tl", True) is not True or check["qnh"] not in ("wx.qnh_dep", "wx.qnh_dest"):
            raise ValueError(f"{where}: 'qnh' non valido")
        return
    if "all" in check:
        if not isinstance(check["all"], list) or not check["all"] or set(check) - {"all", "required"} or "all" not in check or check.get("required", True) is not True \
                or ("required" in check and not all(isinstance(c, dict) and c.get("key") in BY_KEY and BY_KEY[c["key"]].kind == "numeric" for c in check["all"])):
            raise ValueError(f"{where}: 'all' non valido")
        for sub in check["all"]:
            if "all" in sub:
                raise ValueError(f"{where}: 'all' annidato non ammesso")
            _check_keys(sub, where, True)
            if "eq_ref" in sub and not all("eq_ref" in c for c in check["all"]):
                raise ValueError(f"{where}: eq_ref dentro 'all' solo se tutte le voci lo usano")
        return
    key = check.get("key")
    if key not in BY_KEY:
        raise ValueError(f"{where}: chiave sconosciuta {key!r}")
    if "tol_ref" in check:  # numerico con tolleranza percentuale sull'atteso di un campo (solo fuori da 'all')
        tol = check.get("tol_pct")
        if inner or set(check) != {"key", "tol_ref", "tol_pct"} or check["tol_ref"] not in ("fld.fob", "fld.zfw") or BY_KEY[key].kind != "numeric" \
                or isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 < tol <= 50:
            raise ValueError(f"{where}: 'tol_ref' non valido")
        return
    if "eq_ref" in check:
        if set(check) != {"key", "eq_ref"} or check["eq_ref"] not in REFS:
            raise ValueError(f"{where}: 'eq_ref' non valido")
        if (check["eq_ref"] == "fld.flap") != (BY_KEY[key].kind == "flap"):  # fld.flap solo sui flap, e i flap solo con fld.flap
            raise ValueError(f"{where}: eq_ref {check['eq_ref']} non ammesso su {key}")
        if mon.MAPS[BY_KEY[key].kind] and BY_KEY[key].kind != "flap":
            raise ValueError(f"{where}: eq_ref non ammesso su {key} (ha una mappa di etichette)")
        return
    if "lt" in check:  # numerico: valore < soglia (es. VS < 0), solo fuori da 'all'
        if inner or set(check) != {"key", "lt"} or BY_KEY[key].kind != "numeric" or isinstance(check["lt"], bool) or not isinstance(check["lt"], (int, float)):
            raise ValueError(f"{where}: 'lt' non valido")
        return
    eq = check.get("eq")
    wanted = [eq] if isinstance(eq, str) else eq
    if not isinstance(wanted, list) or not wanted:
        raise ValueError(f"{where}: eq non valido")
    valid = set(mon.MAPS[BY_KEY[key].kind].values())
    for e in wanted:
        if e not in valid:
            raise ValueError(f"{where}: etichetta {e!r} non valida per {key}")


def _check_gate_rule(rule, where: str, order: dict) -> None:
    """Regola di un gate: chiavi note, valori del tipo giusto, riferimenti ammessi, after_item su una voce precedente della stessa fase."""
    if not isinstance(rule, dict) or not rule or set(rule) - set(GATE_RULES):
        raise ValueError(f"gates {where}: regola non valida")
    for k in ("alt_ge", "alt_le", "vs_gt", "agl_le", "ias_le"):
        if k in rule and (isinstance(rule[k], bool) or not isinstance(rule[k], (int, float))):
            raise ValueError(f"gates {where}: {k!r} non numerico")
    if "alt_ge_ref" in rule and rule["alt_ge_ref"] not in GATE_ALT_REFS:
        raise ValueError(f"gates {where}: 'alt_ge_ref' non ammesso")
    if "agl_ge_ref" in rule and rule["agl_ge_ref"] not in GATE_AGL_REFS:
        raise ValueError(f"gates {where}: 'agl_ge_ref' non ammesso")
    for k in ("airborne", "ground"):
        if k in rule and rule[k] is not True:
            raise ValueError(f"gates {where}: {k!r} deve essere true")
    if "after_item" in rule:
        ref = rule["after_item"]
        me = order.get(where)
        if ref not in order or (me is not None and (order[ref][0] != me[0] or order[ref][1] >= me[1])):
            raise ValueError(f"gates {where}: 'after_item' deve essere una voce precedente della stessa fase")


def _check_craft(it: dict) -> None:
    fields = it.get("fields")
    if not isinstance(fields, list) or [f.get("k") for f in fields if isinstance(f, dict)] != list(CRAFT_KEYS):
        raise ValueError(f"{it['id']}: 'fields' di craft non valido")
    for f in fields:
        if not isinstance(f.get("label"), str) or not f["label"] or f.get("src", "plan.destination") not in ("plan.destination", "plan.route"):
            raise ValueError(f"{it['id']}: campo craft non valido")


def _check_inputs(it: dict) -> None:
    """Campi inline di una voce action (valori in State.fields): chiavi note, niente doppioni, etichetta obbligatoria."""
    inputs = it["inputs"]
    if it["type"] != "action" or not isinstance(inputs, list) or not inputs:
        raise ValueError(f"{it['id']}: 'inputs' ammesso solo su voci action, lista non vuota")
    keys = [f.get("k") for f in inputs if isinstance(f, dict)]
    if len(keys) != len(inputs) or any(k not in INPUT_KEYS for k in keys) or len(keys) != len(set(keys)):
        raise ValueError(f"{it['id']}: 'inputs' con chiavi non valide o duplicate")
    for f in inputs:
        if not isinstance(f.get("label"), str) or not f["label"] or not isinstance(f.get("plan", False), bool) \
                or ("prefix" in f and (not isinstance(f["prefix"], str) or not f["prefix"].strip())):
            raise ValueError(f"{it['id']}: campo di 'inputs' non valido")


def check_keys(check: dict) -> list:
    """Chiavi del monitor usate da un check (singolo o composto), in ordine e senza doppioni."""
    if "show" in check:
        return list(dict.fromkeys(check["show"]))
    if "gsx" in check:
        return list(dict.fromkeys(check["gsx"]))
    if "detent" in check:
        return ["thr_det_l", "thr_det_r"]
    if "ai" in check:
        return ["eng1_ai", "eng2_ai", "sat", "tat", "gnd"]
    if "qnh" in check or "std" in check:
        return ["baro_std", "baro_hpa", "baro_inch"]
    subs = check.get("all") or [check]
    return list(dict.fromkeys(c["key"] for c in subs if c.get("key")))


def raw_values(check: dict | None, raw: dict) -> list:
    """Valori grezzi letti per un check (solo --test-links): [[variabile, valore|"n/d"], ...]."""
    out = []
    keys = check_keys(check or {})
    if "detent" in (check or {}):
        keys += ["thr_gate_l", "thr_gate_r"]
    for k in keys:
        it = BY_KEY[k]
        out.append([it.lvar or it.std or it.name, mon.raw_text(raw.get(k))])
    return out


def live_values(check: dict | None, labels: dict, live_raw: dict | None = None) -> list:
    """Valori reali letti per un check: [[nome, etichetta|"n/d"], ...] (sola lettura, vuoto se la voce non ha variabile).
    Check "gsx": una sola coppia ["GSX", "Refuel AVAIL \u00b7 Catering AVAIL"] (nome servizio + stato, senza grezzo: quello resta in --test-links e --probe);
    live_raw serve solo a leggere lo stato (anche senza --test-links)."""
    if "gsx" in (check or {}):
        return [["GSX", " \u00b7 ".join(mon.gsx_live_text(k, (live_raw or {}).get(k)) for k in check["gsx"])]]
    if "qnh" in (check or {}) or "std" in (check or {}):
        a = qnh_actual(labels)
        return [["Baro", "STD" if a == "STD" else (f"{a:.0f}" if a is not None else mon.NA)]]
    if "detent" in (check or {}):
        if any(labels.get(k) for k in ("thr_det_l", "thr_det_r")):
            return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in check_keys(check)]
        return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in ("thr_gate_l", "thr_gate_r")]
    return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in check_keys(check or {})]


class Checklist:
    def __init__(self, data: dict, links: dict, simplified: dict | None = None, gates: dict | None = None):
        self.data, self.links = data, links
        self.phases = data["phases"]
        ids, items = set(), {}
        for ph in self.phases:
            if not ph.get("id") or ph["id"] in ids:
                raise ValueError(f"id fase non valido o duplicato: {ph.get('id')!r}")
            ids.add(ph["id"])
            for sp in ph["subphases"]:
                for it in sp["items"]:
                    if it["type"] not in TYPES:
                        raise ValueError(f"{it['id']}: tipo non valido {it['type']!r}")
                    if it["id"] in items or it["id"] in ids:
                        raise ValueError(f"id duplicato: {it['id']}")
                    if it["type"] == "craft":
                        _check_craft(it)
                    if "inputs" in it:
                        _check_inputs(it)
                    if "layout" in it and (it["layout"] != "table" or "inputs" not in it):  # solo rendering: card a tabella per i campi inline
                        raise ValueError(f"{it['id']}: 'layout' non valido")
                    if "wx_hint" in it and (it["type"] not in ("action", "atc") or it["wx_hint"] not in ("dest", "dep")):
                        raise ValueError(f"{it['id']}: 'wx_hint' non valido")
                    for hk in ("atis_hint", "datis_hint"):  # righe ATIS di arrivo / partenza
                        if hk in it:
                            h = it[hk]
                            if it["type"] not in ("atc", "action") or not isinstance(h, list) or not h or len(set(h)) != len(h) or any(x not in ATIS_HINT for x in h):
                                raise ValueError(f"{it['id']}: {hk!r} non valido")
                    items[it["id"]] = it
        for iid, link in links.items():
            if iid not in items:
                raise ValueError(f"link su voce inesistente: {iid}")
            is_gsx = "gsx" in (link.get("check") or {})  # unica eccezione: check GSX su voci announce
            if items[iid]["type"] != ("announce" if is_gsx else "action"):  # su craft (e su ogni altro tipo) nessun link
                raise ValueError(f"{iid}: solo le voci action (o announce con check gsx) possono avere un check")
            if not isinstance(link.get("verified"), bool):
                raise ValueError(f"{iid}: 'verified' mancante")
            if not isinstance(link.get("optional", False), bool):
                raise ValueError(f"{iid}: 'optional' non valido")
            _check_keys(link.get("check") or {}, iid)
        self.ids = [p["id"] for p in self.phases]
        excl = (simplified or {}).get("exclude", [])
        if not isinstance(excl, list):
            raise ValueError("simplified: 'exclude' deve essere una lista")
        for iid in excl:
            if iid not in items:
                raise ValueError(f"simplified: voce inesistente: {iid}")
        if len(excl) != len(set(excl)):
            raise ValueError("simplified: id duplicati")
        self.excluded = frozenset(excl)
        self.names = {iid: it["text"] for iid, it in items.items()}
        self._init_gates(gates or {}, items)

    def _init_gates(self, gates: dict, items: dict) -> None:
        """Gate (voci in attesa di un evento): per id sottofase (vale per tutte le sue voci) e per id voce (sostituisce quello della sottofase).
        Risultato: self.gates = {id voce: regola}, solo per le voci action/announce con link (le altre non possono avere un esito)."""
        sub_ids = {sp["id"]: sp for ph in self.phases for sp in ph["subphases"]}
        order = {it["id"]: (pi, n) for pi, ph in enumerate(self.phases) for n, it in enumerate(i for sp in ph["subphases"] for i in sp["items"])}
        if set(gates) - {"version", "subphases", "items"}:
            raise ValueError("gates: chiavi sconosciute")
        subs, its = gates.get("subphases", {}), gates.get("items", {})
        if not isinstance(subs, dict) or not isinstance(its, dict):
            raise ValueError("gates: 'subphases' e 'items' devono essere oggetti")
        for sid, rule in subs.items():
            if sid not in sub_ids:
                raise ValueError(f"gates: sottofase inesistente: {sid}")
            _check_gate_rule(rule, sid, order)
        for iid, rule in its.items():
            if iid not in items:
                raise ValueError(f"gates: voce inesistente: {iid}")
            _check_gate_rule(rule, iid, order)
        self.gates = {}
        for sid, rule in subs.items():
            for it in sub_ids[sid]["items"]:
                if it["id"] in self.links:
                    self.gates[it["id"]] = rule
        for iid, rule in its.items():
            if iid in self.links:
                self.gates[iid] = rule
            else:
                raise ValueError(f"gates: {iid} non ha un link")

    def has_phase(self, phase_id) -> bool:
        return isinstance(phase_id, str) and phase_id in self.ids

    def visible_ids(self, simplified: bool = False) -> list[str]:
        """Id delle fasi con almeno una voce visibile (in complete: tutte)."""
        if not simplified:
            return list(self.ids)
        return [p["id"] for p in self.phases
                if any(it["id"] not in self.excluded for sp in p["subphases"] for it in sp["items"])]

    def phase(self, phase_id: str) -> dict:
        return self.phases[self.ids.index(phase_id)]

    def evaluate_phase(self, phase_id: str, labels: dict, demo: bool = False, simplified: bool = False, raw: dict | None = None,
                       expected: dict | None = None, wx: dict | None = None, gates: dict | None = None, update: bool = False,
                       latched=None, now: float = 0.0, live_raw: dict | None = None) -> dict:
        """Sottofasi e voci della fase con status/detail; "bad" = numero di voci rosse (solo quelle visibili). demo=True tratta i link come verificati.
        raw (solo --test-links): aggiunge a ogni voce con link "raw" = [[variabile, grezzo], ...].
        expected: valori attesi {riferimento: testo} per i link eq_ref/show_ref (da state.snapshot).
        wx: {"dep": METAR, "dest": METAR} dei dati parsati (da state.snapshot), per i check "ai" (anti-ice dinamico).
        gates: {"open": set, "done": set} di State (gate aperti = restano aperti; done = voci viste ok, per after_item); update=True li aggiorna
        (solo da State.set_monitor, mai dallo snapshot). Gate non aperto => status "wait" (non conta in "bad").
        latched: Latch di State (voci soddisfatte congelate): con update=True osserva lo stato grezzo (istante now); una voce congelata ha status "ok",
        kind "latched", latched=True e detail = quello grezzo (vuoto se ancora ok), la riga live resta quella reale. Non conta in "bad".
        live_raw: valori grezzi sempre disponibili (anche senza --test-links), solo per la riga live dei check GSX (grezzo accanto allo stato).
        simplified=True salta le voci escluse prima della valutazione e scarta le sottofasi rimaste vuote."""
        ph, bad, subs = self.phase(phase_id), 0, []
        g_open, g_done = (gates["open"], gates["done"]) if gates is not None else (set(), set())
        done = set(g_done)
        for sp in ph["subphases"]:
            rows = []
            for it in sp["items"]:
                if simplified and it["id"] in self.excluded:
                    continue
                link = self.links.get(it["id"])
                exp, ai = expected, None
                if link and "ai" in link["check"]:  # anti-ice dinamico: testo e richiesta ON dalla regola (METAR, SAT, TAT)
                    ai = icing.ai_rule(link["check"]["ai"], labels, wx)
                    exp = {**(expected or {}), "ai.on": "1" if ai["on"] else "", "ai.text": ai["text"]}
                rule = self.gates.get(it["id"]) if gates is not None else None
                gate = (rule, gate_ctx(expected, done, self.names)) if rule and it["id"] not in g_open else None
                res = evaluate(link["check"], labels, demo or link["verified"], link.get("optional", False), exp, gate) if link else evaluate(None, labels)
                if rule and gate is not None and res["status"] != "wait" and update and (res["status"] != "none"):
                    g_open.add(it["id"])  # gate aperto: resta aperto
                raw_ok = res["status"] == "ok"
                if latched is not None and update and gates is not None and link:
                    latched.observe(it["id"], raw_ok, now)
                is_latched = latched is not None and it["id"] in latched
                if is_latched:
                    res = _res("ok", "" if raw_ok else res["detail"], "latched")
                if res["status"] == "ok":
                    done.add(it["id"])
                    if update and gates is not None:
                        g_done.add(it["id"])
                bad += res["status"] == "bad"
                row = {"id": it["id"], "text": it["text"], "response": it["response"], "type": it["type"],
                       "live": live_values(link["check"], labels, live_raw) if link else [], "status": res["status"], "kind": res["kind"], "detail": res["detail"]}
                if is_latched:
                    row["latched"] = True
                    if not raw_ok:
                        row["was_ok"] = True  # grezzo non ok: la pagina scrive "was OK \u00b7 NOW: <live>"
                if res["kind"] == "wait":
                    row["gate"] = res["detail"]
                if link and ("show" in link["check"] or "gsx" in link["check"] or ("detent" in link["check"] and res["status"] == "none") or (_refs(link["check"]) and res["status"] == "none") or (ai is not None and res["status"] == "none")):
                    row["show"] = True  # solo da mostrare: la pagina scrive sempre "NOW: <valore>"
                refs = ([link["check"]["show_ref"]] if "show_ref" in link["check"] else _refs(link["check"])) if link else []
                if ai is not None:
                    row["expected"] = ai["text"]  # al posto di AS REQUIRED: la pagina scrive il testo dinamico
                elif refs:
                    row["expected"] = " / ".join(str((expected or {}).get(r, "")) for r in refs) if len(refs) > 1 else (expected or {}).get(refs[0], "")  # la pagina scrive "Expected: <valore>" se non vuoto
                if it["type"] == "craft":
                    row["fields"] = it["fields"]
                if it.get("wx_hint"):
                    row["wx_hint"] = it["wx_hint"]  # la pagina scrive il METAR della destinazione (snapshot wx_dest)
                if it.get("datis_hint"):
                    row["datis_hint"] = it["datis_hint"]
                if it.get("atis_hint"):
                    row["atis_hint"] = it["atis_hint"]  # la pagina scrive la riga "ATIS: ..." dai campi annotati
                if it.get("layout"):
                    row["layout"] = it["layout"]
                if it.get("inputs"):
                    row["inputs"] = it["inputs"]  # la pagina li scrive inline; i valori stanno in snapshot["fields"]
                if raw is not None and link:
                    row["raw"] = raw_values(link["check"], raw)
                if it.get("note"):
                    row["note"] = it["note"]
                rows.append(row)
            if rows:
                subs.append({"id": sp["id"], "name": sp["name"], "items": rows})
        return {"phase": {"id": ph["id"], "name": ph["name"]}, "bad": bad, "subphases": subs}


def load(base: pathlib.Path = HERE) -> Checklist:
    data = json.loads((base / "checklist.json").read_text(encoding="utf-8"))
    links = json.loads((base / "checklist_links.json").read_text(encoding="utf-8"))
    simplified = json.loads((base / "checklist_simplified.json").read_text(encoding="utf-8"))
    gates = json.loads((base / "checklist_gates.json").read_text(encoding="utf-8"))
    return Checklist(data, links, simplified, gates)
