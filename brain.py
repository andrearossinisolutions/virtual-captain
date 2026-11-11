"""Il comandante: da un campione di dati restituisce le frasi da dire (funzione pura, testabile)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Briefing:
    ta: int = 5000
    accel_agl: int = 1500
    mode: str = "allenamento"  # oppure "silenziosa"


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
        return [long_text if (long_text and self.b.mode == "allenamento") else text]

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
