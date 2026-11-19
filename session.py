"""Persistenza di sessione del volo: file ~/.virtual-captain-session.json (separato dalla configurazione).

Logica pura (costruzione, lettura tollerante, regola "stesso volo") + scrittura atomica. Nel dubbio NON si ripristina.
Non si salva mai: monitor, pills, DLL, raw, log, token, coordinate, contatori/timer di debounce, calibrazioni.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

SESSION = pathlib.Path.home() / ".virtual-captain-session.json"
VERSION = 1
DEBOUNCE_S = 0.5  # salvataggio a ogni cambio, raggruppato
HEARTBEAT_S = 60.0  # in volo il file si riscrive almeno ogni minuto (saved_at resta fresco anche senza cambi)
MAX_AGE_S = 6 * 3600  # file piu' vecchio: non e' piu' "lo stesso volo"
WRITE_RETRIES = 3  # os.replace puo' fallire per un attimo su Windows (antivirus/indicizzazione)
_COORD_KEYS = {"lat", "lon", "lng", "latitude", "longitude"}
_CLEAN_TYPES = (str, int, float, bool, type(None))


def _strip(v):
    """Copia JSON-safe senza coordinate (qualunque profondita'); tipi strani scartati."""
    if isinstance(v, dict):
        return {str(k): _strip(x) for k, x in v.items() if str(k).lower() not in _COORD_KEYS}
    if isinstance(v, (list, tuple)):
        return [_strip(x) for x in v]
    return v if isinstance(v, _CLEAN_TYPES) else None


def strip_plan(plan) -> dict:
    return _strip(plan) if isinstance(plan, dict) else {}


def write_atomic(path: pathlib.Path, data: dict) -> bool:
    """tmp + fsync + os.replace; 0600 su POSIX. False se non riesce (mai eccezioni: il volo non deve fermarsi)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            os.chmod(str(tmp), 0o600)
        for i in range(WRITE_RETRIES):
            try:
                os.replace(str(tmp), str(path))
                return True
            except PermissionError:
                if i == WRITE_RETRIES - 1:
                    raise
                time.sleep(0.05)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def delete(path: pathlib.Path) -> None:
    for p in (path, path.with_name(path.name + ".tmp")):
        try:
            p.unlink()
        except OSError:
            pass


def read(path: pathlib.Path) -> dict | None:
    """Dati se il file e' leggibile, e' un oggetto e ha versione supportata; altrimenti None (mai eccezioni)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or isinstance(data.get("v"), bool) or not isinstance(data.get("v"), int) or not 1 <= data["v"] <= VERSION:
        return None
    return data


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def same_flight(data: dict | None, now: float) -> bool:
    """Regola "stesso volo" lato file (il controllo sul sim e' a parte): file valido, fase valida e saved_at < 6 h (non nel futuro).
    Nessun limite su dep_time (e' solo "HH:MM" senza data: briefing fatto la sera prima o volo lungo vanno ripristinati); in volo
    saved_at resta fresco col battito."""
    if not isinstance(data, dict) or data.get("phase") not in ("briefing", "flying"):
        return False
    saved = _num(data.get("saved_at"))
    return saved is not None and 0 <= now - saved < MAX_AGE_S


def _on(v) -> bool | None:
    n = _num(v)
    return None if n is None else n >= 0.5


def stopped_on_ground(core, raw) -> bool:
    """Sim fermo a terra a motori spenti (on_ground, IAS < 1 kt, eng1/eng2 master OFF). Dato mancante -> False (nel dubbio no)."""
    core, raw = core or {}, raw or {}
    ias, e1, e2 = _num(core.get("ias")), _on(raw.get("eng1")), _on(raw.get("eng2"))
    return _on(core.get("on_ground")) is True and ias is not None and ias < 1 and e1 is False and e2 is False


def flight_over(core, raw) -> bool:
    """Fine volo: a terra, fermo, motori spenti e parking brake inserito (usato dalla consegna 2)."""
    return stopped_on_ground(core, raw) and _on((raw or {}).get("parking")) is True
