"""Il comandante: da un campione di dati restituisce le frasi da dire (funzione pura, testabile)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Briefing:
    ta: int = 5000
    accel_agl: int = 1500
    mode: str = "training"  # or "silent"


@dataclass
class Brain:
    b: Briefing
    done: set = field(default_factory=set)
    airborne_since: float | None = None
    gear_checked: bool = False

    def _once(self, key, text, long_text=None):
        if key in self.done:
            return []
        self.done.add(key)
        return [long_text if (long_text and self.b.mode == "training") else text]

    def update(self, s: dict, now: float) -> list[str]:
        out: list[str] = []
        if s["on_ground"] >= 1:
            # a terra: riarmo tutto per il prossimo decollo
            self.done.clear()
            self.airborne_since = None
            return out
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
        f"Departure {fields['origin']}" if fields.get("origin") else "",
        f"runway {fields['runway']}" if fields.get("runway") else "") if x)
    sid = (plan or {}).get("sid")
    if dep or sid:
        parts.append((dep + (", " if dep and sid else "") + (f"SID {sid}" if sid else "")).strip(", ") + ".")
    if fields.get("ta"):
        parts.append(f"Transition altitude {fields['ta']} feet.")
    speeds = [f"{lbl} {fields[k]}" for k, lbl in (("v1", "V1"), ("vr", "rotate"), ("v2", "V2")) if fields.get(k)]
    if speeds:
        parts.append(", ".join(speeds) + ".")
    star = (plan or {}).get("star")
    if fields.get("destination") or star:
        arr = f"Arrival {fields['destination']}" if fields.get("destination") else ""
        parts.append((arr + (", " if arr and star else "") + (f"STAR {star}" if star else "")) + ".")
    if mode == "training" and sid:
        parts.append("Check the SID in the FMC before the clearance call.")
    return " ".join(parts)
