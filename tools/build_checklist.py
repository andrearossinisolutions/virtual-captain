"""Genera checklist.json dalla sorgente testuale della checklist. Uso: python3 tools/build_checklist.py <sorgente.txt> [uscita.json]

ATTENZIONE: dalla fase Departure Clearance la fonte di verita' e' `checklist.json` (si modifica a mano). Questo script e' solo lo
strumento della prima generazione: non conosce le tre fasi FMGS/Departure Clearance (id rinominati) ne' il tipo voce `craft`, quindi
rigenerare il file dalla vecchia sorgente lo riporterebbe indietro. Non usarlo per sovrascrivere `checklist.json`.

Formato della sorgente (descritto anche in testa al file):
  - le righe fino alla prima riga vuota sono un'intestazione di commento e si ignorano;
  - "# FASE" apre una fase, "## SOTTO-FASE" una sotto-fase (le voci prima del primo ## vanno in una sotto-fase senza nome);
  - "Elemento | RISPOSTA" e' una voce (risposta ANNOUNCE => type announce);
  - "ATC: <testo> | <esempio>" e' una voce type atc (text senza prefisso, response = esempio);
  - "NOTE: <testo>" diventa una voce type note (non valutabile), senza risposta.
Id stabili: <slug fase>.<slug elemento>; i duplicati nella stessa fase hanno suffisso _2, _3 (il primo resta senza).
Le sotto-fasi hanno id <id fase>.<slug nome> ("main" se senza nome).
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

SOURCE_NAME = "Google Doc A320 Checklist & Comm"
OUT = pathlib.Path(__file__).resolve().parent.parent / "checklist.json"


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-")
    return s or "x"


def _unique(base: str, seen: dict) -> str:
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}_{seen[base]}"


def parse(text: str) -> dict:
    lines = text.splitlines()
    if "" in [l.strip() for l in lines]:  # scarta l'intestazione di commento fino alla prima riga vuota
        lines = lines[[l.strip() for l in lines].index("") + 1:]
    phases: list = []
    phase = sub = None
    seen_phase: dict = {}
    seen_sub: dict = {}
    seen_item: dict = {}

    def new_sub(name: str):
        nonlocal sub
        sub = {"id": f"{phase['id']}.{_unique(slug(name) if name else 'main', seen_sub)}", "name": name, "items": []}
        phase["subphases"].append(sub)

    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            if phase is None:
                raise ValueError(f"riga {n}: sotto-fase fuori da una fase")
            new_sub(line[3:].strip())
        elif line.startswith("# "):
            name = line[2:].strip()
            phase = {"id": _unique(slug(name), seen_phase), "name": name, "subphases": []}
            phases.append(phase)
            seen_sub, seen_item, sub = {}, {}, None
        else:
            if phase is None:
                raise ValueError(f"riga {n}: voce fuori da una fase")
            if sub is None:
                new_sub("")
            if line.startswith("NOTE:"):
                body, resp, kind = line[5:].strip(), "", "note"
                base = "note"
            else:
                if " | " not in line:
                    raise ValueError(f"riga {n}: manca ' | ' nella voce: {line[:40]}")
                body, resp = (p.strip() for p in line.split(" | ", 1))
                kind = "action"
                if body.startswith("ATC:"):
                    kind, body = "atc", body[4:].strip()
                elif resp.upper() == "ANNOUNCE":
                    kind = "announce"
                base = slug(body)
            sub["items"].append({"id": f"{phase['id']}.{_unique(base, seen_item)}", "text": body, "response": resp, "type": kind})
    return {"version": 1, "source": SOURCE_NAME, "phases": phases}


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    data = parse(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    out = pathlib.Path(argv[2]) if len(argv) > 2 else OUT
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n = sum(len(s["items"]) for p in data["phases"] for s in p["subphases"])
    print(f"{len(data['phases'])} fasi, {n} voci -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
