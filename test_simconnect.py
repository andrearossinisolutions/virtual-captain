# Test di simvars.py e simconnect_backend.py con una DLL finta (solo stdlib, nessun MSFS, nessuna rete, funziona anche su Mac).
# Uso: python3 test_simconnect.py
import ctypes as C
import logging
import os
import sys
import time

import monitor
import simconnect_backend as SB
import simvars as V
from simconnect_backend import BackendError, DllNotFound

HERE = os.path.dirname(os.path.abspath(__file__))
BLOCKS = V.build_blocks()
CORE = BLOCKS[0]


def msg_simple(msg_id):
    buf = (C.c_char * C.sizeof(SB.Recv))()
    h = SB.Recv.from_buffer(buf)
    h.dwSize, h.dwID = C.sizeof(SB.Recv), msg_id
    return buf


def msg_data(req_id, values):
    size = C.sizeof(SB.RecvData)
    buf = (C.c_char * (size + 8 * len(values)))()
    d = SB.RecvData.from_buffer(buf)
    d.hdr.dwSize, d.hdr.dwID = len(buf), SB.RECV_SIMOBJECT_DATA
    d.dwRequestID, d.dwDefineID, d.dwDefineCount = req_id, req_id, len(values)
    for i, v in enumerate(values):
        C.c_double.from_buffer(buf, size + 8 * i).value = v
    return buf


def msg_block(block, **kw):
    return msg_data(block.ident, [kw.get(k, 0.0) for k in block.keys])


def msg_exception(code, send_id, index):
    buf = (C.c_char * C.sizeof(SB.RecvExc))()
    e = SB.RecvExc.from_buffer(buf)
    e.hdr.dwSize, e.hdr.dwID = len(buf), SB.RECV_EXCEPTION
    e.dwException, e.dwSendID, e.dwIndex = code, send_id, index
    return buf


class FakeDll:
    """Imita la SimConnect.dll (solo lettura): registra le chiamate e restituisce i messaggi preparati."""

    def __init__(self):
        self.open_results = []     # HRESULT successivi di Open (poi sempre 0)
        self.msgs = []
        self.keep = []
        self.opens = 0
        self.closes = 0
        self.adds = []             # (defid, nome, unita', indice)
        self.requests = []         # (reqid, defid, period, interval)
        self.dispatch_hr = None    # se impostato, GetNextDispatch fallisce sempre con questo HRESULT

    def SimConnect_Open(self, ph, name, hwnd, ev, hev, cfg):
        self.opens += 1
        hr = self.open_results.pop(0) if self.open_results else 0
        if hr >= 0:
            ph._obj.value = 1234
            self.msgs.append(msg_simple(SB.RECV_OPEN))
        return hr

    def SimConnect_Close(self, h):
        self.closes += 1
        return 0

    def SimConnect_AddToDataDefinition(self, h, def_id, name, units, dtype, eps, idx):
        assert dtype == SB.FLOAT64
        self.adds.append((def_id, name.decode(), units.decode(), idx))
        return 0

    def SimConnect_RequestDataOnSimObject(self, h, req, def_id, obj, period, flags, origin, interval, limit):
        self.requests.append((req, def_id, period, interval))
        return 0

    def SimConnect_GetNextDispatch(self, h, ppp, pcb):
        if self.dispatch_hr is not None:
            return self.dispatch_hr
        if not self.msgs:
            return -2147467259  # E_FAIL: niente da leggere
        buf = self.msgs.pop(0)
        self.keep.append(buf)
        ppp._obj.contents = SB.Recv.from_buffer(buf)
        return 0


def make(dll=None):
    t = [100.0]
    dll = dll or FakeDll()
    be = SB.SimConnectBackend(dll=dll, clock=lambda: t[0], use_thread=False)
    be.start()
    return be, dll, t


def connect(be):
    be.step()  # Open
    be.step()  # RECV_OPEN
    assert be._connected


def raises(fn):
    try:
        fn()
    except BackendError as e:
        return str(e)
    raise AssertionError("BackendError attesa")


# ---- simvars (puro) ----
def test_parse_request():
    assert V.parse_request("(A:VERTICAL SPEED, Feet per minute)") == ("VERTICAL SPEED", "feet per minute")
    assert V.parse_request("(L:S_MIP_GEAR, Number)") == ("L:S_MIP_GEAR", "number")
    assert V.parse_request("(A:SIM ON GROUND, Bool)") == ("SIM ON GROUND", "bool")
    try:
        V.parse_request("boh")
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError attesa")


def test_definitions_from_vars_and_monitor():
    assert CORE.target == "core" and CORE.keys == tuple(V.VARS) and len(CORE.reqs) == 8
    assert CORE.reqs[1] == ("VERTICAL SPEED", "feet per minute")
    mon_keys = [k for b in BLOCKS[1:] for k in b.keys]
    assert all(b.target == "monitor" and len(b.keys) <= V.MAX_PER_DEF for b in BLOCKS[1:])
    assert len(mon_keys) == len(set(mon_keys)) == sum(1 for r in V.MONITOR.values() if r)
    assert len({b.ident for b in BLOCKS}) == len(BLOCKS)
    for b in BLOCKS[1:]:  # A: e L: mai nello stesso blocco
        assert len({name.startswith("L:") for name, _ in b.reqs}) == 1
    by = {it.key: it for it in monitor.ITEMS}
    for k, r in V.MONITOR.items():
        assert (r is None) == (not by[k].lvar and not by[k].std), k  # virtuali senza sorgente
    assert V.MONITOR["apu_master"] == "(L:S_OH_ELEC_APU_MASTER, Number)"  # nessun ripiego sulla standard
    assert V.MONITOR["total_weight"] == "(A:TOTAL WEIGHT, Pounds)" and V.MONITOR["thr_det_l"] is None
    allreq = [nu for b in BLOCKS for nu in b.reqs]
    assert ("L:I_FCU_EFIS1_LS", "number") in allreq and ("PLANE LATITUDE", "degrees") in allreq


def test_decode_and_defaults():
    assert V.decode(CORE, [1, 2, 3, 4, 5, 6, 7, 8]) == dict(zip(V.VARS, map(float, range(1, 9))))
    assert V.decode(CORE, [1, 2]) == {}  # numero errato: ignorato
    assert V.decode(CORE, [float("nan")] + [0] * 7)["on_ground"] == 0.0
    assert V.default_core() == {k: 0.0 for k in V.VARS}
    dm = V.default_monitor()
    assert dm["pack1"] == 0.0 and dm["thr_det_l"] is None and dm["zfw_calc"] is None and set(dm) == set(V.MONITOR)


# ---- backend ----
def test_open_failure_backoff():
    dll = FakeDll()
    dll.open_results = [-1, -1, -1]
    be, dll, t = make(dll)
    assert "not connected" in raises(lambda: be.read_all(0.1))
    be.step()
    assert dll.opens == 1
    t[0] += 0.5
    be.step()
    assert dll.opens == 1  # backoff 1 s
    t[0] += 0.5
    be.step()
    assert dll.opens == 2  # fallisce: prossimo dopo 2 s
    t[0] += 1.9
    be.step()
    assert dll.opens == 2
    t[0] += 0.1
    be.step()
    assert dll.opens == 3
    t[0] += 4.0
    be.step()
    assert dll.opens == 4 and be._h is not None
    be.step()
    assert be._connected
    be.retry()  # Retry: azzera l'attesa
    assert be._next_open == 0.0


def test_definitions_sent_and_period():
    be, dll, t = make()
    connect(be)
    assert len(dll.adds) == sum(len(b.reqs) for b in BLOCKS)
    assert {a[2] for a in dll.adds if a[1].startswith("L:")} == {"number"}
    assert (CORE.ident, "SIM ON GROUND", "bool", 0) in dll.adds
    assert [r[0] for r in dll.requests] == [b.ident for b in BLOCKS]
    assert all(r[2] == SB.PERIOD_VISUAL_FRAME and r[3] == SB.INTERVAL_FRAMES for r in dll.requests)
    assert SB.STALE_S == 5.0


def test_data_to_cache_and_missing_is_zero():
    be, dll, t = make()
    connect(be)
    raises(lambda: be.read_all(0.05))  # connesso, nessun dato
    dll.msgs.append(msg_block(CORE, ias=250.0, alt=12000.0))
    lblock = [b for b in BLOCKS[1:] if "pack1" in b.keys][0]
    dll.msgs.append(msg_block(lblock, pack1=1.0))
    for _ in range(3):
        be.step()
    core, mon = be.read_all(0.5)
    assert core["ias"] == 250.0 and core["alt"] == 12000.0 and core["vs"] == 0.0 and set(core) == set(V.VARS)
    assert mon["pack1"] == 1.0 and mon["pack2"] == 0.0 and mon["ls1"] == 0.0  # assenti: 0
    assert mon["thr_det_l"] is None and set(mon) == set(V.MONITOR)
    dll.msgs.append(msg_data(CORE.ident, [1.0, 2.0]))  # numero errato: ignorato
    be.step()
    assert be.read_all(0.5)[0]["ias"] == 250.0


def test_bad_a_block_does_not_break_others():
    be, dll, t = make()
    connect(be)
    dll.msgs.append(msg_block(CORE, ias=100.0))
    for b in BLOCKS[1:]:
        if b.reqs[0][0].startswith("L:"):
            dll.msgs.append(msg_block(b, **{b.keys[0]: 1.0}))
        # il blocco A: non risponde mai (variabile errata)
    for _ in range(6):
        be.step()
    core, mon = be.read_all(0.5)
    assert core["ias"] == 100.0 and mon["sat"] == 0.0 and mon["lat"] == 0.0
    lb = [b for b in BLOCKS[1:] if b.reqs[0][0].startswith("L:")][0]
    assert mon[lb.keys[0]] == 1.0


def test_stale_data():
    be, dll, t = make()
    connect(be)
    dll.msgs.append(msg_block(CORE, ias=250.0))
    be.step()
    be.read_all(0.5)
    t[0] += SB.STALE_S - 0.1
    be.read_all(0.5)
    t[0] += 0.2
    assert "no fresh data" in raises(lambda: be.read_all(0.05))


def test_quit_reconnects():
    be, dll, t = make()
    connect(be)
    dll.msgs.append(msg_block(CORE, ias=250.0))
    be.step()
    dll.msgs.append(msg_simple(SB.RECV_QUIT))
    be.step()
    assert dll.closes == 1 and not be._connected and be._h is None
    raises(lambda: be.read_all(0.1))
    be.step()
    assert dll.opens == 1  # attende il backoff
    t[0] += 1.0
    be.step()
    be.step()
    assert dll.opens == 2 and be._connected
    assert len(dll.adds) == 2 * sum(len(b.reqs) for b in BLOCKS)  # definizioni rifatte
    raises(lambda: be.read_all(0.05))  # cache svuotata
    dll.msgs.append(msg_block(CORE, ias=260.0))
    be.step()
    assert be.read_all(0.5)[0]["ias"] == 260.0


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_exception_logged_and_reported():
    cap = Capture()
    lg = logging.getLogger("captain.sim")
    lg.addHandler(cap)
    old = lg.level
    lg.setLevel(logging.DEBUG)
    try:
        be, dll, t = make()
        connect(be)
        dll.msgs.append(msg_exception(7, 42, 3))
        be.step()
        assert any("eccezione 7" in r.getMessage() and r.levelno == logging.WARNING for r in cap.records)
        assert be._connected  # un'eccezione non interrompe nulla
        msg = raises(lambda: be.read_all(0.05))
        assert "last SimConnect exception: code 7, index 3" in msg, msg
        short = msg_block(CORE)
        C.cast(short, C.POINTER(SB.RecvData)).contents.hdr.dwSize = C.sizeof(SB.RecvData)  # troncato
        dll.msgs.extend([short, short])
        be.step()
        be.step()
        assert sum("troncato" in r.getMessage() for r in cap.records) == 1
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old)


def test_watchdog_msfs_dies_without_quit():
    be, dll, t = make()
    connect(be)
    dll.msgs.append(msg_block(CORE, ias=250.0))
    be.step()
    be.read_all(0.5)
    dll.dispatch_hr = -2147467260  # MSFS morto: errori in continuo, nessun QUIT
    for _ in range(SB.MAX_BAD_DISPATCH + 1):
        be.step()
    assert dll.closes == 1 and not be._connected
    dll.dispatch_hr = None  # variante senza errori: solo silenzio -> watchdog
    t[0] += 1.0
    be.step()
    be.step()
    assert be._connected and dll.opens == 2
    t[0] += SB.WATCHDOG_S - 1.0
    be.step()
    assert be._connected
    t[0] += 2.0
    be.step()
    assert dll.closes == 2 and not be._connected
    for _ in range(SB.MAX_BAD_DISPATCH * 3):  # E_FAIL non e' un errore
        be.step()
    assert dll.closes == 2


def test_watchdog_data_resets_timer():
    be, dll, t = make()
    connect(be)
    for _ in range(4):
        t[0] += SB.WATCHDOG_S - 1.0
        dll.msgs.append(msg_block(CORE, ias=250.0))
        be.step()
    assert be._connected and dll.closes == 0


def test_dll_lookup_messages():
    try:
        SB.find_simconnect_dll("C:\\nope\\SimConnect.dll", exists=lambda p: False)
    except BackendError as e:
        assert not isinstance(e, DllNotFound)  # percorso esplicito: niente pulsante
        assert "--simconnect-dll" in str(e)
    else:
        raise AssertionError("attesa")
    msg = raises(lambda: SB.find_simconnect_dll(None, exists=lambda p: False))
    assert "SimConnect.dll not found" in msg and "README" in msg and "Download SimConnect.dll" in msg
    assert SB.find_simconnect_dll("x.dll", exists=lambda p: True) == "x.dll"
    cands = SB.dll_candidates()
    assert cands[0].endswith("SimConnect.dll") and os.path.join("vendor", "SimConnect.dll") in cands[1]
    assert os.path.join(SB.vendor_dir(), "SimConnect.dll") in cands and SB.dll_candidates("x.dll") == ["x.dll"]
    sdk = [i for i, c in enumerate(cands) if "SimConnect SDK" in c]
    assert sdk and cands.index(os.path.join(SB.vendor_dir(), "SimConnect.dll")) < sdk[0]
    be = SB.SimConnectBackend(dll_path=os.path.join(HERE, "non-esiste", "SimConnect.dll"))
    be.start()
    assert "SimConnect.dll" in raises(lambda: be.read_all(0.1)) and be.last_error
    be.stop()
    if sys.platform != "win32":
        assert "only available on Windows" in raises(lambda: SB.load_dll(os.path.join(HERE, "x.dll")))


def test_dll_missing_and_retry():
    saved = (SB.find_simconnect_dll, SB.load_dll)
    found = []

    def fake_find(explicit=None, exists=None):
        if not found:
            raise DllNotFound("messaggio qualunque")
        return found[0]
    SB.find_simconnect_dll = fake_find
    SB.load_dll = lambda path: FakeDll()
    try:
        be = SB.SimConnectBackend()
        be.start()
        assert be.dll_missing is True and be._thread is None
        assert "messaggio qualunque" in raises(lambda: be.read_all(0.1))
        found.append("C:\\x\\SimConnect.dll")
        be.retry_dll()
        assert be.dll_missing is False and be._fatal is None and be._thread is not None
        t = be._thread
        be.retry_dll()  # gia' caricata: nulla
        assert be._thread is t
        be.stop()

        def bad_load(path):
            raise BackendError("SimConnect.dll cannot be loaded (x)")
        SB.load_dll = bad_load
        be2 = SB.SimConnectBackend()
        be2.start()
        assert be2.dll_missing is False and be2._fatal and be2._thread is None
    finally:
        SB.find_simconnect_dll, SB.load_dll = saved


def test_read_only_source_and_no_agpl_import():
    for name in ("simconnect_backend.py", "simvars.py", "dll_fetch.py"):
        src = open(os.path.join(HERE, name), encoding="utf-8").read()
        for word in ("SetDataOnSimObject", "TransmitClientEvent", "MapClientEvent"):
            assert word not in src, (name, word)
        for line in src.splitlines():
            code = line.split("#")[0].strip()
            assert not code.startswith(("import SimConnect", "from SimConnect")), (name, line)
    assert "SimConnect" not in sys.modules


def test_thread_start_stop():
    dll = FakeDll()
    be = SB.SimConnectBackend(dll=dll)
    be.start()
    end = time.monotonic() + 3
    while not be._connected and time.monotonic() < end:
        time.sleep(0.01)
    assert be._connected
    dll.msgs.append(msg_block(CORE, ias=250.0))
    assert be.read_all(2.0)[0]["ias"] == 250.0
    be.stop()
    assert dll.closes == 1 and not be._connected


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("OK", n)
