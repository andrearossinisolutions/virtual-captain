"""Monitor del cockpit (sola lettura): tabella delle voci e traduzione valore grezzo -> etichetta. Puro, nessun I/O.

Significati dei valori Fenix A320 dai file di script del repo pubblico del 2022 (da riverificare dal vivo
con il valore grezzo mostrato accanto). Le sorgenti marcate `uncertain` sono nomi da provare.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

NA = "n/d"

# Anti-ice dinamico (icing.py): soglie e fenomeni in un solo posto. Fonti: PPRuNe, guide FlyByWire, flashcards (NON il FCOM Fenix): da verificare sul FCOM Fenix.
AI_ICING_TEMP_MAX = 10  # gradi C: icing conditions con temperatura (OAT a terra, TAT in volo) <= 10 e umidita' visibile; da verificare sul FCOM Fenix
AI_VIS_MAX_M = 1600  # visibilita' <= 1600 m conta come umidita' visibile (anche senza codice) e rende contabile BR; da verificare sul FCOM Fenix
AI_CRZ_SAT_MIN = -40  # climb: sotto questa SAT l'anti-ice puo' restare OFF (ON solo in Cb / ICE DETECTED); da verificare sul FCOM Fenix
AI_DESC_SAT_MAX = -40  # descent preparation: sotto questa SAT l'anti-ice va ON (indipendente dal METAR); da verificare sul FCOM Fenix
AI_WX_ALWAYS = ("RA", "DZ", "SN", "SG", "PL", "GR", "GS", "UP", "FG", "TS")  # fenomeni METAR che contano sempre; da verificare sul FCOM Fenix
AI_WX_VIS = ("BR",)  # fenomeni che contano solo se vis <= AI_VIS_MAX_M
AI_CLOUD_TYPES = ("CB", "TCU")  # nuvole che contano sempre
AI_CLOUD_COVERS_ALWAYS = ("VV",)  # coperture che contano sempre
AI_CLOUD_COVERS_AIR = ("BKN", "OVC")  # coperture che contano solo in volo (FEW/SCT escluse)
FLIGHT_GROUP = "Flight data"  # voci cinematiche: solo nella card Flight data (restano nella richiesta SimConnect)

# Stati GSX (FSDT_GSX_*_STATE): mappa da confermare dal vivo (fonte non ufficiale: issue mrlm-net/simconnect #663, che cita il manuale GSX).
# 1 avail (callable, richiamabile), 2 non disponibile, 3 bypassed, 4 requested, 5 in progress, 6 completed. 0 = non letta (nessuno stato valido).
GSX_STATES = {1: "AVAIL", 2: "NOT AVAIL", 3: "BYPASSED", 4: "REQUESTED", 5: "IN PROGRESS", 6: "COMPLETED"}
GSX_ACTIVE = ("IN PROGRESS", "COMPLETED")  # unico posto: stati GSX che valgono "servizio avviato" (in corso o completato) per i check {"gsx": [...]}
GSX_DONE = ("COMPLETED",)  # unico posto: stati GSX che valgono "servizio concluso" (check {"gsx": [...], "set": "done"})
GSX_SETS = {"active": GSX_ACTIVE, "done": GSX_DONE}  # nome del set nel check; default "active"
GSX_GROUP = "GSX"
GSX_SHORT = {"gsx_refuel": "Refuel", "gsx_cater": "Catering", "gsx_board": "Boarding"}  # nome breve nella riga Actual/NOW


def gsx_live_text(key: str, raw) -> str:
    """Testo "<Servizio> <STATO>" per la pagina, senza il grezzo (es. "Boarding AVAIL"); non letta: "Refuel n/d"; fuori mappa: "Refuel n/d (N)"."""
    v = to_number(raw)
    if v is None:
        return f"{GSX_SHORT[key]} {NA}"
    state = GSX_STATES.get(int(v)) if v == int(v) else None
    return f"{GSX_SHORT[key]} {state}" if state else f"{GSX_SHORT[key]} {NA} ({raw_text(v)})"


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
    "tcas": {0: "STBY", 1: "TA", 2: "TA/RA"},  # provvisoria (ipotesi): da verificare dal vivo col grezzo
    "xpdrop": {0: "STBY", 1: "AUTO", 2: "ON"},  # S_XPDR_OPERATION, verificata dal vivo 20/11 (Andrea)
    "detent": {-1: "BETWEEN", 0: "REVERSE", 1: "REVERSE IDLE", 2: "IDLE", 3: "CLIMB", 4: "FLX", 5: "TOGA"},  # virtuale, dalle LVar INPUT/GATE (thrust.py)
    "autobrake": {0: "OFF", 1: "LO", 2: "MED", 3: "MAX"},  # virtuale, dalle tre lampade (autobrake_state)
    "gsx": GSX_STATES,
    "numeric": {},  # nessuna mappa: si mostra solo il valore (nessun esito)
}
AMBIGUOUS = {"landing", "nose", "flap", "speedbrake", "engmode", "wxsys", "irmode", "signs", "emerlt", "tcas", "gsx"}  # lo 0 non e' "spento": mostra anche il grezzo


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
    Item("squawk", "Transponder", "Squawk code", "numeric", {"Before start": "as cleared"}, None, "N_FREQ_XPDR_SELECTED"),  # verificata dal vivo 13/11
    Item("tcas", "Transponder", "TCAS mode selector", "tcas", {"Before T/O": "TA or TA/RA", "After landing": "STBY"}, None, "S_XPDR_MODE"),  # valori 0/1/2 ipotizzati
    Item("xpdr_operation", "Transponder", "XPDR operation", "xpdrop", {"Before start": "AUTO"}, None, "S_XPDR_OPERATION"),  # 0 STBY, 1 AUTO, 2 ON (Andrea 20/11)
    Item("xpdr_atc", "Transponder", "XPDR ATC (raw)", "numeric", {}, None, "S_XPDR_ATC"),  # solo grezzo, per capire la mappa
    # GSX (mappa da confermare, vedi GSX_STATES): voce senza colore se la L-var non risponde o il valore e' fuori mappa
    Item("gsx_refuel", "GSX", "GSX refueling state", "gsx", {}, None, "FSDT_GSX_REFUELING_STATE"),
    Item("gsx_cater", "GSX", "GSX catering state", "gsx", {}, None, "FSDT_GSX_CATERING_STATE"),
    Item("gsx_board", "GSX", "GSX boarding state", "gsx", {}, None, "FSDT_GSX_BOARDING_STATE"),
    # GSX, sola diagnostica (solo grezzo, mai interpretato): per capire dal vivo cosa risponde Couatl (python simconnect_backend.py --probe)
    Item("gsx_d_couatl_started", "GSX", "COUATL_STARTED (raw)", "numeric", {}, None, "FSDT_GSX_COUATL_STARTED"),
    Item("gsx_d_couatl_alive", "GSX", "COUATL_ALIVE (raw)", "numeric", {}, None, "FSDT_GSX_COUATL_ALIVE"),
    Item("gsx_d_heartbeat", "GSX", "HEARTBEAT (raw)", "numeric", {}, None, "FSDT_GSX_HEARTBEAT"),
    Item("gsx_d_version_major", "GSX", "VERSION_MAJOR (raw)", "numeric", {}, None, "FSDT_GSX_VERSION_MAJOR"),
    Item("gsx_d_version_minor", "GSX", "VERSION_MINOR (raw)", "numeric", {}, None, "FSDT_GSX_VERSION_MINOR"),
    Item("gsx_d_version_build", "GSX", "VERSION_BUILD (raw)", "numeric", {}, None, "FSDT_GSX_VERSION_BUILD"),
    Item("gsx_d_deboarding_state", "GSX", "DEBOARDING_STATE (raw)", "numeric", {}, None, "FSDT_GSX_DEBOARDING_STATE"),
    Item("gsx_d_departure_state", "GSX", "DEPARTURE_STATE (raw)", "numeric", {}, None, "FSDT_GSX_DEPARTURE_STATE"),
    Item("gsx_d_pushback_status", "GSX", "PUSHBACK_STATUS (raw)", "numeric", {}, None, "FSDT_GSX_PUSHBACK_STATUS"),
    Item("gsx_d_jetway", "GSX", "JETWAY (raw)", "numeric", {}, None, "FSDT_GSX_JETWAY"),
    Item("gsx_d_stairs", "GSX", "STAIRS (raw)", "numeric", {}, None, "FSDT_GSX_STAIRS"),
    Item("gsx_d_gpu_state", "GSX", "GPU_STATE (raw)", "numeric", {}, None, "FSDT_GSX_GPU_STATE"),
    Item("gsx_d_state", "GSX", "STATE (raw)", "numeric", {}, None, "FSDT_GSX_STATE"),
    Item("fcu_alt", "FCU", "FCU altitude", "numeric", {"Climb": "as cleared"}, None, "N_FCU_ALTITUDE"),  # piedi, verificata dal vivo 13/11
    # FMGS performance & glareshield (nodi, 0 = non inserita; lampade 0/1; da verificare dal vivo col grezzo)
    Item("v1", "FMGS performance", "V1", "numeric", {"Before start": "as briefing"}, None, "N_MISC_PERF_TO_V1"),
    Item("vr", "FMGS performance", "VR", "numeric", {"Before start": "as briefing"}, None, "N_MISC_PERF_TO_VR"),
    Item("v2", "FMGS performance", "V2", "numeric", {"Before start": "as briefing"}, None, "N_MISC_PERF_TO_V2"),
    Item("flex", "FMGS performance", "Flex temp", "numeric", {"Before start": "as briefing"}, None, "N_MISC_PERF_TO_FLEX"),
    Item("cstr1", "Glareshield", "Constraint pb 1", "onoff", {"Before start": "ON"}, None, "I_FCU_EFIS1_CSTR"),
    Item("cstr2", "Glareshield", "Constraint pb 2", "onoff", {"Before start": "ON"}, None, "I_FCU_EFIS2_CSTR"),
    # LS pb: nome L-var IPOTIZZATO per analogia con CSTR/FD, NON verificato dal vivo (se non risponde il valore e' "n/d")
    Item("ls1", "Glareshield", "LS pb 1", "onoff", {}, None, "I_FCU_EFIS1_LS", uncertain=True),
    Item("ls2", "Glareshield", "LS pb 2", "onoff", {}, None, "I_FCU_EFIS2_LS", uncertain=True),
    Item("fd1", "Glareshield", "Flight director pb 1", "onoff", {"Before start": "ON"}, None, "I_FCU_EFIS1_FD"),
    Item("fd2", "Glareshield", "Flight director pb 2", "onoff", {"Before start": "ON"}, None, "I_FCU_EFIS2_FD"),
    Item("ap1", "Glareshield", "Autopilot 1", "onoff", {}, None, "I_FCU_AP1"),  # lampada, da verificare dal vivo
    Item("ap2", "Glareshield", "Autopilot 2", "onoff", {}, None, "I_FCU_AP2"),
    Item("athr", "Glareshield", "Autothrust", "onoff", {}, None, "I_FCU_ATHR"),
    Item("appr", "Glareshield", "APPR button", "onoff", {}, None, "I_FCU_APPR"),  # lampada, da verificare dal vivo
    Item("sat", "Anti-ice", "SAT (C)", "numeric", {}, "(A:AMBIENT TEMPERATURE, Celsius)"),  # SimVar standard, solo da mostrare (se non risponde: n/d)
    Item("tat", "Anti-ice", "TAT (C)", "numeric", {}, "(A:TOTAL AIR TEMPERATURE, Celsius)"),  # SimVar standard (da verificare sul Fenix: prova 1 sul Shadow); assente = 0
    Item("gnd", "Anti-ice", "On ground (virtual)", "numeric"),  # virtuale: SIM ON GROUND del core (1 a terra, 0 in volo)
    Item("eng1_ai", "Anti-ice", "Engine 1 anti-ice sw", "onoff", {}, None, "S_OH_PNEUMATIC_ENG1_ANTI_ICE"),  # da verificare dal vivo
    Item("eng2_ai", "Anti-ice", "Engine 2 anti-ice sw", "onoff", {}, None, "S_OH_PNEUMATIC_ENG2_ANTI_ICE"),
    # LO/MED: nomi L-var IPOTIZZATI per analogia con MAX, NON verificati dal vivo (se non rispondono: ripiego, vedi autobrake_state)
    Item("autobrake_lo", "Autobrake", "Autobrake LO (lamp L)", "onoff", {}, None, "I_MIP_AUTOBRAKE_LO_L", uncertain=True),
    Item("autobrake_med", "Autobrake", "Autobrake MED (lamp L)", "onoff", {}, None, "I_MIP_AUTOBRAKE_MED_L", uncertain=True),
    Item("autobrake_sel", "Autobrake", "Autobrake selected (from lamps)", "autobrake"),  # virtuale, solo da mostrare
    Item("autobrake_max", "Autobrake", "Autobrake MAX (lamp L)", "onoff", {"Before T/O": "ON"}, None, "I_MIP_AUTOBRAKE_MAX_L"),  # ipotesi: la lampada L si accende a MAX armato, da provare dal vivo
    Item("baro_std", "Glareshield", "Baro STD (EFIS 1)", "numeric", {"Before start": "set"}, None, "B_FCU_EFIS1_BARO_STD"),
    Item("baro_hpa", "Glareshield", "Baro hPa (EFIS 1)", "numeric", {"Before start": "set"}, None, "N_FCU_EFIS1_BARO_HPA"),
    Item("baro_inch", "Glareshield", "Baro inHg (EFIS 1)", "numeric", {"Before start": "set"}, None, "N_FCU_EFIS1_BARO_INCH"),
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
    # Peso e carburante. N_FUEL_TOTAL_QTY: unita' NON nota (dipende da B_CONFIG_WEIGHT_LBS: 1 = lbs, 0 = kg, da provare dal vivo).
    # Verificato da Andrea (13/11): N_FUEL_TOTAL_QTY e' in MIGLIAIA dell'unita' mostrata (1.17 = ECAM 1160 kg; 6.6 = 6600 kg).
    Item("fuel_total", "Weight & fuel", "Fuel total (Fenix raw, thousands)", "numeric", {}, None, "N_FUEL_TOTAL_QTY"),
    Item("fob_now", "Weight & fuel", "FOB (Fenix, display unit)", "numeric", {"Before start": "as plan FOB"}),  # virtuale: fuel_total x 1000
    Item("cfg_lbs", "Weight & fuel", "Fenix weight unit lbs (raw)", "numeric", {}, None, "B_CONFIG_WEIGHT_LBS"),  # solo grezzo
    Item("total_weight", "Weight & fuel", "Total weight (MSFS, lbs)", "numeric", {}, "(A:TOTAL WEIGHT, Pounds)"),
    Item("fuel_weight", "Weight & fuel", "Fuel weight (MSFS, lbs)", "numeric", {}, "(A:FUEL TOTAL QUANTITY WEIGHT, Pounds)"),
    Item("zfw_calc", "Weight & fuel", "ZFW calc. (MSFS total - fuel, Fenix unit)", "numeric"),  # virtuale, solo da mostrare
    Item("vs_fpm", FLIGHT_GROUP, "VS (fpm)", "numeric"),  # virtuale: A:VERTICAL SPEED del core (stessa della pill VS), per il check "lt" della checklist
    # Thrust levers: INPUT 0..5 e GATE 10..5 (gate = 10 - input) per leva
    Item("thr_gate_l", "Thrust", "Throttle gate L (raw)", "numeric", {}, None, "A_FC_THROTTLE_LEFT_GATE"),  # solo grezzo
    Item("thr_gate_r", "Thrust", "Throttle gate R (raw)", "numeric", {}, None, "A_FC_THROTTLE_RIGHT_GATE"),  # solo grezzo
    Item("thr_in_l", "Thrust", "Throttle input L (raw)", "numeric", {}, None, "A_FC_THROTTLE_LEFT_INPUT"),  # solo grezzo
    Item("thr_in_r", "Thrust", "Throttle input R (raw)", "numeric", {}, None, "A_FC_THROTTLE_RIGHT_INPUT"),  # solo grezzo
    # Virtuali (nessuna sorgente): detent calcolato da thr_in_* e thr_gate_* (thrust.py); None se non leggibile
    Item("thr_det_l", "Thrust", "Thrust lever L detent", "detent"),
    Item("thr_det_r", "Thrust", "Thrust lever R detent", "detent"),
    # Dati di volo correnti (sola lettura, solo standard: nessuna L-var). Segni MSFS: pitch + = muso giu', bank + = sinistra.
    Item("lat", "Flight data", "Latitude", "numeric", {}, "(A:PLANE LATITUDE, Degrees)"),
    Item("lon", "Flight data", "Longitude", "numeric", {}, "(A:PLANE LONGITUDE, Degrees)"),
    Item("gs", "Flight data", "Ground speed (kt)", "numeric", {}, "(A:GROUND VELOCITY, Knots)"),
    Item("hdg_mag", "Flight data", "Heading magnetic", "numeric", {}, "(A:PLANE HEADING DEGREES MAGNETIC, Degrees)"),
    Item("trk_mag", "Flight data", "Track magnetic", "numeric", {}, "(A:GPS GROUND MAGNETIC TRACK, Degrees)"),
    Item("pitch", "Flight data", "Pitch (MSFS sign)", "numeric", {}, "(A:PLANE PITCH DEGREES, Degrees)"),
    Item("bank", "Flight data", "Bank (MSFS sign)", "numeric", {}, "(A:PLANE BANK DEGREES, Degrees)"),
]


def to_number(raw):
    if raw is None or isinstance(raw, bool):
        return None if raw is None else float(raw)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


LB_PER_KG = 2.20462


def weight_annotate(raw: dict | None) -> dict:
    """Valori virtuali fob_now = fuel_total x 1000 (nell'unita' del display) e zfw_calc = peso totale - carburante (MSFS, lbs), nell'unita' del Fenix (cfg_lbs 0 = kg, altrimenti lbs); None se manca un dato."""
    ft = to_number((raw or {}).get("fuel_total"))
    out = {"fob_now": None if ft is None else round(ft * 1000)}
    total, fuel = to_number((raw or {}).get("total_weight")), to_number((raw or {}).get("fuel_weight"))
    if total is None or fuel is None:
        return {**out, "zfw_calc": None}
    zfw = total - fuel
    if to_number((raw or {}).get("cfg_lbs")) == 0:
        zfw /= LB_PER_KG
    return {**out, "zfw_calc": round(zfw)}


def vs_annotate(raw: dict | None, core: dict | None) -> dict:
    """Valore virtuale vs_fpm = VS del core (fpm); None se non leggibile."""
    return {"vs_fpm": to_number((core or {}).get("vs"))}


def gnd_annotate(raw: dict | None, core: dict | None) -> dict:
    """Valore virtuale gnd = on_ground del core (1 a terra, 0 in volo); None se non leggibile."""
    v = to_number((core or {}).get("on_ground"))
    return {"gnd": None if v is None else (1 if v >= 0.5 else 0)}


def autobrake_state(raw: dict | None) -> dict:
    """Valore virtuale autobrake 0 OFF / 1 LO / 2 MED / 3 MAX dalle lampade. Prudente: una sola lampada accesa => quella (anche se le altre non si leggono);
    OFF solo se tutte e tre sono leggibili e spente; piu' lampade accese o dati mancanti senza nessuna accesa => None (n/d)."""
    vals = [to_number((raw or {}).get(k)) for k in ("autobrake_lo", "autobrake_med", "autobrake_max")]
    on = [i + 1 for i, v in enumerate(vals) if v is not None and v >= 0.5]
    if len(on) == 1:
        return {"autobrake_sel": on[0]}
    if not on and all(v is not None for v in vals):
        return {"autobrake_sel": 0}
    return {"autobrake_sel": None}


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
        if it.group == FLIGHT_GROUP:
            continue  # mostrato nella card "Flight data", non in "Cockpit state"
        raw = monitor.get(it.key)
        groups.setdefault(it.group, []).append([it.name, label(it, raw), expected_text(it), raw_text(raw)])
    return [[g, rows] for g, rows in groups.items()]


# -- Pannello "Flight data" (puro): righe [etichetta, testo]; dato mancante/non numerico = "—", mai "0" --
DASH = "\u2014"
TRACK_MIN_GS = 2.0  # sotto questa velocita' al suolo la rotta GPS non e' significativa


def _fmt(raw, spec: str, unit: str = "") -> str:
    v = to_number(raw)
    return DASH if v is None else f"{v:{spec}}{unit}"


def _signed(v: float, nd: int) -> str:
    r = round(v, nd)
    return f"{0.0 if r == 0 else r:+.{nd}f}"


def _coord(raw, pos: str, neg: str) -> str:
    v = to_number(raw)
    return DASH if v is None else f"{abs(v):.4f}\u00b0 {pos if v >= 0 else neg}"


def _deg(raw) -> str:
    v = to_number(raw)
    return DASH if v is None else f"{round(v) % 360:03d}\u00b0"


def _angle_up(raw, unit: str) -> str:
    """Pitch/bank: segno invertito rispetto a MSFS (muso su = +, bank destra = +)."""
    v = to_number(raw)
    return DASH if v is None else f"{_signed(-v, 1)}{unit}"


def _core_line(core: dict) -> str:
    def num(k, spec, unit):
        return _fmt(core.get(k), spec, unit)
    onoff = {0: "OFF", 1: "ON"}

    def flag(k, m):
        v = to_number(core.get(k))
        return DASH if v is None or int(v) not in m else m[int(v)]
    parts = [f"Alt {num('alt', '.0f', ' ft')}", f"IAS {num('ias', '.0f', ' kt')}", f"VS {_vs(core.get('vs'))}",
             f"AGL {num('agl', '.0f', ' ft')}", f"Ground {flag('on_ground', {0: 'AIR', 1: 'GND'})}",
             f"Gear {flag('gear', {0: 'UP', 1: 'DOWN'})}", f"Landing {flag('landing', onoff)}",
             f"Seatbelt {flag('seatbelt', onoff)}"]
    return " \u00b7 ".join(parts)


def _vs(raw) -> str:
    v = to_number(raw)
    return DASH if v is None else f"{round(v):+d} fpm"


def flight_panel(raw: dict | None, core: dict | None) -> list:
    """Righe [etichetta, testo] dei dati di volo, con unita'. raw = monitor (grezzi + virtuali), core = campione del comandante."""
    raw, core = raw or {}, core or {}
    gs = to_number(raw.get("gs"))
    track = _deg(raw.get("trk_mag")) if gs is not None and gs >= TRACK_MIN_GS else DASH
    return [["Position", f"{_coord(raw.get('lat'), 'N', 'S')}  {_coord(raw.get('lon'), 'E', 'W')}"],
            ["Ground speed", _fmt(raw.get("gs"), ".0f", " kt")],
            ["Heading (mag)", _deg(raw.get("hdg_mag"))], ["Track (mag)", track],
            ["Pitch", _angle_up(raw.get("pitch"), "\u00b0")], ["Bank", _angle_up(raw.get("bank"), "\u00b0")],
            ["Core", _core_line(core)]]


# Registro delle pills dell'header: (chiave, etichetta, funzione (raw, core) -> testo). Ordine = ordine di visualizzazione.
PILLS = [
    ("gs", "GS", lambda raw, core: _fmt(raw.get("gs"), ".0f", " kt")),
    ("alt", "ALT", lambda raw, core: _fmt(core.get("alt"), ".0f", " ft")),
    ("vs", "VS", lambda raw, core: _vs(core.get("vs"))),
]
ACTIVE_PILLS = ["gs", "alt", "vs"]  # default fisso (la scelta da parte dell'utente non e' ancora implementata)


def active_pill_defs(active: list | None = None) -> list:
    """[[chiave, etichetta], ...] delle pills attive, nell'ordine del registro."""
    keys = ACTIVE_PILLS if active is None else active
    return [[k, lbl] for k, lbl, _ in PILLS if k in keys]


def flight_pills(raw: dict | None, core: dict | None) -> dict:
    """{chiave: testo} per tutte le pills del registro; "\u2014" se manca il dato."""
    raw, core = raw or {}, core or {}
    return {k: fn(raw, core) for k, _, fn in PILLS}
