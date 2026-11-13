"""Virtual Captain. Uso: python main.py [--sim] [--test-links] [--port 8080] [--host IP] [--no-firewall]

Avvia la pagina web del briefing (raggiungibile dal tablet via Tailscale) e, quando dal tablet si preme
"Briefing complete", legge il simulatore (AAO) e parla. --sim = volo simulato, senza AAO.
"""
import sys
import time

import firewall
import web
from aao import AAO
from brain import Brain, readout
from speak import say, wait
from state import State
from weather import Weather, online_fetch


def simulated():
    """Decollo finto (corsa a terra con velocità crescente, poi salita) per provare voce e soglie."""
    t = 0
    while True:
        roll = t < 9  # corsa: 0, 20, … 160 kt
        alt = 0 if roll else (t - 9) * 150
        yield {"on_ground": 1.0 if roll else 0.0, "vs": 0 if roll else 2000, "ias": t * 20 if roll else 160,
               "agl": alt, "alt": alt, "gear": 1.0 if t < 45 else 0.0, "landing": 1, "seatbelt": 1}, sim_monitor(t, roll)
        t += 1
        if alt > 11000:
            return


def sim_read(src, last, done):
    """Un passo del volo simulato: (campione, frase da dire|None, done, finito). A volo finito resta l'ultimo campione, frase una sola volta."""
    try:
        return next(src), None, done, False
    except StopIteration:
        return last, (None if done else "Simulated flight finished."), True, True


def sim_monitor(t, roll):
    """Valori finti plausibili del cockpit per la pagina in --sim (qualche voce assente di proposito)."""
    return {"apu_master": 0, "apu_bleed": 0, "pack1": 1 if t > 3 else 0, "pack2": 1 if t > 3 else 0,
            "landing_l": 2, "landing_r": 2, "nose": 2 if roll else 0, "strobe": 2, "beacon": 1, "navlogo": 1,
            "rwy_turnoff": 1 if roll else 0, "seatbelt": 1, "flap": 1, "spoilers": 0, "gear": 1.0 if t < 45 else 0.0,
            "engmode": 1, "eng1": 1, "eng2": 1, "parking": None,
            "baro_std": 0 if t < 20 else 1, "baro_hpa": 1013, "baro_inch": 29.92}


def show_qr(url):
    """QR nel terminale per aprire la pagina dal tablet (serve `pip install qrcode`; senza, solo l'indirizzo)."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001  (libreria assente o terminale senza Unicode)
        print("(per il QR: pip install qrcode)")


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    sim = "--sim" in sys.argv
    port = int(arg("--port", 8080))
    host = arg("--host") or web.tailscale_ip()
    if not host:
        host = "127.0.0.1"
        print("⚠ Tailscale non trovato: pagina raggiungibile solo da questo PC. Avvia Tailscale o usa --host <IP>.")
    if "--no-firewall" not in sys.argv:
        firewall.ensure(port)
    st = State(weather=Weather(fetch=(lambda icao: 1013.0 if icao == st.fields.get("origin") else 1009.0) if sim else online_fetch))
    st.cl_trust_links = "--test-links" in sys.argv  # solo prova: link valutati come verified + valori grezzi accanto alle voci
    st.cl_demo = sim  # solo in --sim: link della checklist trattati come verificati (resa dimostrativa)
    srv = web.serve(st, host, port)
    url = f"http://{host}:{port}"
    print(f"\nVirtual Captain — apri dal tablet:  {url}\n", flush=True)
    show_qr(url)

    def speak(text):
        say(text, on_start=st.add_log)  # nel log (e in grande nella pagina) solo quando inizia la lettura

    if arg("--aao"):
        st.update({"aao_url": arg("--aao")})
    aao, brain, src, clock = AAO(st.aao_url), None, None, 0.0
    last, sim_done = None, False
    if not sim:
        print(f"AAO: {aao.base}", flush=True)
    try:
        while True:
            if st.phase != "flying":
                brain = None
                time.sleep(0.3)
                continue
            if brain is None:  # appena premuto "Briefing complete"
                b = st.briefing()
                brain, src, clock = Brain(b), (simulated() if sim else None), 0.0
                last, sim_done = None, False
                with st.lock:
                    text = readout(dict(st.fields), dict(st.plan), st.mode)
                speak(text)
            if aao.base != st.aao_url:  # indirizzo cambiato dalla pagina
                aao = AAO(st.aao_url)
                print(f"AAO: {aao.base}", flush=True)
            brain.b = st.briefing()  # modifiche dal tablet valide subito
            try:
                if src:
                    sample, phrase, sim_done, finished = sim_read(src, last, sim_done)
                    if phrase:
                        speak(phrase)
                    if finished:  # volo simulato finito: resta in "flying" con l'ultimo campione fermo
                        if sample:
                            st.set_monitor(sample[1])
                        time.sleep(0.4)
                        continue
                    last = sample
                    s, mon = sample
                else:
                    s, mon = aao.read_all()
                st.aao_ok = True if not src else None
            except Exception as e:  # noqa: BLE001
                st.aao_ok = False
                print("AAO not responding:", e)
                time.sleep(2)
                continue
            st.set_monitor(mon)
            clock = clock + 1 if src else time.time()
            for phrase in brain.update(s, clock):
                speak(phrase)
            time.sleep(0.4 if src else 0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        wait()


if __name__ == "__main__":
    main()
