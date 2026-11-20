"""Backend SimConnect (ctypes puro, solo Windows), SOLA LETTURA: legge le variabili del core e del monitor.

Copiato e ridotto da RoxPanel (provato sul Shadow). Nessuna libreria di terzi: la SimConnect.dll si carica a runtime
(WinDLL). Tutte le chiamate alla DLL partono da un solo thread ("simconnect-dispatch"). Nessuna scrittura sul simulatore.
Diagnosi: python simconnect_backend.py --probe [--simconnect-dll PERCORSO]
"""
from __future__ import annotations

import ctypes as C
import glob
import importlib.util
import logging
import os
import sys
import threading
import time
from typing import Callable, Optional

import simvars

log = logging.getLogger("captain.sim")

DLL_NAME = "SimConnect.dll"
APP_NAME = b"VirtualCaptain"

FLOAT64 = 4                      # SIMCONNECT_DATATYPE_FLOAT64
PERIOD_VISUAL_FRAME = 2
RECV_EXCEPTION, RECV_OPEN, RECV_QUIT, RECV_SIMOBJECT_DATA = 1, 2, 3, 8
USER_AIRCRAFT = 0

INTERVAL_FRAMES = 30             # eventi VISUAL_FRAME tra due invii (circa 2 Hz: da calibrare sul Shadow)
STALE_S = 5.0                    # dato del core piu' vecchio di cosi': BackendError
OPEN_BACKOFF = (1.0, 2.0, 4.0, 5.0)
POLL_S = 0.015                   # attesa tra due GetNextDispatch
MAX_MSG_PER_STEP = 64
WATCHDOG_S = 15.0                # connesso ma nessun dato del core da tanto: si riconnette
MAX_BAD_DISPATCH = 20            # giri consecutivi con errore di GetNextDispatch (diverso da E_FAIL) -> disconnessione
E_FAIL = 0x80004005              # "nessun messaggio": normale
PERIOD_NEVER = 0                 # SIMCONNECT_PERIOD_NEVER: ferma una richiesta periodica
GSX_RETRY_S = 12.0               # blocco GSX muto (tutto 0): si riregistra ogni tanto, GSX/Couatl puo' partire dopo Virtual Captain
GEN_STEP = 1000                  # id definizione/richiesta di una riregistrazione = id del blocco + GEN_STEP * generazione


class BackendError(Exception):
    """Simulatore non raggiungibile o operazione fallita."""


class DllNotFound(BackendError):
    """SimConnect.dll non trovata nei percorsi noti (si puo' scaricare)."""


class Recv(C.Structure):
    _fields_ = [("dwSize", C.c_uint32), ("dwVersion", C.c_uint32), ("dwID", C.c_uint32)]


class RecvData(C.Structure):  # SIMCONNECT_RECV_SIMOBJECT_DATA, senza dwData
    _fields_ = [("hdr", Recv), ("dwRequestID", C.c_uint32), ("dwObjectID", C.c_uint32),
                ("dwDefineID", C.c_uint32), ("dwFlags", C.c_uint32), ("dwentrynumber", C.c_uint32),
                ("dwoutof", C.c_uint32), ("dwDefineCount", C.c_uint32)]


class RecvExc(C.Structure):
    _fields_ = [("hdr", Recv), ("dwException", C.c_uint32), ("dwSendID", C.c_uint32), ("dwIndex", C.c_uint32)]


# ---- percorsi ----
def base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def vendor_dir() -> str:
    """Cartella della SimConnect.dll propria di Virtual Captain (anche quella scaricata dopo il consenso)."""
    return os.path.join(base_dir(), "vendor")


def _pip_package_dll() -> list:
    """DLL nel pacchetto pip "SimConnect" (solo test personali): si cerca il file, il modulo NON si importa."""
    try:
        spec = importlib.util.find_spec("SimConnect")
    except Exception:
        return []
    found: list = []
    for base in (spec.submodule_search_locations or []) if spec else []:
        found.extend(glob.glob(os.path.join(base, "**", DLL_NAME), recursive=True))
    return found


def dll_candidates(explicit: Optional[str] = None) -> list:
    """Percorsi dove cercare la DLL, in ordine di priorita' (l'argomento --simconnect-dll vince, poi CAPTAIN_SIMCONNECT_DLL)."""
    if explicit:
        return [explicit]
    out = []
    env = os.environ.get("CAPTAIN_SIMCONNECT_DLL")
    if env:
        out.append(env)
    out += [os.path.join(base_dir(), DLL_NAME), os.path.join(vendor_dir(), DLL_NAME)]
    for var in ("MSFS_SDK", "MSFS2024_SDK", "MSFS2020_SDK"):
        sdk = os.environ.get(var)
        if sdk:
            out.append(os.path.join(sdk, "SimConnect SDK", "lib", DLL_NAME))
    for sdk in ("C:\\MSFS 2024 SDK", "C:\\MSFS SDK"):
        out.append(os.path.join(sdk, "SimConnect SDK", "lib", DLL_NAME))
    out.extend(_pip_package_dll())
    return out


def find_simconnect_dll(explicit: Optional[str] = None, exists: Callable[[str], bool] = os.path.isfile) -> str:
    """Percorso della SimConnect.dll o BackendError con il messaggio per l'utente."""
    if explicit:
        if exists(explicit):
            return explicit
        raise BackendError("SimConnect.dll not found in %s (--simconnect-dll)" % explicit)
    for path in dll_candidates():
        if exists(path):
            return path
    raise DllNotFound("SimConnect.dll not found: press 'Download SimConnect.dll', or copy it next to main.py "
                      "or into vendor\\, or use --simconnect-dll (see README)")


def load_dll(path: str):
    """Carica la DLL (solo Windows) e imposta i prototipi. WinDLL si importa solo qui, a runtime."""
    if sys.platform != "win32":
        raise BackendError("SimConnect is only available on Windows (use --sim)")
    try:
        dll = C.WinDLL(path)  # type: ignore[attr-defined]
    except OSError as e:
        raise BackendError("SimConnect.dll cannot be loaded (%s): the 64-bit version is needed" % e) from e
    vp, u32 = C.c_void_p, C.c_uint32
    protos = {
        "SimConnect_Open": [C.POINTER(vp), C.c_char_p, vp, u32, vp, u32],
        "SimConnect_Close": [vp],
        "SimConnect_AddToDataDefinition": [vp, u32, C.c_char_p, C.c_char_p, u32, C.c_float, u32],
        "SimConnect_RequestDataOnSimObject": [vp, u32, u32, u32, u32, u32, u32, u32, u32],
        "SimConnect_GetNextDispatch": [vp, C.POINTER(C.POINTER(Recv)), C.POINTER(u32)],
    }
    for name, argtypes in protos.items():
        fn = getattr(dll, name)
        fn.argtypes = argtypes
        fn.restype = C.c_long  # HRESULT: i valori negativi sono errori
    return dll


class SimConnectBackend:
    """Backend SimConnect in sola lettura. `dll` iniettabile (per i test); senza, si cerca e si carica la SimConnect.dll."""

    name = "SimConnect"

    def __init__(self, dll=None, dll_path: Optional[str] = None, clock: Callable[[], float] = time.monotonic,
                 use_thread: bool = True) -> None:
        self._dll = dll
        self._dll_path = dll_path
        self._clock = clock
        self._use_thread = use_thread
        self._h: Optional[C.c_void_p] = None
        self._connected = False
        self._fatal: Optional[str] = None     # errore non recuperabile (DLL assente)
        self.dll_missing = False              # True solo se la DLL non e' stata trovata (si puo' scaricare)
        self._dll_lock = threading.Lock()     # serializza start()/retry_dll() (non il thread di dispatch)
        self._last_error = "not connected to MSFS"
        self._open_failures = 0
        self._next_open = 0.0
        self._last_data_at = 0.0      # ultimo pacchetto del core (o RECV_OPEN) per il watchdog
        self._bad_dispatch = 0
        self._last_exc: Optional[str] = None
        self._warned_size = False
        self._blocks = simvars.build_blocks()
        self._by_req: dict = {}               # id richiesta (anche delle riregistrazioni) -> blocco
        self._gsx_answered = False            # almeno una L-var GSX ha risposto con un valore diverso da 0
        self._gsx_gen = 0
        self._gsx_next = 0.0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._core: dict = {}
        self._monitor: dict = {}
        self._core_ts: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- API pubblica ----
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._fatal or self._last_error

    def start(self) -> None:
        with self._dll_lock:
            self._load_and_run()

    def _load_and_run(self) -> None:
        if self._dll is None:
            try:
                self._dll = load_dll(find_simconnect_dll(self._dll_path))
            except BackendError as e:
                self._fatal = str(e)
                self.dll_missing = isinstance(e, DllNotFound)
                log.error("%s", e)
                return
        self._fatal = None
        self.dll_missing = False
        if self._use_thread and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="simconnect-dispatch", daemon=True)
            self._thread.start()

    def retry_dll(self) -> None:
        """Dopo il download (o col pulsante Retry): ripete ricerca e caricamento della DLL. Nulla se gia' caricata."""
        with self._dll_lock:
            if self._dll is not None or self._stop.is_set():
                return
            self._load_and_run()

    def retry(self) -> None:
        """Pulsante Retry: riprova subito (DLL se manca, altrimenti apertura senza attendere il backoff)."""
        self.retry_dll()
        self._next_open = 0.0

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        else:
            self._close()

    def read_all(self, timeout: float = 1.0) -> tuple:
        """-> (core, monitor): dizionari come prima (0 se la variabile non arriva; None per le voci virtuali).
        BackendError se non connesso o dati piu' vecchi di STALE_S."""
        deadline = time.monotonic() + timeout  # attesa reale; la vecchiaia del dato usa self._clock
        with self._cond:
            while True:
                if self._fatal:
                    raise BackendError(self._fatal)
                if not self._connected:
                    raise BackendError(self._last_error)
                now = self._clock()
                if self._core_ts is not None and now - self._core_ts <= STALE_S:
                    return {**simvars.default_core(), **self._core}, {**simvars.default_monitor(), **self._monitor}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    msg = "no fresh data from MSFS (simulator paused or in a menu?)"
                    if self._last_exc:
                        msg += " (last SimConnect exception: %s)" % self._last_exc
                    raise BackendError(msg)
                self._cond.wait(min(remaining, 0.1))

    # ---- thread di dispatch ----
    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    wait = self.step()
                except Exception:
                    log.exception("SimConnect: error in the dispatch thread")
                    wait = 0.5
                self._stop.wait(wait)
        finally:
            self._close()

    def step(self) -> float:
        """Un giro del thread di dispatch; ritorna i secondi di attesa prima del prossimo."""
        now = self._clock()
        if self._dll is None:
            return 0.5
        if self._h is None:
            if now < self._next_open:
                return min(self._next_open - now, 0.5)
            self._try_open(now)
            return POLL_S
        self._drain(now)
        if self._h is not None and self._connected:
            now = self._clock()
            self._gsx_retry(now)
            if now - self._last_data_at > WATCHDOG_S:
                log.warning("No data from MSFS for %.0f s: closing and reopening SimConnect.", WATCHDOG_S)
                self._disconnect(now, "no data from MSFS (SimConnect OFF)")
        return POLL_S

    @staticmethod
    def _hr_failed(hr: int) -> bool:
        return hr < 0

    def _try_open(self, now: float) -> None:
        h = C.c_void_p()
        try:
            hr = self._dll.SimConnect_Open(C.byref(h), APP_NAME, None, 0, None, 0)
        except Exception as e:
            hr = -1
            self._set_error("SimConnect_Open: %s" % e)
        if self._hr_failed(hr) or not h.value:
            delay = OPEN_BACKOFF[min(self._open_failures, len(OPEN_BACKOFF) - 1)]
            if self._open_failures == 0:
                log.warning("MSFS unreachable (SimConnect_Open hr=0x%08X): retrying every %.0f-%.0f s",
                            hr & 0xFFFFFFFF, OPEN_BACKOFF[0], OPEN_BACKOFF[-1])
            self._open_failures += 1
            self._next_open = now + delay
            self._set_error("MSFS not reachable (SimConnect OFF)")
            return
        self._h = h
        self._open_failures = 0
        log.info("SimConnect open: waiting for RECV_OPEN.")

    def _set_error(self, msg: str) -> None:
        with self._cond:
            self._last_error = msg

    def _drain(self, now: float) -> None:
        for _ in range(MAX_MSG_PER_STEP):
            pp = C.POINTER(Recv)()
            cb = C.c_uint32()
            try:
                hr = self._dll.SimConnect_GetNextDispatch(self._h, C.byref(pp), C.byref(cb))
            except Exception as e:
                log.error("SimConnect_GetNextDispatch: %s", e)
                self._disconnect(now, "SimConnect dispatch error")
                return
            if self._hr_failed(hr) and (hr & 0xFFFFFFFF) != E_FAIL:
                self._bad_dispatch += 1
                if self._bad_dispatch >= MAX_BAD_DISPATCH:
                    log.warning("GetNextDispatch failing for %d cycles (hr=0x%08X): reconnecting.",
                                self._bad_dispatch, hr & 0xFFFFFFFF)
                    self._disconnect(now, "SimConnect errors (SimConnect OFF)")
                return
            if self._hr_failed(hr) or not pp:
                return  # niente da leggere (E_FAIL e' normale)
            self._bad_dispatch = 0
            msg_id = pp.contents.dwID
            if msg_id == RECV_OPEN:
                self._on_open()
            elif msg_id == RECV_QUIT:
                log.warning("MSFS closed SimConnect: reconnecting.")
                self._disconnect(now, "MSFS closed (SimConnect OFF)")
                return
            elif msg_id == RECV_EXCEPTION:
                e = C.cast(pp, C.POINTER(RecvExc)).contents
                log.warning("SimConnect: exception %d (sendID %d, index %d)", e.dwException, e.dwSendID, e.dwIndex)
                self._last_exc = "code %d, index %d" % (e.dwException, e.dwIndex)
            elif msg_id == RECV_SIMOBJECT_DATA:
                self._on_data(pp)

    def _register(self, b, ident: int) -> bool:
        """Definizione + richiesta periodica di un blocco con l'id dato (id richiesta = id definizione). True se nessun errore."""
        ok = True
        for i, (name, unit) in enumerate(b.reqs):
            hr = self._dll.SimConnect_AddToDataDefinition(self._h, ident, name.encode(), unit.encode(), FLOAT64, C.c_float(0.0), i)
            if self._hr_failed(hr):
                log.error("AddToDataDefinition %s failed (hr=0x%08X): value is 0", name, hr & 0xFFFFFFFF)
                ok = False
        hr = self._dll.SimConnect_RequestDataOnSimObject(self._h, ident, ident, USER_AIRCRAFT,
                                                         PERIOD_VISUAL_FRAME, 0, 0, INTERVAL_FRAMES, 0)
        if self._hr_failed(hr):
            log.error("RequestDataOnSimObject (block %d) failed (hr=0x%08X)", ident, hr & 0xFFFFFFFF)
            ok = False
        self._by_req[ident] = b
        return ok

    def _gsx_retry(self, now: float) -> None:
        """Riregistrazione tardiva: finche' nessuna L-var GSX risponde (tutto 0), ogni GSX_RETRY_S si ferma la richiesta vecchia e se ne fa una
        nuova (nuovi id) per i soli blocchi GSX. Solo lettura: nessuna scrittura di variabili."""
        if self._gsx_answered or now < self._gsx_next:
            return
        self._gsx_next = now + GSX_RETRY_S
        blocks = [b for b in self._blocks if b.retry]
        if not blocks:
            return
        self._gsx_gen += 1
        log.info("GSX: no response from the L-vars, re-registering (attempt %d).", self._gsx_gen)
        for b in blocks:
            old = b.ident + GEN_STEP * (self._gsx_gen - 1)
            self._dll.SimConnect_RequestDataOnSimObject(self._h, old, old, USER_AIRCRAFT, PERIOD_NEVER, 0, 0, INTERVAL_FRAMES, 0)
            self._register(b, b.ident + GEN_STEP * self._gsx_gen)

    def _on_open(self) -> None:
        self._warned_size = False
        self._bad_dispatch = 0
        self._last_data_at = self._clock()
        self._by_req, self._gsx_answered, self._gsx_gen = {}, False, 0
        self._gsx_next = self._clock() + GSX_RETRY_S
        ok = True
        for b in self._blocks:  # una definizione per blocco, una richiesta periodica per definizione (id richiesta = id definizione)
            ok = self._register(b, b.ident) and ok
        with self._cond:
            self._connected = True
            self._core, self._monitor, self._core_ts = {}, {}, None
            self._last_error = "waiting for data from MSFS" if ok else "data request refused by SimConnect"
            self._cond.notify_all()
        log.info("SimConnect connected to MSFS.")

    def _on_data(self, pp) -> None:
        d = C.cast(pp, C.POINTER(RecvData)).contents
        count = d.dwDefineCount
        size = C.sizeof(RecvData)
        if d.hdr.dwSize < size + 8 * count:
            if not self._warned_size:
                self._warned_size = True
                log.warning("SimConnect: truncated data packet (dwSize %d, at least %d expected): ignored",
                            d.hdr.dwSize, size + 8 * count)
            return
        values = list((C.c_double * count).from_address(C.addressof(d) + size))  # copia subito dal buffer
        block = self._by_req.get(d.dwRequestID)
        if block is None:
            return
        got = simvars.decode(block, values)
        if not got:
            log.warning("SimConnect: %d values instead of %d in block %d (ignored)", count, len(block.keys), block.ident)
            return
        if block.retry:
            if not self._gsx_answered and not any(got.values()):
                return  # L-var GSX ancora mute (0 non e' uno stato): restano None, si riprova a registrarle
            self._gsx_answered = True
        with self._cond:
            if block.target == "core":
                self._core.update(got)
                self._core_ts = self._clock()
                self._last_data_at = self._core_ts
            else:
                self._monitor.update(got)
            self._cond.notify_all()

    def _disconnect(self, now: float, reason: str) -> None:
        self._close()
        self._open_failures = 0
        self._next_open = now + OPEN_BACKOFF[0]
        with self._cond:
            self._connected = False
            self._core, self._monitor, self._core_ts = {}, {}, None
            self._last_error = reason
            self._cond.notify_all()

    def _close(self) -> None:
        h, self._h = self._h, None
        if h is not None and self._dll is not None:
            try:
                self._dll.SimConnect_Close(h)
            except Exception as e:
                log.debug("SimConnect_Close: %s", e)
        with self._cond:
            self._connected = False
            self._cond.notify_all()


def probe(dll_path: Optional[str] = None, wait_s: float = 20.0) -> int:
    """Diagnosi in sola lettura: si connette, legge una volta core e monitor e stampa i valori (0 = assente o sconosciuta)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    be = SimConnectBackend(dll_path=dll_path)
    be.start()
    end = time.monotonic() + wait_s
    gsx_end = time.monotonic() + GSX_RETRY_S * 2 + 3
    try:
        while True:
            try:
                core, mon = be.read_all(timeout=1.0)
                if any(mon[k] is not None for k in simvars.GSX_KEYS) or time.monotonic() > gsx_end:
                    break
                time.sleep(0.5)  # core gia' letto: si lascia il tempo alle L-var GSX (anche a una riregistrazione)
            except BackendError as e:
                if be.dll_missing or time.monotonic() > end:
                    print("PROBE FAILED:", e)
                    return 1
        print("SimConnect OK. Core:")
        for k, v in core.items():
            print(f"  {k:12} {v}")
        print("Monitor (0 may also mean variable missing or unknown; GSX: 'not read' = no answer yet, see GSX_RETRY_S):")
        for k, v in mon.items():
            if v is not None:
                print(f"  {k:16} {v}  <- {simvars.MONITOR[k]}")
            elif k in simvars.GSX_KEYS:
                print(f"  {k:16} not read  <- {simvars.MONITOR[k]}")
        return 0
    finally:
        be.stop()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--probe" in args:
        dll = args[args.index("--simconnect-dll") + 1] if "--simconnect-dll" in args else None
        sys.exit(probe(dll))
    print(__doc__)
