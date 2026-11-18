"""Wire contract between the PC program and the public server (constants and the pure snapshot allowlist, stdlib only).

The program connects to `wss://<base>/ws/agent` with `Authorization: Bearer <access token>`
(never a query string). Messages are JSON text frames with a `t` field:
  server -> program   {"t":"welcome","v":1,"heartbeat":5,"max_msg":262144}
  program -> server   {"t":"hello","v":1,"app":"virtual-captain/<ver>","platform":"<os>"}  (within HELLO_TIMEOUT)
  program -> server   {"t":"ping"}  every HEARTBEAT seconds;  server -> program {"t":"pong"}
  program -> server   {"t":"snapshot","sv":1,"seq":N,"d":{...}}  (only after the hello; see sanitize_snapshot)
  either side         {"t":"bye"}
The server closes a connection that is silent for STALE_AFTER seconds, after MAX_LIFETIME seconds,
and re-reads the authorisation (token family) every FAMILY_RECHECK seconds.
"""
from __future__ import annotations

import re

__all__ = [
    "PROTOCOL_VERSION", "SUPPORTED_VERSIONS", "HEARTBEAT", "STALE_AFTER", "MAX_MSG", "HELLO_TIMEOUT",
    "FAMILY_RECHECK", "MAX_LIFETIME", "WS_PATH", "CLOSE_REPLACED", "CLOSE_BAD_VERSION",
    "CLOSE_REVOKED", "CLOSE_TOO_BIG", "CLOSE_RESTART", "T_WELCOME", "T_HELLO", "T_PING", "T_PONG",
    "T_BYE", "welcome_message", "T_SNAPSHOT", "SNAPSHOT_VERSION", "SUPPORTED_SNAPSHOT_VERSIONS",
    "MAX_SNAPSHOT_TARGET", "SNAPSHOT_FIELDS", "HIDDEN_ROW_LABELS", "SnapshotError", "sanitize_snapshot",
]

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})

HEARTBEAT = 5          # seconds between the program's pings
STALE_AFTER = 12       # seconds of silence before the server drops the connection
MAX_MSG = 262144       # bytes per message (the uvicorn --ws-max-size must match)
HELLO_TIMEOUT = 5      # seconds to send the hello after the welcome
FAMILY_RECHECK = 10    # seconds between re-reads of the token family
MAX_LIFETIME = 86400   # a connection lives at most 24 h, then the program reconnects

WS_PATH = "/ws/agent"

CLOSE_REPLACED = 4001     # another instance of the same account took over
CLOSE_BAD_VERSION = 4002  # protocol version not supported
CLOSE_REVOKED = 4003      # authorisation revoked or expired
CLOSE_TOO_BIG = 4004      # message too big or too frequent
CLOSE_RESTART = 4005      # reserved: uvicorn closes with 1012 on a restart; the program reconnects on ANY close
#                           except 4001/4002/4003, so the code is informational only

T_WELCOME, T_HELLO, T_PING, T_PONG, T_BYE = "welcome", "hello", "ping", "pong", "bye"
T_SNAPSHOT = "snapshot"

SNAPSHOT_VERSION = 1
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({1})
MAX_SNAPSHOT_TARGET = 65536   # bytes: the program trims (waypoints, then monitor) above 96 KB, the tests keep the worst case here
MAX_DEPTH = 8
KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
HIDDEN_ROW_LABELS = frozenset({"position", "latitude", "longitude"})  # rows with these labels never leave the PC
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def welcome_message() -> dict:
    return {"t": T_WELCOME, "v": PROTOCOL_VERSION, "heartbeat": HEARTBEAT, "max_msg": MAX_MSG}


# ---------------------------------------------------------------- snapshot of the flight (allowlist)

class SnapshotError(ValueError):
    """The snapshot does not match the allowlist (wrong type or limit exceeded)."""


# Schema: ("bool",) ("int",) ("num",) ("str", max) ("opt", schema) = schema or null,
# ("list", item, max), ("pair", max_a, max_b) = [str, str], ("map", value, max_keys) = object with free keys
# matching KEY_RE, ("obj", {key: schema}) = object with known keys (unknown keys dropped).
def _s(n):
    return ("str", n)


_HINTS = ("list", _s(16), 8)
_ITEM = ("obj", {
    "id": _s(96), "text": _s(300), "response": _s(300), "type": _s(24), "status": _s(16), "kind": _s(24),
    "detail": _s(300), "show": ("bool",), "layout": _s(24), "expected": _s(120), "wx_hint": _s(16),
    "live": ("list", ("pair", 64, 120), 12),
    "inputs": ("list", ("obj", {"k": _s(32), "label": _s(80), "prefix": _s(24), "plan": ("bool",)}), 8),
    "fields": ("list", ("obj", {"k": _s(32), "label": _s(80), "src": _s(32)}), 8),
    "datis_hint": _HINTS, "atis_hint": _HINTS,
})
_ID_NAME = ("obj", {"id": _s(96), "name": _s(120)})
_WX = ("opt", ("obj", {"icao": _s(8), "line": _s(300), "raw": _s(300)}))

SNAPSHOT_FIELDS = {
    "phase": _s(16), "sim_ok": ("opt", ("bool",)), "sim_demo": ("bool",), "mode": _s(16), "checklist_mode": _s(16),
    "fields": ("map", _s(40), 40),
    "plan": ("obj", {
        "flight": ("opt", _s(32)), "aircraft": ("opt", _s(32)), "cruise_alt": ("opt", ("num",)),
        "distance_nm": ("opt", ("num",)), "ete": ("opt", _s(16)), "alternate": ("opt", _s(8)),
        "sid": ("opt", _s(32)), "star": ("opt", _s(32)), "route": ("opt", _s(1000)),
        "waypoints": ("list", ("obj", {"ident": ("opt", _s(32)), "via": ("opt", _s(32)), "alt": ("opt", ("num",))}), 300),
    }),
    "rwy_bad": ("obj", {"atis": ("bool",), "datis": ("bool",)}),
    "atis_warn": ("obj", {"atis": _s(80), "datis": _s(80)}),
    "wx_dep": _WX, "wx_dest": _WX,
    "flight": ("list", ("pair", 40, 200), 12),
    "pills": ("map", _s(40), 16),
    "pill_defs": ("list", ("pair", 32, 40), 16),
    "monitor": ("list", ("pair", 40, None), 24),   # [group, rows]: rows checked below (<=150 in all)
    "cl_phases": ("list", _ID_NAME, 40), "cl_phase": _s(96),
    "checklist": ("obj", {
        "bad": ("int",), "phase": _ID_NAME,
        "subphases": ("list", ("obj", {"id": _s(96), "name": _s(120), "items": ("list", _ITEM, 100)}), 30),
    }),
    "clearance": ("map", _s(400), 8),
    "log": ("list", _s(300), 30),
    "spoke_n": ("int",),
}
MAX_MONITOR_ROWS = 150
MAX_CHECKLIST_ITEMS = 100
_MONITOR_ROW = ("list", _s(120), 4)


def _clean_str(v, limit, path):
    if not isinstance(v, str):
        raise SnapshotError(f"{path}: expected text")
    if len(v) > limit:
        raise SnapshotError(f"{path}: text too long")
    return _CONTROL.sub(" ", v)


def _walk(v, schema, path, depth):
    if depth > MAX_DEPTH:
        raise SnapshotError(f"{path}: too deep")
    kind = schema[0]
    if kind == "opt":
        return None if v is None else _walk(v, schema[1], path, depth)
    if kind == "bool":
        if not isinstance(v, bool):
            raise SnapshotError(f"{path}: expected true/false")
        return v
    if kind in ("int", "num"):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind == "int" and not isinstance(v, int)):
            raise SnapshotError(f"{path}: expected a number")
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            raise SnapshotError(f"{path}: not finite")
        return v
    if kind == "str":
        return _clean_str(v, schema[1], path)
    if kind == "list":
        if not isinstance(v, list) or (schema[2] is not None and len(v) > schema[2]):
            raise SnapshotError(f"{path}: bad list")
        return [_walk(x, schema[1], f"{path}[]", depth + 1) for x in v]
    if kind == "pair":
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise SnapshotError(f"{path}: bad pair")
        return [_clean_str(v[0], schema[1], path), _clean_str(v[1], schema[2], path)] if schema[2] is not None \
            else [_clean_str(v[0], schema[1], path), v[1]]
    if kind == "map":
        if not isinstance(v, dict) or len(v) > schema[2]:
            raise SnapshotError(f"{path}: bad object")
        out = {}
        for k, x in v.items():
            if not isinstance(k, str) or not KEY_RE.match(k):
                raise SnapshotError(f"{path}: bad key")
            out[k] = _walk(x, schema[1], f"{path}.{k}", depth + 1)
        return out
    if kind == "obj":
        if not isinstance(v, dict):
            raise SnapshotError(f"{path}: expected an object")
        return {k: _walk(v[k], sub, f"{path}.{k}", depth + 1) for k, sub in schema[1].items() if k in v}
    raise SnapshotError("bad schema")  # pragma: no cover


def _hidden(label) -> bool:
    return isinstance(label, str) and label.strip().lower() in HIDDEN_ROW_LABELS


def sanitize_snapshot(data) -> dict:
    """Rebuilds a snapshot from the allowlist: unknown top-level keys and unknown keys inside known sections
    are dropped; a wrong type or a limit exceeded raises SnapshotError. Position, Latitude and Longitude rows
    are removed; waypoints carry no coordinates (the schema has no place for them)."""
    if not isinstance(data, dict):
        raise SnapshotError("snapshot is not an object")
    out = {}
    for key, schema in SNAPSHOT_FIELDS.items():
        if key not in data:
            continue
        if key == "monitor":
            out[key] = _monitor(data[key])
        elif key == "flight":
            raw = data[key]
            if not isinstance(raw, list):
                raise SnapshotError("flight: bad list")
            out[key] = _walk([r for r in raw if not (isinstance(r, (list, tuple)) and r and _hidden(r[0]))], schema, key, 0)
        else:
            out[key] = _walk(data[key], schema, key, 0)
    if "phase" not in out:
        raise SnapshotError("phase missing")
    cl = out.get("checklist")
    if cl is not None and sum(len(sp["items"]) for sp in cl.get("subphases", [])) > MAX_CHECKLIST_ITEMS:
        raise SnapshotError("checklist: too many items")
    return out


def _monitor(raw):
    if not isinstance(raw, list) or len(raw) > SNAPSHOT_FIELDS["monitor"][2]:
        raise SnapshotError("monitor: bad list")
    out, rows_total = [], 0
    for g in raw:
        if not isinstance(g, (list, tuple)) or len(g) != 2 or not isinstance(g[1], list):
            raise SnapshotError("monitor: bad group")
        name = _clean_str(g[0], 40, "monitor")
        rows = []
        for r in g[1]:
            if not isinstance(r, (list, tuple)) or not 1 <= len(r) <= 4:
                raise SnapshotError("monitor: bad row")
            if _hidden(r[0]):
                continue
            rows.append(_walk(list(r), _MONITOR_ROW, "monitor.row", 3))
        rows_total += len(rows)
        out.append([name, rows])
    if rows_total > MAX_MONITOR_ROWS:
        raise SnapshotError("monitor: too many rows")
    return out
