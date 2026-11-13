"""Calibrazione dei detent delle leve (logica pura, nessun I/O).

Le due leve si muovono insieme a ogni passo del wizard; si registrano tutte le variabili. Per ogni leva la
"guida" e' la prima variabile candidata con i 6 valori strettamente monotoni (in un verso o nell'altro) e
distanziati almeno del 2% dello span. Un detent e' valido solo se entrambe le leve sono nel proprio detent.

Config: {"at": epoch, "levers": {"l": {"var": chiave, "v": {detent: valore}}, "r": {...}}, "raw": {detent: {chiave: valore}}}.
"""
from __future__ import annotations

import monitor as mon

DETENTS = ["REVERSE", "REVERSE IDLE", "IDLE", "CLIMB", "FLX", "TOGA"]  # in ordine di passo; il codice numerico e' l'indice (MAPS["detent"])
STEP_NAMES = ["FULL REVERSE", "REVERSE IDLE", "IDLE", "CLIMB", "FLEX/MCT", "TOGA"]
STEP_SHORT = ["REV", "R.IDLE", "IDLE", "CLB", "FLX", "TOGA"]
STEPS = [{"detent": d, "name": n, "text": f"Move both thrust levers to {n}, then press Next"} for d, n in zip(DETENTS, STEP_NAMES)]
CANDIDATES = {"l": ["thr_in_l", "thr_pos_1", "thr_gate_l"], "r": ["thr_in_r", "thr_pos_2", "thr_gate_r"]}
RECORDED = ["thr_in_l", "thr_in_r", "thr_pos_1", "thr_pos_2", "thr_pos_3", "thr_pos_4",
            "thr_gate_l", "thr_gate_r", "thr_rev_l", "thr_rev_r"]  # tutte le variabili registrate a ogni passo
MIN_GAP_FRAC = 0.02  # gap minimo tra detent vicini, in frazione dello span
BAND_FRAC = 0.4  # un detent e' "preso" se |v - v_i| <= BAND_FRAC * min(gap adiacenti)


def _num(x):
    v = mon.to_number(x)
    return v if v is not None and not isinstance(x, bool) else None


def _series(values) -> list | None:
    out = [_num(v) for v in values]
    return None if any(v is None for v in out) else out


def monotone(vs: list) -> bool:
    """Valori strettamente monotoni (crescenti o decrescenti) con gap minimo >= MIN_GAP_FRAC dello span."""
    if len(vs) < 2:
        return False
    gaps = [b - a for a, b in zip(vs, vs[1:])]
    if not (all(g > 0 for g in gaps) or all(g < 0 for g in gaps)):
        return False
    span = abs(vs[-1] - vs[0])
    return min(abs(g) for g in gaps) >= MIN_GAP_FRAC * span - 1e-12


def choose_guide(side: str, raw: dict) -> str | None:
    """Prima variabile candidata della leva con 6 valori monotoni validi, altrimenti None. raw = {detent: {chiave: valore}}."""
    for key in CANDIDATES[side]:
        vs = _series([(raw.get(d) or {}).get(key) for d in DETENTS])
        if vs is not None and monotone(vs):
            return key
    return None


def build_cal(raw: dict, at: float) -> tuple:
    """-> (cal, "") oppure (None, messaggio d'errore con i valori letti). Nulla viene scritto qui."""
    levers, errs = {}, []
    for side in ("l", "r"):
        g = choose_guide(side, raw)
        if g is None:
            vals = "; ".join(f"{k}=" + ",".join(mon.raw_text((raw.get(d) or {}).get(k)) or "n/d" for d in DETENTS) for k in CANDIDATES[side])
            errs.append(f"no usable guide variable for lever {side.upper()} ({vals})")
        else:
            levers[side] = {"var": g, "v": {d: float(_num(raw[d][g])) for d in DETENTS}}
    if errs:
        return None, "Calibration failed: " + " / ".join(errs)
    return {"at": float(at), "levers": levers, "raw": {d: dict(raw.get(d) or {}) for d in DETENTS}}, ""


def valid_cal(cal) -> bool:
    """Struttura e valori di una calibrazione (anche letta dal file di configurazione)."""
    try:
        if not isinstance(cal, dict) or _num(cal.get("at")) is None or not isinstance(cal.get("raw"), dict):
            return False
        for side in ("l", "r"):
            lv = cal["levers"][side]
            if lv["var"] not in CANDIDATES[side] or not isinstance(lv["v"], dict):
                return False
            vs = _series([lv["v"].get(d) for d in DETENTS])
            if vs is None or not monotone(vs):
                return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def decode(v: dict, value) -> int | None:
    """Valore letto della variabile guida -> codice del detent (0..5), -1 se fuori banda (BETWEEN), None se assente."""
    x = _num(value)
    if x is None:
        return None
    vs = [float(v[d]) for d in DETENTS]
    gaps = [abs(b - a) for a, b in zip(vs, vs[1:])]
    for i, vi in enumerate(vs):
        adj = [gaps[j] for j in (i - 1, i) if 0 <= j < len(gaps)]
        if abs(x - vi) <= BAND_FRAC * min(adj) + 1e-12:
            return i
    return -1


def annotate(raw: dict | None, cal) -> dict:
    """Aggiunge ai grezzi i due valori virtuali thr_det_l / thr_det_r (None se non calibrato o dato assente)."""
    out = {"thr_det_l": None, "thr_det_r": None}
    if raw is None or not valid_cal(cal):
        return out
    for side in ("l", "r"):
        lv = cal["levers"][side]
        out[f"thr_det_{side}"] = decode(lv["v"], raw.get(lv["var"]))
    return out


def median(xs: list) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

