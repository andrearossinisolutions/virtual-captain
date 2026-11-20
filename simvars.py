"""Variabili lette dal simulatore via SimConnect (solo lettura): traduzione delle richieste e decodifica. Puro, nessun I/O.

Le richieste del monitor restano scritte come "(A:NOME, Unità)" / "(L:NOME, Number)" (vedi monitor.py) e qui diventano
(nome, unità) per SimConnect. Una variabile assente o sconosciuta vale 0 (nessuna gestione degli n/d).
Compatibilità voluta: solo Fenix A320, niente ripiego sulle variabili standard.
Eccezione: le L-var GSX (FSDT_GSX_*, gruppo "GSX") valgono None finche' non arriva un dato (0 non e' uno stato valido) e stanno in blocchi
a parte, rinviabili: GSX/Couatl puo' partire dopo Virtual Captain (vedi simconnect_backend: riregistrazione tardiva).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from monitor import GSX_GROUP, ITEMS, lvar_request

MAX_PER_DEF = 48  # voci per definizione SimConnect (limite mai provato: da verificare sul Shadow)

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

_REQ = re.compile(r"^\((A|L):([^,]+),\s*([^)]+)\)$")


def parse_request(text: str) -> tuple[str, str]:
    """"(A:NOME, Unità)" -> ("NOME", "unità"); "(L:NOME, Number)" -> ("L:NOME", "number")."""
    m = _REQ.match(text.strip())
    if not m:
        raise ValueError(f"richiesta non valida: {text!r}")
    kind, name, unit = m.group(1), m.group(2).strip(), m.group(3).strip().lower()
    return (f"L:{name}" if kind == "L" else name), unit


def monitor_source(item) -> str | None:
    """Richiesta del monitor per una voce: la L-var Fenix, altrimenti la standard (nessun ripiego tra le due)."""
    return lvar_request(item) or item.std


MONITOR = {i.key: monitor_source(i) for i in ITEMS}  # chiave -> richiesta o None (voce virtuale, calcolata altrove)
GSX_KEYS = frozenset(i.key for i in ITEMS if i.group == GSX_GROUP and i.lvar)  # L-var GSX: None finche' non arriva un dato


@dataclass(frozen=True)
class Block:
    """Una definizione SimConnect: id, destinazione ("core" o "monitor"), chiavi e richieste (nome, unità) nello stesso ordine."""
    ident: int
    target: str
    keys: tuple
    reqs: tuple
    retry: bool = False  # blocco GSX: se non risponde (tutto 0) si riregistra periodicamente (backend)


def build_blocks() -> list:
    """Core (8 A:) separato dal monitor. Il monitor e' diviso per tipo (A: / L:) e in blocchi da MAX_PER_DEF, cosi'
    una variabile A: errata (eccezione, nessun dato per il blocco) non rompe le L-var."""
    blocks = [Block(1, "core", tuple(VARS), tuple(parse_request(v) for v in VARS.values()))]
    wanted = [(k, parse_request(r)) for k, r in MONITOR.items() if r]
    for lvars, gsx in ((False, False), (True, False), (True, True)):  # A:, poi L-var Fenix, poi L-var GSX (blocco a parte)
        part = [(k, nu) for k, nu in wanted if nu[0].startswith("L:") == lvars and (k in GSX_KEYS) == gsx]
        for i in range(0, len(part), MAX_PER_DEF):
            chunk = part[i:i + MAX_PER_DEF]
            blocks.append(Block(len(blocks) + 1, "monitor", tuple(k for k, _ in chunk), tuple(nu for _, nu in chunk), gsx))
    return blocks


def default_core() -> dict:
    return {k: 0.0 for k in VARS}


def default_monitor() -> dict:
    """0 per ogni voce con sorgente (anche se non arriva mai il dato), None per le virtuali e per le L-var GSX (non lette)."""
    return {k: (0.0 if r and k not in GSX_KEYS else None) for k, r in MONITOR.items()}


def number(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) or math.isinf(f) else f


def decode(block: Block, values) -> dict:
    """Valori grezzi di un blocco -> {chiave: float}; vuoto se il numero di valori non corrisponde."""
    if len(values) != len(block.keys):
        return {}
    return {k: number(v) for k, v in zip(block.keys, values)}
