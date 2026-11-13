"""Monitor del cockpit (sola lettura): tabella delle voci e traduzione valore grezzo -> etichetta. Puro, nessun I/O.

Significati dei valori Fenix A320 dai file di script del repo pubblico del 2022 (da riverificare dal vivo
con il valore grezzo mostrato accanto). Le sorgenti marcate `uncertain` sono nomi da provare.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

NA = "n/d"

MAPS = {
    "onoff": {0: "OFF", 1: "ON"},
    "landing": {0: "RETRACT", 1: "OFF", 2: "ON"},
    "nose": {0: "OFF", 1: "TAXI", 2: "T/O"},
    "strobe": {0: "OFF", 1: "AUTO", 2: "ON"},
    "navlogo": {0: "OFF", 1: "NAV", 2: "LOGO"},
    "flap": {0: "UP", 1: "1", 2: "2", 3: "3", 4: "FULL"},
    "speedbrake": {0: "ARM", 1: "RETRACT", 2: "HALF", 3: "FULL"},
    "engmode": {0: "CRANK", 1: "NORM", 2: "START"},
    "gear": {0: "UP", 1: "DOWN"},
    "wxsys": {0: "SYS 1", 1: "OFF", 2: "SYS 2"},  # verificata dal vivo 13/11
    "irmode": {0: "OFF", 1: "NAV", 2: "ATT"},  # provvisoria: da verificare dal vivo col grezzo
    "signs": {0: "OFF", 1: "AUTO", 2: "ON"},  # provvisoria: da verificare dal vivo col grezzo
    "emerlt": {0: "OFF", 1: "ARM", 2: "ON"},  # provvisoria: da verificare dal vivo col grezzo
    "numeric": {},  # nessuna mappa: si mostra solo il valore (nessun esito)
}
AMBIGUOUS = {"landing", "nose", "flap", "speedbrake", "engmode", "wxsys", "irmode", "signs", "emerlt"}  # lo 0 non e' "spento": mostra anche il grezzo


@dataclass(frozen=True)
class Item:
    key: str
    group: str
    name: str
    kind: str
    expected: dict = field(default_factory=dict)  # fase -> etichetta attesa (solo informativa)
    std: str | None = None  # sorgente standard (A:...)
    lvar: str | None = None  # L-var Fenix (senza "(L:" e ", Number)")
    std_fallback: bool = False  # se manca la L-var, vale la standard (stessa mappa)
    uncertain: bool = False  # nome da provare dal vivo


ITEMS: list[Item] = [
    Item("apu_master", "APU / Air", "APU master", "onoff", {"Prep": "ON", "After start": "OFF"},
         "(A:APU SWITCH, Bool)", "S_OH_ELEC_APU_MASTER", True),
    Item("apu_bleed", "APU / Air", "APU bleed", "onoff", {"Prep": "ON", "After start": "OFF"},
         "(A:BLEED AIR APU, Bool)", "S_OH_PNEUMATIC_APU_BLEED", False),
    Item("pack1", "APU / Air", "Pack 1", "onoff", {"Before start": "OFF", "After start": "ON", "Before T/O": "OFF", "Climb": "ON"},
         None, "S_OH_PNEUMATIC_PACK_1"),
    Item("pack2", "APU / Air", "Pack 2", "onoff", {"Before start": "OFF", "After start": "ON", "Before T/O": "OFF", "Climb": "ON"},
         None, "S_OH_PNEUMATIC_PACK_2"),
    Item("landing_l", "Lights", "Landing L", "landing", {"Before T/O": "ON", "10000 ft": "OFF"},
         "(A:LIGHT LANDING, Bool)", "S_OH_EXT_LT_LANDING_L"),
    Item("landing_r", "Lights", "Landing R", "landing", {"Before T/O": "ON", "10000 ft": "OFF"},
         "(A:LIGHT LANDING, Bool)", "S_OH_EXT_LT_LANDING_R"),
    Item("nose", "Lights", "Nose", "nose", {"Taxi": "TAXI", "Before T/O": "T/O", "Climb": "OFF"},
         None, "S_OH_EXT_LT_NOSE"),
    Item("strobe", "Lights", "Strobe", "strobe", {"Prep": "AUTO", "Before T/O": "ON"},
         "(A:LIGHT STROBE, Bool)", "S_OH_EXT_LT_STROBE"),
    Item("beacon", "Lights", "Beacon", "onoff", {"Before start": "ON"},
         "(A:LIGHT BEACON, Bool)", "S_OH_EXT_LT_BEACON", True),
    Item("navlogo", "Lights", "Nav & logo", "navlogo", {"Prep": "NAV/LOGO"},
         "(A:LIGHT NAV, Bool)", "S_OH_EXT_LT_NAV_LOGO"),
    Item("rwy_turnoff", "Lights", "Rwy turn-off", "onoff", {"Before T/O": "ON", "Climb": "OFF"},
         None, "S_OH_EXT_LT_RWY_TURNOFF"),
    Item("seatbelt", "Cabin", "Seatbelt", "onoff", {"Prep": "ON"},
         "(A:CABIN SEATBELTS ALERT SWITCH, Bool)", "S_OH_SIGNS", True),
    Item("flap", "Flight controls", "Flaps", "flap", {"After start": "set for T/O"},
         "(A:FLAPS HANDLE INDEX, Number)", "S_FC_FLAPS"),
    Item("spoilers", "Flight controls", "Ground spoilers", "speedbrake", {"After start": "ARM", "Climb": "RETRACT"},
         "(A:SPOILERS ARMED, Bool)", "A_FC_SPEEDBRAKE"),
    Item("gear", "Flight controls", "Gear", "gear", {"Takeoff": "DOWN", "Positive climb": "UP"},
         "(A:GEAR HANDLE POSITION, Bool)", "S_MIP_GEAR", True),  # mappa 0/1 verificata dal vivo
    Item("engmode", "Engines", "Engine mode", "engmode", {"Start": "START", "After start": "NORM"},
         None, "S_ENG_MODE"),
    Item("eng1", "Engines", "Engine master 1", "onoff", {"Start": "ON"}, None, "S_ENG_MASTER_1"),
    Item("eng2", "Engines", "Engine master 2", "onoff", {"Start": "ON"}, None, "S_ENG_MASTER_2"),
    Item("parking", "Brakes", "Parking brake", "onoff", {"Before start": "OFF", "Taxi": "OFF"},
         "(A:BRAKE PARKING POSITION, Bool)", "S_MIP_PARKING_BRAKE", True),
    # Preliminary cockpit prep (valori ipotizzati: da verificare col grezzo di --test-links)
    Item("wx_sys", "Weather radar", "WX Radar sw", "wxsys", {"Prep": "OFF"}, None, "S_WR_SYS"),
    Item("wx_pws", "Weather radar", "Windshear PWS sw", "onoff", {"Prep": "OFF"}, None, "S_WR_PRED_WS"),
    Item("wx_gain", "Weather radar", "WX Gain knob", "numeric", {"Prep": "AUTO/CAL"}, None, "A_WR_GAIN"),
    Item("wx_mode", "Weather radar", "WX Mode", "numeric", {"Prep": "as required"}, None, "S_WR_MODE"),
    Item("squawk", "Transponder", "Squawk code", "numeric", {"Before start": "as cleared"}, None, "N_FREQ_XPDR_SELECTED", uncertain=True),  # nome da provare dal vivo
    Item("fcu_alt", "FCU", "FCU altitude", "numeric", {"Climb": "as cleared"}, None, "N_FCU_ALTITUDE"),  # piedi (da provare dal vivo col grezzo)
    Item("bat1", "Electrical", "Battery 1", "onoff", {"Prep": "ON"}, None, "S_OH_ELEC_BAT1"),
    Item("bat2", "Electrical", "Battery 2", "onoff", {"Prep": "ON"}, None, "S_OH_ELEC_BAT2"),
    Item("ext_pwr", "Electrical", "External power", "onoff", {"Prep": "ON if avail"}, None, "S_OH_ELEC_EXT_PWR"),
    Item("crew_oxy", "Electrical", "Crew supply", "onoff", {"Prep": "ON"}, None, "S_OH_OXYGEN_CREW_OXYGEN"),
    Item("ir1", "ADIRS", "IR 1 mode", "irmode", {"Prep": "NAV"}, None, "S_OH_NAV_IR1_MODE"),
    Item("ir2", "ADIRS", "IR 2 mode", "irmode", {"Prep": "NAV"}, None, "S_OH_NAV_IR2_MODE"),
    Item("ir3", "ADIRS", "IR 3 mode", "irmode", {"Prep": "NAV"}, None, "S_OH_NAV_IR3_MODE"),
    Item("smoking", "Cabin", "No Smoking/Elec.dev. Signs", "signs", {"Prep": "AUTO", "Securing": "OFF"}, None, "S_OH_SIGNS_SMOKING"),
    Item("emer_lt", "Cabin", "Emergency Exit Lights", "emerlt", {"Prep": "ARM", "Securing": "OFF"}, None, "S_OH_INT_LT_EMER"),
    Item("fuel_l1", "Fuel", "Fuel pump L1", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_LEFT_1"),
    Item("fuel_l2", "Fuel", "Fuel pump L2", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_LEFT_2"),
    Item("fuel_c1", "Fuel", "Fuel pump C1", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_CENTER_1"),
    Item("fuel_c2", "Fuel", "Fuel pump C2", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_CENTER_2"),
    Item("fuel_r1", "Fuel", "Fuel pump R1", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_RIGHT_1"),
    Item("fuel_r2", "Fuel", "Fuel pump R2", "onoff", {"Prep": "ON", "Parking": "OFF"}, None, "S_OH_FUEL_RIGHT_2"),
]


def to_number(raw):
    if raw is None or isinstance(raw, bool):
        return None if raw is None else float(raw)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def raw_text(raw) -> str:
    v = to_number(raw)
    if v is None:
        return ""
    return str(int(v)) if v == int(v) else f"{v:g}"


def label(item: Item, raw) -> str:
    """Etichetta del valore, oppure "n/d" se assente, non numerico o fuori mappa. Per i valori ambigui aggiunge il grezzo."""
    v = to_number(raw)
    if v is None:
        return NA
    if item.kind == "numeric":
        return raw_text(v)
    text = MAPS[item.kind].get(int(v)) if v == int(v) else None
    if text is None:
        return f"{NA} ({raw_text(v)})"  # valore inatteso: "n/d" ma con il grezzo, distinto da OFF
    return f"{text} ({int(v)})" if item.kind in AMBIGUOUS else text


def clean_label(item: Item, raw) -> str | None:
    """Etichetta pulita (senza il grezzo) per il confronto della checklist, oppure None se assente o fuori mappa."""
    v = to_number(raw)
    if v is not None and item.kind == "numeric":
        return raw_text(v)  # solo da mostrare, mai confrontato
    if v is None or v != int(v):
        return None
    return MAPS[item.kind].get(int(v))


def clean_labels(monitor: dict) -> dict:
    """Dal dizionario {chiave: grezzo|None} a {chiave: etichetta pulita|None}."""
    return {it.key: clean_label(it, monitor.get(it.key)) for it in ITEMS}


def expected_text(item: Item) -> str:
    return " · ".join(f"{p}: {v}" for p, v in item.expected.items())


def lvar_request(item: Item) -> str | None:
    return f"(L:{item.lvar}, Number)" if item.lvar else None


def std_request(item: Item) -> str | None:
    """La standard si chiede solo se serve: ripiego esplicito o voce senza L-var."""
    return item.std if item.std and (item.std_fallback or not item.lvar) else None


def build(monitor: dict) -> list:
    """Dal dizionario {chiave: grezzo|None} ai gruppi [[gruppo, [[nome, etichetta, atteso, grezzo], ...]], ...]."""
    groups: dict = {}
    for it in ITEMS:
        raw = monitor.get(it.key)
        groups.setdefault(it.group, []).append([it.name, label(it, raw), expected_text(it), raw_text(raw)])
    return [[g, rows] for g, rows in groups.items()]
