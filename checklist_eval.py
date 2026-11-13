"""Confronto puro tra uno stato atteso della checklist e le etichette lette dal cockpit (sola lettura, nessun I/O).

check: {"key": k, "eq": "ON" | ["ON", "OFF"]}, {"all": [check, ...]} oppure {"show": [chiavi]} (solo mostrare: nessun esito).
Valore atteso da un riferimento chiuso (REFS): {"key": k, "eq_ref": "clr.t"} (solo kind senza mappa di etichette, mai dentro "all")
e {"show_ref": "clr.a"} (solo atteso, nessun esito). expected: {riferimento: testo}; atteso vuoto => "none" (si mostra solo NOW).
optional (campo del link): se la voce non e' conforme l'esito e' "opt" (neutro, non conta come bad) invece di "bad".
labels: {chiave: etichetta pulita | None}.
Esito: {"status": "ok|bad|na|opt|none", "kind": "ok|mismatch|unverified|unreadable|none", "detail": str}. Priorita' nelle composte: bad > na > ok.
"""
from __future__ import annotations

import monitor as mon

BY_KEY = {it.key: it for it in mon.ITEMS}
UNVERIFIED = "unverified"
UNREADABLE = "unreadable"
REFS = ("clr.c", "clr.r", "clr.a", "clr.f", "clr.t", "fld.runway", "fld.ta", "plan.sid")


def _norm(kind: str, text) -> str:
    """Confronto normalizzato: per i numerici 02000 == 2000 (e 2000.0), altrimenti testo senza spazi e maiuscolo."""
    s = str(text).strip()
    if kind == "numeric":
        try:
            v = float(s)
            return f"{v:g}"
        except ValueError:
            pass
    return s.upper()


def _res(status: str, detail: str = "", kind: str = "") -> dict:
    """kind e' il motivo leggibile dal codice (la UI non dipende dal testo di detail)."""
    return {"status": status, "kind": kind or status, "detail": detail}


def _eq_list(eq) -> list:
    return [eq] if isinstance(eq, str) else list(eq)


def _single(check: dict, labels: dict, expected: dict | None = None) -> dict:
    item = BY_KEY.get(check.get("key"))
    if item is None:
        return _res("na", UNREADABLE, "unreadable")
    if item.uncertain:  # sorgente da provare dal vivo: mai un verde o un rosso
        return _res("na", UNVERIFIED, "unverified")
    cur = labels.get(item.key)
    if "eq_ref" in check:
        want = str((expected or {}).get(check["eq_ref"]) or "").strip()
        if cur is None:
            return _res("na", UNREADABLE, "unreadable")
        if _norm(item.kind, cur) == _norm(item.kind, want):
            return _res("ok")
        return _res("bad", f"expected {want}, actual {cur}", "mismatch")
    if cur is None or cur not in mon.MAPS[item.kind].values():
        return _res("na", UNREADABLE, "unreadable")
    want = _eq_list(check.get("eq"))
    if cur in want:
        return _res("ok")
    return _res("bad", f"expected {' / '.join(want)}, actual {cur}", "mismatch")


def evaluate(check: dict | None, labels: dict, verified: bool = True, optional: bool = False, expected: dict | None = None) -> dict:
    """Valuta un collegamento. verified=False (link non ancora verificato) => sempre "na" (se c'e' un check).
    {"show": [...]} => sempre "none" (si mostra solo il valore). optional=True: il "bad" diventa "opt"."""
    if not check or "show" in check or "show_ref" in check:
        return _res("none")
    if "eq_ref" in check and not str((expected or {}).get(check["eq_ref"]) or "").strip():
        return _res("none")  # niente atteso: si mostra solo NOW
    res = _evaluate(check, labels, verified, expected)
    if optional and res["status"] == "bad":
        return _res("opt", res["detail"], "optional")
    return res


def _evaluate(check: dict, labels: dict, verified: bool, expected: dict | None = None) -> dict:
    if not verified:
        return _res("na", UNVERIFIED, "unverified")
    if "all" not in check:
        return _single(check, labels, expected)
    parts = [(sub, _single(sub, labels)) for sub in check["all"]]
    bad = [f"{BY_KEY[s['key']].name}: {r['detail']}" for s, r in parts if r["status"] == "bad"]
    if bad:
        return _res("bad", "; ".join(bad), "mismatch")
    na = [r["kind"] for _, r in parts if r["status"] == "na"]
    if na:
        return _res("na", UNVERIFIED if "unverified" in na else UNREADABLE, "unverified" if "unverified" in na else "unreadable")
    return _res("ok")
