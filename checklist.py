"""Caricamento e validazione di checklist.json e checklist_links.json, e valutazione delle voci di una fase."""
from __future__ import annotations

import json
import pathlib

import monitor as mon
from checklist_eval import BY_KEY, evaluate

HERE = pathlib.Path(__file__).resolve().parent
TYPES = ("action", "announce", "atc", "note")


def _check_keys(check: dict, where: str) -> None:
    if "all" in check:
        if not isinstance(check["all"], list) or not check["all"] or set(check) != {"all"}:
            raise ValueError(f"{where}: 'all' non valido")
        for sub in check["all"]:
            if "all" in sub:
                raise ValueError(f"{where}: 'all' annidato non ammesso")
            _check_keys(sub, where)
        return
    key = check.get("key")
    if key not in BY_KEY:
        raise ValueError(f"{where}: chiave sconosciuta {key!r}")
    eq = check.get("eq")
    wanted = [eq] if isinstance(eq, str) else eq
    if not isinstance(wanted, list) or not wanted:
        raise ValueError(f"{where}: eq non valido")
    valid = set(mon.MAPS[BY_KEY[key].kind].values())
    for e in wanted:
        if e not in valid:
            raise ValueError(f"{where}: etichetta {e!r} non valida per {key}")


def check_keys(check: dict) -> list:
    """Chiavi del monitor usate da un check (singolo o composto), in ordine e senza doppioni."""
    subs = check.get("all") or [check]
    return list(dict.fromkeys(c["key"] for c in subs if c.get("key")))


def live_values(check: dict | None, labels: dict) -> list:
    """Valori reali letti per un check: [[nome, etichetta|"n/d"], ...] (sola lettura, vuoto se la voce non ha variabile)."""
    return [[BY_KEY[k].name, labels.get(k) or mon.NA] for k in check_keys(check or {})]


class Checklist:
    def __init__(self, data: dict, links: dict):
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
                    items[it["id"]] = it
        for iid, link in links.items():
            if iid not in items:
                raise ValueError(f"link su voce inesistente: {iid}")
            if items[iid]["type"] != "action":
                raise ValueError(f"{iid}: solo le voci action possono avere un check")
            if not isinstance(link.get("verified"), bool):
                raise ValueError(f"{iid}: 'verified' mancante")
            _check_keys(link.get("check") or {}, iid)
        self.ids = [p["id"] for p in self.phases]

    def has_phase(self, phase_id) -> bool:
        return isinstance(phase_id, str) and phase_id in self.ids

    def phase(self, phase_id: str) -> dict:
        return self.phases[self.ids.index(phase_id)]

    def evaluate_phase(self, phase_id: str, labels: dict, demo: bool = False) -> dict:
        """Sottofasi e voci della fase con status/detail; "bad" = numero di voci rosse. demo=True tratta i link come verificati."""
        ph, bad, subs = self.phase(phase_id), 0, []
        for sp in ph["subphases"]:
            rows = []
            for it in sp["items"]:
                link = self.links.get(it["id"])
                res = evaluate(link["check"], labels, demo or link["verified"]) if link else evaluate(None, labels)
                bad += res["status"] == "bad"
                row = {"id": it["id"], "text": it["text"], "response": it["response"], "type": it["type"],
                       "live": live_values(link["check"], labels) if link else [], "status": res["status"], "kind": res["kind"], "detail": res["detail"]}
                if it.get("note"):
                    row["note"] = it["note"]
                rows.append(row)
            subs.append({"id": sp["id"], "name": sp["name"], "items": rows})
        return {"phase": {"id": ph["id"], "name": ph["name"]}, "bad": bad, "subphases": subs}


def load(base: pathlib.Path = HERE) -> Checklist:
    data = json.loads((base / "checklist.json").read_text(encoding="utf-8"))
    links = json.loads((base / "checklist_links.json").read_text(encoding="utf-8"))
    return Checklist(data, links)
