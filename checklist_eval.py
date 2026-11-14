"""Confronto puro tra uno stato atteso della checklist e le etichette lette dal cockpit (sola lettura, nessun I/O).

check: {"key": k, "eq": "ON" | ["ON", "OFF"]}, {"all": [check, ...]}, {"detent": "IDLE" | ["FLX", "TOGA"]} (entrambe le leve nel detent, valori virtuali thr_det_l/thr_det_r; senza calibrazione "none") oppure {"show": [chiavi]} (solo mostrare: nessun esito).
Valore atteso da un riferimento chiuso (REFS): {"key": k, "eq_ref": "clr.t"} (kind con mappa di etichette ammessi solo se l'atteso normalizzato e' un'etichetta valida, altrimenti "none"; ammesso dentro "all", dove i riferimenti con atteso vuoto sono saltati)
e {"show_ref": "clr.a"} (solo atteso, nessun esito). expected: {riferimento: testo}; atteso vuoto => "none" (si mostra solo NOW).
optional (campo del link): se la voce non e' conforme l'esito e' "opt" (neutro, non conta come bad) invece di "bad".
labels: {chiave: etichetta pulita | None}.
Esito: {"status": "ok|bad|na|opt|none", "kind": "ok|mismatch|unverified|unreadable|none", "detail": str}. Priorita' nelle composte: bad > na > ok.
"""
from __future__ import annotations

import re

import monitor as mon
from monitor import LB_PER_KG

BY_KEY = {it.key: it for it in mon.ITEMS}
UNVERIFIED = "unverified"
UNREADABLE = "unreadable"
REFS = ("clr.c", "clr.r", "clr.a", "clr.f", "clr.t", "fld.runway", "fld.ta", "fld.v1", "fld.vr", "fld.v2", "fld.flap", "fld.fob", "fld.zfw", "fld.zfwcg", "plan.sid", "wx.qnh_dep", "wx.qnh_dest")
QNH_TOL = 1.0  # hPa


ALT_KEYS = ("fcu_alt",)  # voci in piedi: l'atteso puo' essere "5000", "7000 ft", "FL070", "FL 70"
_FL = re.compile(r"^FL\s*(\d{1,3})$")
_FT = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:FT|FEET)?$")


def altitude_ft(text) -> float | None:
    """Quota in piedi: FLnnn -> nnn*100 (solo FL esplicito), numero puro (con eventuale "ft") in piedi, altrimenti None."""
    s = str(text).strip().upper()
    m = _FL.match(s)
    if m:
        return float(int(m.group(1)) * 100)
    m = _FT.match(s)
    return float(m.group(1)) if m else None


def _norm(kind: str, text, key: str = "") -> str:
    """Confronto normalizzato: per i numerici 02000 == 2000 (e 2000.0), altrimenti testo senza spazi e maiuscolo.
    Per le quote (ALT_KEYS) tutto in piedi."""
    s = str(text).strip()
    if key in ALT_KEYS:
        ft = altitude_ft(s)
        if ft is not None:
            return f"{ft:g}"
    if kind == "numeric":
        try:
            v = float(s)
            return f"{v:g}"
        except ValueError:
            pass
    return s.upper()


_FLAP = re.compile(r"^(?:CONF(?:IG(?:URATION)?)?|FLAPS?)?\s*(\d)(?:\s*\+\s*F)?$")


def norm_label(kind: str, text) -> str | None:
    """Atteso -> etichetta valida della mappa del kind (es. flap: '1', 'CONF 1', 'flap 1', '1+F' -> '1'; '0' -> UP; '4' -> FULL), altrimenti None."""
    s = re.sub(r"\s+", " ", str(text).strip().upper())
    valid = set(mon.MAPS.get(kind, {}).values())
    if kind == "flap":
        m = _FLAP.match(s)
        if m:
            s = {"0": "UP", "4": "FULL"}.get(m.group(1), m.group(1))
    return s if s in valid else None


def _res(status: str, detail: str = "", kind: str = "") -> dict:
    """kind e' il motivo leggibile dal codice (la UI non dipende dal testo di detail)."""
    return {"status": status, "kind": kind or status, "detail": detail}


def ref_of(c: dict):
    """Riferimento dell'atteso di un check semplice: eq_ref (uguaglianza) o tol_ref (numerico con tolleranza %)."""
    return c.get("eq_ref") or c.get("tol_ref")


def _want(expected, ref) -> str:
    return str((expected or {}).get(ref) or "").strip()


def _active(sub: dict, expected) -> bool:
    """Riferimento eq_ref con un atteso utilizzabile (non vuoto e, per i kind a mappa, un'etichetta valida)."""
    want = _want(expected, ref_of(sub))
    item = BY_KEY.get(sub.get("key"))
    if want and item is not None and mon.MAPS[item.kind]:
        return norm_label(item.kind, want) is not None
    return bool(want)


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def qnh_actual(labels: dict):
    """"STD" se baro_std=1, altrimenti QNH impostato in hPa (ripiego: inHg convertito), altrimenti None."""
    std = _num(labels.get("baro_std"))
    if std is not None and std >= 0.5:
        return "STD"
    hpa = _num(labels.get("baro_hpa"))
    if hpa is not None and hpa > 0:
        return hpa
    inch = _num(labels.get("baro_inch"))
    if inch is not None and inch > 0:
        return inch * 33.8639
    return None


def _qnh_single(ref: str, labels: dict, expected) -> dict:
    text = _want(expected, ref)
    m = re.search(r"\d+(?:\.\d+)?", text)
    want = float(m.group(0))
    src = re.search(r"\((\w+)\)", text)  # fonte dell'atteso: "1013 hPa (ATIS)"
    sfx = f" ({src.group(1)})" if src else ""
    if _num(labels.get("baro_std")) is None:
        return _res("na", UNREADABLE, "unreadable")
    act = qnh_actual(labels)
    if act == "STD":
        return _res("bad", f"expected {want:g}{sfx}, actual STD", "mismatch")
    if act is None:
        return _res("na", UNREADABLE, "unreadable")
    if abs(act - want) <= QNH_TOL + 1e-6:
        return _res("ok")
    return _res("bad", f"expected {want:g}{sfx}, actual {act:.0f}", "mismatch")


def detent_all(check: dict) -> dict:
    """{"detent": d} -> {"all": [thr_det_l eq d, thr_det_r eq d]}."""
    return {"all": [{"key": "thr_det_l", "eq": check["detent"]}, {"key": "thr_det_r", "eq": check["detent"]}]}


def _refs(check: dict) -> list:
    """Riferimenti eq_ref di un check (singolo o composto); {"qnh": ref} -> [ref]."""
    if "qnh" in check:
        return [check["qnh"]]
    return [ref_of(c) for c in (check.get("all") or [check]) if ref_of(c)]


def _eq_list(eq) -> list:
    return [eq] if isinstance(eq, str) else list(eq)


def _tolerance(item, check: dict, cur, labels: dict, expected) -> dict:
    """Numerico con tolleranza percentuale sull'atteso (campo fob). Se il piano e il Fenix hanno unita' diverse (kgs/lbs) converte l'atteso
    nell'unita' del Fenix (B_CONFIG_WEIGHT_LBS: 1 = lbs, 0 = kg); unita' del Fenix non leggibile ma servirebbe la conversione -> non leggibile."""
    want, act = _num(_want(expected, check["tol_ref"])), _num(cur)
    if want is None:
        return _res("none")
    if act is None:
        return _res("na", UNREADABLE, "unreadable")
    plan_u = _want(expected, "fld.fob_unit")
    cfg = _num(labels.get("cfg_lbs"))
    if plan_u in ("kgs", "lbs"):
        if cfg is None:
            return _res("na", UNREADABLE, "unreadable")
        cock_u = "lbs" if cfg >= 0.5 else "kgs"
        if plan_u != cock_u:
            want = want * LB_PER_KG if cock_u == "lbs" else want / LB_PER_KG
    tol = float(check["tol_pct"])
    if want == 0 or abs(act - want) <= abs(want) * tol / 100 + 1e-9:
        return _res("ok")
    return _res("bad", f"expected {want:.0f} (+/-{tol:g}%), actual {act:g}", "mismatch")


def _single(check: dict, labels: dict, expected: dict | None = None) -> dict:
    item = BY_KEY.get(check.get("key"))
    if item is None:
        return _res("na", UNREADABLE, "unreadable")
    if item.uncertain:  # sorgente da provare dal vivo: mai un verde o un rosso
        return _res("na", UNVERIFIED, "unverified")
    cur = labels.get(item.key)
    if "tol_ref" in check:
        return _tolerance(item, check, cur, labels, expected)
    if "eq_ref" in check:
        want = str((expected or {}).get(check["eq_ref"]) or "").strip()
        if cur is None:
            return _res("na", UNREADABLE, "unreadable")
        if mon.MAPS[item.kind]:
            want = norm_label(item.kind, want) or want
        if _norm(item.kind, cur, item.key) == _norm(item.kind, want, item.key):
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
    if "detent" in check:
        if not _want(expected, "cal.thrust"):
            return _res("none")  # non calibrato: si mostra solo NOW
    elif "qnh" in check:
        if not _want(expected, check["qnh"]):
            return _res("none")
    elif _refs(check) and not any(_active(c, expected) for c in (check.get("all") or [check]) if ref_of(c)):
        return _res("none")  # niente atteso: si mostra solo NOW
    res = _evaluate(check, labels, verified, expected)
    if optional and res["status"] == "bad":
        return _res("opt", res["detail"], "optional")
    return res


def _evaluate(check: dict, labels: dict, verified: bool, expected: dict | None = None) -> dict:
    if not verified:
        return _res("na", UNVERIFIED, "unverified")
    if "detent" in check:
        check = detent_all(check)
    if "std" in check:
        if _num(labels.get("baro_std")) is None:
            return _res("na", UNREADABLE, "unreadable")
        act = qnh_actual(labels)
        return _res("ok") if act == "STD" else _res("bad", "expected STD, actual " + ("n/d" if act is None else f"{act:.0f}"), "mismatch")
    if "qnh" in check:
        return _qnh_single(check["qnh"], labels, expected)
    if "all" not in check:
        return _single(check, labels, expected)
    subs = [sub for sub in check["all"] if "eq_ref" not in sub or _active(sub, expected)]  # atteso vuoto: voce saltata
    parts = [(sub, _single(sub, labels, expected)) for sub in subs]
    bad = [f"{BY_KEY[s['key']].name}: {r['detail']}" for s, r in parts if r["status"] == "bad"]
    if bad:
        return _res("bad", "; ".join(bad), "mismatch")
    na = [r["kind"] for _, r in parts if r["status"] == "na"]
    if na:
        return _res("na", UNVERIFIED if "unverified" in na else UNREADABLE, "unverified" if "unverified" in na else "unreadable")
    return _res("ok")
