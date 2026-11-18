"""Controllo dinamico dell'Engine Anti-Ice (puro, nessun I/O, solo lettura): icing conditions dal METAR e regole per fase.

Visible moisture e soglie sono in monitor.py (AI_*), da verificare sul FCOM Fenix (fonti: PPRuNe, guide FlyByWire, flashcards).
Variabili del sim assenti = 0 (nessuna gestione degli n/d). Mai "no icing" senza dati: METAR assente -> nessun esito, "no METAR, check manually".
"""
from __future__ import annotations

import monitor as mon
from weather import vis_m

NO_METAR = "no METAR, check manually"
NOT_REQUIRED = "not required (OFF)"
RULES = ("ground", "takeoff", "climb", "descent")  # ground e descent sono ATTIVE (esito su eng1_ai/eng2_ai), takeoff e climb solo promemoria


def _num(v) -> float:
    """Valore dalle etichette del monitor (stringa numerica) -> float; assente o non numerico = 0."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def _tt(t: float) -> str:
    return f"{t:g}"


def moisture(metar: dict | None, in_flight: bool) -> list:
    """Motivi di visible moisture dal METAR (lista di testi, vuota se non ce ne sono). In volo contano anche BKN/OVC, a terra no."""
    m = metar or {}
    vis = vis_m(m.get("vis"))
    low_vis = vis is not None and vis <= mon.AI_VIS_MAX_M
    why = []
    for c in m.get("wx") or []:
        if c in mon.AI_WX_ALWAYS or (c in mon.AI_WX_VIS and low_vis):
            why.append(c)
    for cl in m.get("clouds") or []:
        cover, typ = cl.get("cover"), cl.get("type")
        if typ in mon.AI_CLOUD_TYPES:
            why.append(typ)
        elif cover in mon.AI_CLOUD_COVERS_ALWAYS or (in_flight and cover in mon.AI_CLOUD_COVERS_AIR):
            why.append(cover)
    if low_vis:
        why.append(f"vis {vis:.0f} m")
    return list(dict.fromkeys(why))


def icing_assessment(metar: dict | None, temp: float | None, in_flight: bool, icao: str = "") -> dict:
    """{"icing": True|False|None, "text": str}. metar: dati parsati (weather.metar_from_raw); temp: OAT a terra / TAT in volo (None = non nota).
    Il METAR puo' venire dal piano SimBrief (senza scadenza oltre `stale` di weather.py): il testo positivo mostra ICAO e dati usati.
    METAR assente o senza raw -> icing None; temp > AI_ICING_TEMP_MAX -> False "not required (OFF)"; temp <= soglia senza moisture -> False; con moisture -> True."""
    data = {k: v for k, v in (metar or {}).items() if k != "icao"}
    if not data.get("raw"):  # dati sufficienti = METAR grezzo presente; un dato parziale (senza raw) vale come METAR assente, mai "no visible moisture"
        return {"icing": None, "text": NO_METAR}
    if temp is None:
        return {"icing": None, "text": "no temperature, check manually"}
    if temp > mon.AI_ICING_TEMP_MAX:
        return {"icing": False, "text": NOT_REQUIRED}
    why = moisture(data, in_flight)
    if not why:
        return {"icing": False, "text": "no visible moisture: not required"}
    head = f"{icao}: " if icao else ""
    return {"icing": True, "text": f"{head}{', '.join(why)}, T {_tt(temp)} °C: icing conditions, ON"}


def ai_rule(rule: str, labels: dict, wx: dict | None) -> dict:
    """Esito di una regola anti-ice: {"on": bool, "text": str}. on=True = la voce e' ATTIVA e richiede eng1_ai e eng2_ai ON; altrimenti
    solo testo (nessun colore). labels: etichette del monitor (sat, tat, gnd); wx: {"dep": METAR, "dest": METAR} (con "icao" facoltativo)."""
    wx = wx or {}
    sat, tat = _num(labels.get("sat")), _num(labels.get("tat"))
    if rule == "ground":
        dep = wx.get("dep") or {}
        icao = dep.get("icao", "")
        temp = dep.get("temp")
        if temp is None and _num(labels.get("gnd")) >= 0.5 and sat != 0:
            temp = sat  # ripiego: SAT del sim a terra (se non e' 0, cioe' letta)
        r = icing_assessment(dep, temp, False, icao)
        return {"on": r["icing"] is True, "text": r["text"]}
    if rule == "takeoff":
        return {"on": False, "text": f"TAT {_tt(tat)} °C: ON if visible moisture" if tat <= mon.AI_ICING_TEMP_MAX else NOT_REQUIRED}
    if rule == "climb":
        if sat < mon.AI_CRZ_SAT_MIN:
            return {"on": False, "text": "may be OFF (ON only in Cb / ICE DETECTED)"}
        return {"on": False, "text": f"TAT {_tt(tat)} °C: ON if visible moisture" if tat <= mon.AI_ICING_TEMP_MAX else NOT_REQUIRED}
    if rule == "descent":
        if sat < mon.AI_DESC_SAT_MAX:
            return {"on": True, "text": f"SAT {_tt(sat)} °C below {mon.AI_DESC_SAT_MAX}: ON"}
        dest = wx.get("dest") or {}
        r = icing_assessment(dest, tat, True, dest.get("icao", ""))
        return {"on": r["icing"] is True, "text": r["text"]}
    raise ValueError(f"regola anti-ice sconosciuta: {rule!r}")
