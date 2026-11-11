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
                              "Positive rate. Gear up: ritira il carrello ora che non si torna a terra.")
        # carrello ancora giù 10 s dopo il positive rate
        if "posrate" in self.done and s["gear"] >= 1 and now - self.airborne_since > 10:
            if "gear_repeat" not in self.done:
                self.done.add("gear_repeat")
                out.append("Gear is still down, gear up")
        if s["agl"] >= self.b.accel_agl:
            out += self._once("accel", "Acceleration altitude",
                              "Acceleration altitude: abbassa il muso, accelera e ritira i flap come da schedula.")
        if s["alt"] >= self.b.ta:
            out += self._once("ta", "Transition altitude, standard",
                              "Transition altitude: imposta lo standard, 1013 o 29.92, e controlla che lo mostrino entrambi gli altimetri.")
        if s["alt"] >= 10000:
            out += self._once("fl100", "Ten thousand, landing lights and seat belts off",
                              "Diecimila piedi: spegni le landing lights e i seat belts, da qui sopra non servono più.")
        return out
