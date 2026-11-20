"""Shared status registry plus the pure mapping State/Uplink -> window rows (standard library only, no tkinter).

App threads write with report(); the GUI reads snapshot() and checks version. The mapping functions
(sim_row, dll_row, uplink_row, flight_rows, derive_rows) take plain data so they are testable anywhere.
"""
from __future__ import annotations

import threading
import time

PENDING = "PENDING"  # grey: waiting / not known yet
OK = "OK"            # green
WARN = "WARN"        # yellow
ERROR = "ERROR"      # red
STATES = (PENDING, OK, WARN, ERROR)

GROUPS = ("System", "Connections", "Flight")


class StatusItem:
    def __init__(self, key: str, label: str, group: str, order: int, state: str = PENDING,
                 detail: str = "", ts=None) -> None:
        self.key, self.label, self.group, self.order = key, label, group, order
        self.state, self.detail, self.ts = state, detail, ts

    def copy(self) -> "StatusItem":
        return StatusItem(self.key, self.label, self.group, self.order, self.state, self.detail, self.ts)


class StatusRegistry:
    def __init__(self, clock=time.time) -> None:
        self._lock = threading.Lock()
        self._items: dict = {}
        self._clock = clock
        self.version = 0

    def register(self, key: str, label: str, group: str = "System", order: int = 0) -> None:
        with self._lock:
            if key in self._items:
                return
            self._items[key] = StatusItem(key, label, group, order)
            self.version += 1

    def report(self, key: str, state: str, detail: str = "") -> None:
        """Updates a row from any thread; no-op if unchanged or unknown."""
        if state not in STATES:
            return
        with self._lock:
            item = self._items.get(key)
            if item is None or (item.state == state and item.detail == detail):
                return
            item.state, item.detail, item.ts = state, detail, self._clock()
            self.version += 1

    def snapshot(self) -> list:
        """Ordered copy: by group (GROUPS order), then order, then label."""
        with self._lock:
            items = [i.copy() for i in self._items.values()]
        rank = lambda g: GROUPS.index(g) if g in GROUPS else len(GROUPS)  # noqa: E731
        items.sort(key=lambda i: (rank(i.group), i.order, i.label))
        return items


# Standard rows: (key, label, group, order)
STANDARD_ITEMS = (
    ("code", "Code (git pull)", "System", 0),
    ("deps", "Dependencies (pip)", "System", 1),
    ("sim", "Simulator", "Connections", 0),
    ("simdll", "SimConnect.dll", "Connections", 1),
    ("uplink", "Remote server", "Connections", 2),
    ("page", "Local page", "Connections", 3),
    ("flight", "Flight", "Flight", 0),
    ("plan", "Plan", "Flight", 1),
    ("checklist", "Checklist phase", "Flight", 2),
    ("captain", "Last captain phrase", "Flight", 3),
    ("error", "Last error", "Flight", 4),
)


def make_registry() -> StatusRegistry:
    reg = StatusRegistry()
    for key, label, group, order in STANDARD_ITEMS:
        reg.register(key, label, group, order)
    return reg


# ---------------------------------------------------------------- State / Uplink -> rows

def sim_row(snap: dict) -> tuple:
    """Simulator: grey waiting for MSFS, green connected (or simulated flight), red if SimConnect is unusable."""
    if snap.get("sim_demo"):
        return OK, "Simulated flight (no simulator)"
    if snap.get("dll_missing"):
        return ERROR, "SimConnect.dll missing"
    ok = snap.get("sim_ok")
    if ok:
        return OK, "Connected"
    if ok is False:
        return PENDING, "Waiting for MSFS"
    return PENDING, "Starting"


def dll_row(snap: dict) -> tuple:
    if snap.get("sim_demo"):
        return PENDING, "Not needed in simulated flight"
    if snap.get("dll_missing"):
        if snap.get("dll_fetching"):
            return PENDING, "Downloading..."
        return ERROR, snap.get("dll_msg") or "Not found: use Download SimConnect.dll"
    if snap.get("sim_ok") is None:
        return PENDING, "Checking"
    return OK, "Loaded"


UPLINK_ROWS = {
    "unlinked": (WARN, "Not linked"),
    "linking": (WARN, "Linking: approve the code on the site"),
    "connecting": (WARN, "Connecting"),
    "connected": (OK, "Connected"),
    "relink_needed": (ERROR, "Link needed again"),
    "stopped": (ERROR, "Stopped"),
}


def uplink_row(up_state, message: str = "", enabled: bool = True) -> tuple:
    """Remote server row. up_state None: no uplink (disabled or started without it)."""
    if up_state is None:
        return PENDING, message or ("Disabled" if not enabled else "Not started")
    state, detail = UPLINK_ROWS.get(up_state, (PENDING, str(up_state)))
    if message and up_state in ("unlinked", "connecting", "relink_needed", "stopped"):
        detail = message
    return state, detail


def flight_rows(snap: dict) -> dict:
    """key -> (state, detail) for the Flight group (without the last error, owned by the app)."""
    f = snap.get("fields") or {}
    phase = snap.get("phase")
    dep, arr = (f.get("origin") or "").split("/")[0].strip(), (f.get("destination") or "").strip()
    out = {}
    if phase == "flying":
        out["flight"] = (OK, "In flight")
    else:
        out["flight"] = (PENDING, "Briefing")
    out["plan"] = (OK, f"{dep or '?'} - {arr or '?'}") if (dep or arr) else (PENDING, "No plan")
    names = {p.get("id"): p.get("name") for p in snap.get("cl_phases") or []}
    cl = snap.get("cl_phase")
    out["checklist"] = (OK, names.get(cl) or str(cl)) if cl and phase == "flying" else (PENDING, "-")
    log = snap.get("log") or []
    out["captain"] = (OK, log[0]) if log else (PENDING, "-")
    return out


def derive_rows(snap: dict, up_state=None, up_message: str = "", uplink_enabled: bool = True) -> dict:
    """All the rows computed from State.snapshot() and the Uplink: key -> (state, detail)."""
    rows = {"sim": sim_row(snap), "simdll": dll_row(snap), "uplink": uplink_row(up_state, up_message, uplink_enabled)}
    rows.update(flight_rows(snap))
    return rows


def apply_rows(registry: StatusRegistry, rows: dict) -> None:
    for key, (state, detail) in rows.items():
        registry.report(key, state, detail)
