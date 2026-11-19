"""Detent delle leve letto direttamente dalle LVar del Fenix (logica pura, nessun I/O).

Per ogni leva: A_FC_THROTTLE_*_INPUT (thr_in_*) vale 0..5 e A_FC_THROTTLE_*_GATE (thr_gate_*) vale 10..5, con
gate = 10 - input. Detent: REVERSE/REVERSE IDLE/IDLE/CLIMB/FLX/TOGA = 0/1/2/3/4/5. Nessuna calibrazione.
"""
from __future__ import annotations

import monitor as mon

DETENTS = ["REVERSE", "REVERSE IDLE", "IDLE", "CLIMB", "FLX", "TOGA"]  # il codice numerico e' l'indice (MAPS["detent"])
TOL = 1e-6  # tolleranza sull'uguaglianza tra input e 10 - gate e sull'interezza dell'input


def _num(x):
    v = mon.to_number(x)
    return v if v is not None and not isinstance(x, bool) else None


def decode_lever(inp, gate) -> int | None:
    """(input, gate) di una leva -> codice del detent 0..5; -1 (BETWEEN) se i dati non sono coerenti con un detent;
    None se non leggibile (gate assente o fuori 5..10, input assente)."""
    g, i = _num(gate), _num(inp)
    if g is None or i is None or g < 5 - TOL or g > 10 + TOL:
        return None
    if abs(i - (10 - g)) > TOL or abs(i - round(i)) > TOL or i < -TOL or i > 5 + TOL:
        return -1
    return int(round(i))


def annotate(raw: dict | None) -> dict:
    """Aggiunge ai grezzi i due valori virtuali thr_det_l / thr_det_r (None se non leggibili)."""
    out = {"thr_det_l": None, "thr_det_r": None}
    if raw is None:
        return out
    for side in ("l", "r"):
        out[f"thr_det_{side}"] = decode_lever(raw.get(f"thr_in_{side}"), raw.get(f"thr_gate_{side}"))
    return out
