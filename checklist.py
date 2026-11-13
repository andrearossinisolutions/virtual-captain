"""Caricamento e validazione di checklist.json e checklist_links.json, e valutazione delle voci di una fase."""
from __future__ import annotations

import json
import pathlib

import monitor as mon
import thrust
from checklist_eval import BY_KEY, REFS, _refs, evaluate, qnh_actual

HERE = pathlib.Path(__file__).resolve().parent
TYPES = ("action", "announce", "atc", "note", "craft")
CRAFT_KEYS = ("c", "r", "a", "f", "t")
INPUT_KEYS = ("runway", "ta", "thr_red_agl", "accel_agl", "v1", "vr", "v2", "flap")  # campi di State.fields modificabili dalle voci


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
    if "detent" in check:
        d = check["detent"]
        names = [d] if isinstance(d, str) else d
        if inner or set(check) != {"detent"} or not isinstance(names, list) or not names or any(n not in thrust.DETENTS for n in names):
            raise ValueError(f"{where}: 'detent' non valido")
        return
    if "std" in check:
        if inner or check != {"std": True}:
            raise ValueError(f"{where}: 'std' non valido")
        return
    if "qnh" in check:
        if inner or set(check) != {"qnh"} or check["qnh"] not in ("wx.qnh_dep", "wx.qnh_dest"):
            raise ValueError(f"{where}: 'qnh' non valido")
        return
    if "all" in check:
        if not isinstance(check["all"], list) or not check["all"] or set(check) != {"all"}:
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
    if "eq_ref" in check:
        if set(check) != {"key", "eq_ref"} or check["eq_ref"] not in REFS:
            raise ValueError(f"{where}: 'eq_ref' non valido")
        if (check["eq_ref"] == "fld.flap") != (BY_KEY[key].kind == "flap"):  # fld.flap solo sui flap, e i flap solo con fld.flap
            raise ValueError(f"{where}: eq_ref {check['eq_ref']} non ammesso su {key}")
        if mon.MAPS[BY_KEY[key].kind] and BY_KEY[key].kind != "flap":
            raise ValueError(f"{where}: eq_ref non ammesso su {key} (ha una mappa di etichette)")
        return
    eq = check.get("eq")
    wanted = [eq] if isinstance(eq, str) else eq
    if not isinstance(wanted, list) or not wanted:
        raise ValueError(f"{where}: eq non valido")
    valid = set(mon.MAPS[BY_KEY[key].kind].values())
    for e in wanted:
        if e not in valid:
            raise ValueError(f"{where}: etichetta {e!r} non valida per {key}")


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
        if not isinstance(f.get("label"), str) or not f["label"] or not isinstance(f.get("plan", False), bool):
            raise ValueError(f"{it['id']}: campo di 'inputs' non valido")


def check_keys(check: dict) -> list:
    """Chiavi del monitor usate da un check (singolo o composto), in ordine e senza doppioni."""
    if "show" in check:
        return list(dict.fromkeys(check["show"]))
    if "detent" in check:
        return ["thr_det_l", "thr_det_r"]
    if "qnh" in check or "std" in check:
        return ["baro_std", "baro_hpa", "baro_inch"]
    subs = check.get("all") or [check]
    return list(dict.fromkeys(c["key"] for c in subs if c.get("key")))


def raw_values(check: dict | None, raw: dict) -> list:
    """Valori grezzi letti per un check (solo --test-links): [[variabile, valore|"n/d"], ...]."""
    out = []
    keys = check_keys(check or {})
    if "detent" in (check or {}):
        keys += ["thr_gate_l", "thr_gate_r", "thr_pos_1", "thr_pos_2"]
    for k in keys:
        it = BY_KEY[k]
        out.append([it.lvar or it.std or it.name, mon.raw_text(raw.get(k))])
    return out


def live_values(check: dict | None, labels: dict) -> list:
    """Valori reali letti per un check: [[nome, etichetta|"n/d"], ...] (sola lettura, vuoto se la voce non ha variabile)."""
    if "qnh" in (check or {}) or "std" in (check or {}):
        a = qnh_actual(labels)
        return [["Baro", "STD" if a == "STD" else (f"{a:.0f}" if a is not None else mon.NA)]]
    if "detent" in (check or {}):
        if any(labels.get(k) for k in ("thr_det_l", "thr_det_r")):
            return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in check_keys(check)]
        return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in ("thr_gate_l", "thr_gate_r", "thr_pos_1", "thr_pos_2")]
    return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in check_keys(check or {})]


class Checklist:
    def __init__(self, data: dict, links: dict, simplified: dict | None = None):
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
                    items[it["id"]] = it
        for iid, link in links.items():
            if iid not in items:
                raise ValueError(f"link su voce inesistente: {iid}")
            if items[iid]["type"] != "action":  # su craft (e su ogni altro tipo) nessun link
                raise ValueError(f"{iid}: solo le voci action possono avere un check")
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
                       expected: dict | None = None) -> dict:
        """Sottofasi e voci della fase con status/detail; "bad" = numero di voci rosse (solo quelle visibili). demo=True tratta i link come verificati.
        raw (solo --test-links): aggiunge a ogni voce con link "raw" = [[variabile, grezzo], ...].
        expected: valori attesi {riferimento: testo} per i link eq_ref/show_ref (da state.snapshot).
        simplified=True salta le voci escluse prima della valutazione e scarta le sottofasi rimaste vuote."""
        ph, bad, subs = self.phase(phase_id), 0, []
        for sp in ph["subphases"]:
            rows = []
            for it in sp["items"]:
                if simplified and it["id"] in self.excluded:
                    continue
                link = self.links.get(it["id"])
                res = evaluate(link["check"], labels, demo or link["verified"], link.get("optional", False), expected) if link else evaluate(None, labels)
                bad += res["status"] == "bad"
                row = {"id": it["id"], "text": it["text"], "response": it["response"], "type": it["type"],
                       "live": live_values(link["check"], labels) if link else [], "status": res["status"], "kind": res["kind"], "detail": res["detail"]}
                if link and ("show" in link["check"] or ("detent" in link["check"] and res["status"] == "none") or (_refs(link["check"]) and res["status"] == "none")):
                    row["show"] = True  # solo da mostrare: la pagina scrive sempre "NOW: <valore>"
                refs = ([link["check"]["show_ref"]] if "show_ref" in link["check"] else _refs(link["check"])) if link else []
                if refs:
                    row["expected"] = " / ".join(str((expected or {}).get(r, "")) for r in refs) if len(refs) > 1 else (expected or {}).get(refs[0], "")  # la pagina scrive "Expected: <valore>" se non vuoto
                if it["type"] == "craft":
                    row["fields"] = it["fields"]
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
    return Checklist(data, links, simplified)
