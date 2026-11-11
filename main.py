"""Virtual Captain (MVP salita). Uso: python main.py [--sim]  (--sim = volo simulato, senza AAO)"""
import sys
import time

import briefing_ui
from aao import AAO
from brain import Brain, Briefing
from speak import say, wait


def simulated():
    """Decollo finto per provare voce e soglie senza simulatore."""
    t = 0
    while True:
        alt = max(0, (t - 5) * 150)
        yield {"on_ground": 1.0 if t < 5 else 0.0, "vs": 2000 if t >= 5 else 0,
               "agl": alt, "alt": alt, "gear": 1.0 if t < 40 else 0.0, "landing": 1, "seatbelt": 1}
        t += 1
        if alt > 11000:
            return


def main():
    r = briefing_ui.run()
    if not r:
        return
    brain = Brain(Briefing(ta=r["ta"], accel_agl=r["accel_agl"], mode=r["mode"]))
    say(f"Briefing received. Transition altitude {r['ta']} feet.")
    src = simulated() if "--sim" in sys.argv else None
    aao = AAO()
    sim_t = 0.0
    while True:
        try:
            s = next(src) if src else aao.read()
        except StopIteration:
            break
        except Exception as e:  # noqa: BLE001
            print("AAO not responding:", e)
            time.sleep(2)
            continue
        sim_t = sim_t + 1 if src else time.time()
        for phrase in brain.update(s, sim_t):
            say(phrase)
        time.sleep(0.4 if src else 0.5)


if __name__ == "__main__":
    main()
    wait()
