"""Virtual Captain. Uso: python main.py [--sim] [--port 8080] [--host IP] [--no-firewall]

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
    st = State()
    srv = web.serve(st, host, port)
    url = f"http://{host}:{port}"
    print(f"\nVirtual Captain — apri dal tablet:  {url}\n", flush=True)
    show_qr(url)

    def speak(text):
        say(text)
        st.add_log(text)

    aao, brain, src, clock = AAO(), None, None, 0.0
    try:
        while True:
            if st.phase != "flying":
                brain = None
                time.sleep(0.3)
                continue
            if brain is None:  # appena premuto "Briefing complete"
                b = st.briefing()
                brain, src, clock = Brain(b), (simulated() if sim else None), 0.0
                with st.lock:
                    text = readout(dict(st.fields), dict(st.plan), st.mode)
                speak(text)
            brain.b = st.briefing()  # modifiche dal tablet valide subito
            try:
                s = next(src) if src else aao.read()
                st.aao_ok = True if not src else None
            except StopIteration:
                speak("Simulated flight finished.")
                st.set_phase("briefing")
                continue
            except Exception as e:  # noqa: BLE001
                st.aao_ok = False
                print("AAO not responding:", e)
                time.sleep(2)
                continue
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
