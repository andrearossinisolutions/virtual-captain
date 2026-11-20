"""Virtual Captain. Normally started by launcher.py (window); `python main.py` runs it without a window.

Usage: python launcher.py [--no-gui] [--sim] [--test-links] [--port 8080] [--host IP] [--simconnect-dll PATH]
                          [--server | --no-server | --local] [--server-url URL] [--link] [--unlink] [--no-remote-commands]

Starts the briefing web page (127.0.0.1 by default, a fallback to open on this PC) and the SimConnect reader
(read-only, from startup); when "Briefing complete" is pressed the captain reads the simulator and speaks.
--sim = simulated flight, without SimConnect; --test-links = checklist links trusted (testing only): both are in the
window under Advanced. Diagnostics: python simconnect_backend.py --probe
Server link (uplink): ON by default; the first time the login code is shown in the window ("Link to server") and the
program stays usable locally. --no-server / --local exclude it for this run, --server re-enables and saves it,
--server-url changes the address (saved), --no-remote-commands (or "remote_commands": false in uplink.json) stops the
site from commanding the program (status only), --link forces a new link, --unlink revokes it on the server and deletes
the tokens (command line only).
"""
from __future__ import annotations

import logging
import sys
import threading
import time

import applog
import paths
import session
import snapshot
import status
import uplink
import web
from brain import Brain, parse_dep, readout_pair
from simconnect_backend import BackendError, SimConnectBackend
from speak import say, wait
from state import State
from weather import Weather, online_fetch

log = logging.getLogger("captain.app")


class StartupError(Exception):
    """The program cannot start (for example the port is already in use): the message is for the user."""


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
            "baro_std": 0 if t < 20 else 1, "baro_hpa": 1013, "baro_inch": 29.92,
            "lat": 45.63 + t * 0.001, "lon": 8.72 + t * 0.001, "gs": t * 20 if roll else 165,
            "hdg_mag": 168.0, "trk_mag": 170.0, "pitch": 0.0 if roll else -12.0, "bank": 1.5 if t % 4 else -1.5,
            "total_weight": 150000, "fuel_weight": 40000, "cfg_lbs": 0, "fuel_total": 18.1}


def arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def login_text(info):
    """Instructions for the login code (console only: the code never goes to the log files)."""
    return (f"\nLink the program to your Virtual Captain account:\n"
            f"  1. open  {info.get('verification_uri_complete') or info['verification_uri']}\n"
            f"  2. check that the code is  {info['user_code']}  and type its last 4 characters  ({info['user_code'][-4:]})\n"
            f"  Meanwhile the program stays usable locally.\n")


def toggled_argv(argv, flag, on):
    """argv with the flag added (on) or removed (off), for the restart requested from Advanced."""
    rest = [a for a in argv if a != flag]
    return rest + [flag] if on else rest


class App:
    """The captain: State, web page, uplink, SimConnect and the main loop (step), without any window.

    The window (gui.py) only reads self.registry and calls the public methods; run() is meant for a worker thread
    and stops when self.stop is set.
    """

    def __init__(self, argv, registry=None, state=None, headless=False, say=say):
        self.argv = list(argv)
        self.registry = registry or status.make_registry()
        self.headless = headless
        self.say = say
        self.sim = "--sim" in self.argv
        self.port = int(arg(self.argv, "--port", 8080))
        self.host = arg(self.argv, "--host") or "127.0.0.1"
        self.st = state or self._make_state()
        self.stop = threading.Event()
        self.srv = self.up = self.backend = None
        self.threads = []
        self.login_info = None       # {"user_code", "verification_uri", ...} while waiting for approval (never logged)
        self.uplink_msg = ""
        self.last_error = ""
        self.restart_argv = None     # set when Advanced asks to restart with different options
        self.brain = self.src = None
        self.clock, self.last, self.sim_done = 0.0, None, False
        self._sim_msg = ""
        self._shut = False
        self.st.cl_trust_links = "--test-links" in self.argv  # testing only: links evaluated as verified + raw values next to the items
        self.st.sim_demo = self.sim
        self.st.cl_demo = self.sim  # only in --sim: checklist links treated as verified (demo rendering)

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def _make_state(self):
        st = State(session_path=None if self.sim else session.SESSION, weather=Weather(
            fetch=(lambda icao: 1013.0 if icao == parse_dep(st.fields.get("origin"))[0] else 1009.0) if self.sim else online_fetch))
        return st

    # ---- start / stop
    def start(self):
        """Web page, uplink and SimConnect (all with their own threads). StartupError if the page cannot start."""
        try:
            self.srv = web.serve(self.st, self.host, self.port)
        except OSError as e:
            raise StartupError(f"Cannot use {self.host}:{self.port} ({e}). Is Virtual Captain already running? "
                               "Close it, or use --port <other>.") from None
        log.info("Virtual Captain page: %s", self.url)
        self.registry.report("page", status.OK, self.url)
        self.up = uplink.launch(self.argv, self.show_login, say=self._uplink_say,
                                snapshot_source=lambda: snapshot.remote_view(self.st.snapshot()),
                                command_target=self.st)  # daemon thread: reads only the State (locked), never SimConnect or the voice
        if not self.sim:
            self.backend = SimConnectBackend(dll_path=arg(self.argv, "--simconnect-dll"))
            self.st.sim_backend = self.backend
            self.backend.start()  # at program startup, not after "Briefing complete"; never writes to the simulator
        self.threads.append(threading.Thread(target=self._pump, name="status", daemon=True))
        self.threads[-1].start()

    def start_loop(self):
        """Main loop on a worker thread (window mode)."""
        t = threading.Thread(target=self.run, name="captain-loop", daemon=True)
        self.threads.append(t)
        t.start()

    def shutdown(self, wait_voice=False):
        """Idempotent: stop the loop, save the session, close uplink, SimConnect and page."""
        if self._shut:
            return
        self._shut = True
        self.stop.set()
        for t in self.threads:
            if t is not threading.current_thread():
                t.join(3.0)
        for name, fn in (("session", self.st.flush_session),
                         ("uplink", (lambda: self.up.stop()) if self.up is not None else None),
                         ("SimConnect", (lambda: self.backend.stop()) if self.backend is not None else None),
                         ("page", (lambda: self.srv.shutdown()) if self.srv is not None else None)):
            if fn is None:
                continue
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("Shutdown: error closing %s", name)
        if wait_voice:
            wait()
        log.info("Virtual Captain stopped")

    # ---- window actions
    def reconnect(self):
        log.info("Reconnect requested")
        self.st.sim_retry()

    def fetch_dll(self):
        try:
            self.st.fetch_dll()
        except ValueError as e:
            log.warning("SimConnect.dll download: %s", e)

    def set_test_links(self, on):
        self.st.cl_trust_links = bool(on)
        log.info("Test links %s", "on" if on else "off")

    def request_restart_with(self, flag, on):
        """Advanced > Simulated flight: the mode changes only at startup, so the launcher restarts the program."""
        self.restart_argv = toggled_argv(self.argv, flag, on)

    # ---- messages for the window
    def show_login(self, info):
        """Login code: kept in memory for the window (and the console in --no-gui); never in the log files."""
        self.login_info = dict(info)
        log.info("Waiting for approval on the site: open 'Link to server' for the code")
        if self.headless:
            print(login_text(info), flush=True)

    def _uplink_say(self, msg):
        self.uplink_msg = msg[len("[uplink] "):] if msg.startswith("[uplink] ") else msg
        log.info("%s", msg)

    def _pump(self):
        """Every second: State + uplink -> registry rows (threads never touch tkinter)."""
        while not self.stop.is_set():
            try:
                self.refresh_status()
            except Exception:  # noqa: BLE001
                log.exception("Status update failed")
            self.stop.wait(1.0)

    def refresh_status(self):
        snap = self.st.snapshot()
        up_state = self.up.state if self.up is not None else None
        if self.up is None or up_state != uplink.LINKING:
            self.login_info = None
        status.apply_rows(self.registry, status.derive_rows(snap, up_state, self.uplink_msg))
        self.registry.report("error", *((status.ERROR, self.last_error) if self.last_error else (status.PENDING, "-")))

    # ---- main loop
    def speak(self, text, shown=None):
        self.say(text, on_start=self.st.add_log, shown=shown)  # in the log (and big on the page) only when reading starts

    def _read_sim(self, timeout):
        """SimConnect cache -> (core, monitor) and state for the page; BackendError if not connected or stale data."""
        try:
            data = self.backend.read_all(timeout)
        except BackendError as e:
            self.st.set_sim(False, str(e), self.backend.dll_missing)
            raise
        self.st.set_sim(True)
        return data

    def run(self):
        while not self.stop.is_set():
            try:
                delay = self.step()
            except Exception as e:  # noqa: BLE001  (the loop never dies silently: it is logged and shown)
                log.exception("Error in the main loop")
                self.last_error = f"{type(e).__name__}: {e}"
                delay = 2
            if self.stop.wait(delay):
                return

    def step(self):
        """One pass of the main loop; returns the seconds to wait before the next one."""
        st = self.st
        if st.phase != "flying":
            self.brain = None
            if self.backend is not None:  # outside the flight the reading feeds the status row
                try:
                    self._read_sim(0)
                except BackendError:
                    pass
                return 0.5
            return 0.3
        if self.brain is None:
            b = st.briefing()
            if st.resumed:  # restored flight: brain state from the file, no spoken briefing; runs only after the sim check
                self.brain = Brain(b)
                self.brain.from_dict(st.brain_saved)
                self.brain.resumed = True
                self.src, self.clock, self.last, self.sim_done = (simulated() if self.sim else None), 0.0, None, False
            else:  # "Briefing complete" just pressed
                self.brain, self.src, self.clock = Brain(b), (simulated() if self.sim else None), 0.0
                self.last, self.sim_done = None, False
                with st.lock:
                    shown, text = readout_pair(dict(st.fields), dict(st.plan), st.mode)
                self.speak(text, shown)
        self.brain.b = st.briefing()  # changes from the page are valid immediately
        try:
            if self.src:
                sample, phrase, self.sim_done, finished = sim_read(self.src, self.last, self.sim_done)
                if phrase:
                    self.speak(phrase)
                if finished:  # simulated flight over: stays in "flying" with the last frozen sample
                    if sample:
                        st.set_core(sample[0])
                        st.set_monitor(sample[1])
                    return 0.4
                self.last = sample
                s, mon = sample
            else:
                s, mon = self._read_sim(1.0)
        except BackendError as e:
            if str(e) != self._sim_msg:  # logged when it changes, not every 2 seconds
                self._sim_msg = str(e)
                log.warning("SimConnect not responding: %s", e)
            return 2
        self._sim_msg = ""
        st.set_core(s)
        st.set_monitor(mon)
        if not st.sample_check(s, mon):  # flight over (even before the restart): back to briefing, session closed
            self.brain = None
            return 0
        self.clock = self.clock + 1 if self.src else time.time()
        for phrase in self.brain.update(s, self.clock):
            self.speak(phrase)
        st.set_brain(self.brain.to_dict())  # changes only when it changes: the file is updated
        return 0.4 if self.src else 0.5


def main(argv=None):
    """Run without a window (--no-gui, or python main.py): log on the console, Ctrl+C to quit."""
    argv = list(sys.argv[1:] if argv is None else argv)
    applog.setup_logging(paths.log_dir(), console=True)
    if "--unlink" in argv:
        sys.exit(uplink.unlink_cli(argv))
    app = App(argv, headless=True)
    try:
        app.start()
    except StartupError as e:
        log.error("%s", e)
        return 1
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown(wait_voice=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
