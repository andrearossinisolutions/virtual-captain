"""Il comandante: da un campione di dati restituisce le frasi da dire (funzione pura, testabile)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field

PHONETIC = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo", "F": "Foxtrot", "G": "Golf",
    "H": "Hotel", "I": "India", "J": "Juliett", "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November",
    "O": "Oscar", "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango", "U": "Uniform",
    "V": "Victor", "W": "Whiskey", "X": "X-ray", "Y": "Yankee", "Z": "Zulu",
}


def spell_icao(code: str) -> str:
    """EDDF → "Echo Delta Delta Foxtrot" (cifre e altri caratteri restano così)."""
    return " ".join(PHONETIC.get(c, c) for c in code.upper())


_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_SIDE = {"L": "Left", "R": "Right", "C": "Center"}


def spell_runway(rwy: str) -> str:
    """35R → "three five Right"; 09L → "zero nine Left". Formati diversi restano com'è."""
    m = re.fullmatch(r"(\d{1,2})([LRC]?)", rwy.strip().upper())
    if not m:
        return rwy
    digits, side = m.groups()
    return " ".join([_DIGITS[int(d)] for d in digits] + ([_SIDE[side]] if side else []))


def spell_proc(ident: str) -> str:
    """Nome di SID/STAR: lettere normali fino al numero, numero, ultima lettera in fonetico.
    EMPA5B → "EMPA 5 Bravo". Formati diversi da lettere+cifra+lettera restano com'è."""
    m = re.fullmatch(r"([A-Z]+)(\d)([A-Z]?)", ident.strip().upper())
    if not m:
        return ident
    name, digit, last = m.groups()
    return " ".join(x for x in (name, digit, PHONETIC.get(last, "")) if x)


@dataclass
class Briefing:
    ta: int = 5000
    accel_agl: int = 1500
    mode: str = "training"  # or "silent"
    v1: int | None = None
    vr: int | None = None


@dataclass
class Brain:
    b: Briefing
    done: set = field(default_factory=set)
    airborne_since: float | None = None
    gear_checked: bool = False
    roll_done: set = field(default_factory=set)
    armed: bool = False  # callout del decollo attivi solo dopo essere stati fermi/lenti a terra

    def _once(self, key, text, long_text=None):
        if key in self.done:
            return []
        self.done.add(key)
        return [long_text if (long_text and self.b.mode == "training") else text]

    def update(self, s: dict, now: float) -> list[str]:
        out: list[str] = []
        ias = s.get("ias", 0)
        if s["on_ground"] >= 1:
            # a terra: riarmo le frasi della salita per il prossimo decollo
            self.done.clear()
            self.airborne_since = None
            if ias < 40:  # fermo o lento: pronto per un decollo (anche dopo un decollo abortito)
                self.armed = True
                self.roll_done.clear()
            if self.armed:  # corsa di decollo: V1 e Rotate, mai durante la corsa di atterraggio
                if self.b.v1 and ias >= self.b.v1 and "v1" not in self.roll_done:
                    self.roll_done.add("v1")
                    out.append("V1: from here we are committed to take off." if self.b.mode == "training" else "V1")
                if self.b.vr and ias >= self.b.vr and "vr" not in self.roll_done:
                    self.roll_done.add("vr")
                    out.append("Rotate: pull back smoothly, about 15 degrees of pitch." if self.b.mode == "training" else "Rotate")
            return out
        self.armed = False  # in volo: i callout del decollo si riarmano solo dopo essere tornati lenti a terra
        if self.airborne_since is None:
            self.airborne_since = now
        if s["vs"] > 200 and s["agl"] > 30 and s["gear"] >= 1:
            out += self._once("posrate", "Positive rate, gear up",
                              "Positive rate. Gear up: retract the gear now, we are not going back to the runway.")
        # carrello ancora giù 10 s dopo il positive rate
        if "posrate" in self.done and s["gear"] >= 1 and now - self.airborne_since > 10:
            if "gear_repeat" not in self.done:
                self.done.add("gear_repeat")
                out.append("Gear is still down, gear up")
        if s["agl"] >= self.b.accel_agl:
            out += self._once("accel", "Acceleration altitude",
                              "Acceleration altitude: lower the nose, accelerate and retract the flaps on schedule.")
        if s["alt"] >= self.b.ta:
            out += self._once("ta", "Transition altitude, standard",
                              "Transition altitude: set standard, 1013 or 29.92, and check both altimeters show it.")
        if s["alt"] >= 10000:
            out += self._once("fl100", "Ten thousand, landing lights and seat belts off",
                              "Ten thousand feet: landing lights and seat belts off, we no longer need them from here.")
        return out


def readout(fields: dict, plan: dict, mode: str) -> str:
    """Frase di apertura: ciò che il capitano legge quando premi "Briefing complete" (solo ciò che c'è)."""
    parts = ["Briefing received."]
    dep = " ".join(x for x in (
        f"Departure {spell_icao(fields['origin'])}" if fields.get("origin") else "",
        f"runway {spell_runway(fields['runway'])}" if fields.get("runway") else "") if x)
    sid = (plan or {}).get("sid")
    if dep or sid:
        parts.append((dep + (", " if dep and sid else "") + (f"SID {spell_proc(sid)}" if sid else "")).strip(", ") + ".")
    if fields.get("ta"):
        parts.append(f"Transition altitude {fields['ta']} feet.")
    speeds = [f"{lbl} {fields[k]}" for k, lbl in (("v1", "V1"), ("vr", "rotation"), ("v2", "V2")) if fields.get(k)]
    if speeds:
        parts.append(", ".join(speeds) + ".")
    star = (plan or {}).get("star")
    if fields.get("destination") or star:
        arr = f"Arrival {spell_icao(fields['destination'])}" if fields.get("destination") else ""
        parts.append((arr + (", " if arr and star else "") + (f"STAR {spell_proc(star)}" if star else "")) + ".")
    if mode == "training" and sid:
        parts.append("Check the SID in the FMC before the clearance call.")
    return " ".join(parts)
