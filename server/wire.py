"""Wire contract between the PC program and the public server (constants and the pure snapshot allowlist, stdlib only).

The program connects to `wss://<base>/ws/agent` with `Authorization: Bearer <access token>`
(never a query string). Messages are JSON text frames with a `t` field:
  server -> program   {"t":"welcome","v":1,"heartbeat":5,"max_msg":262144}
  program -> server   {"t":"hello","v":1,"app":"virtual-captain/<ver>","platform":"<os>"}  (within HELLO_TIMEOUT)
  program -> server   {"t":"ping"}  every HEARTBEAT seconds;  server -> program {"t":"pong"}
  program -> server   {"t":"snapshot","sv":1,"seq":N,"d":{...}}  (only after the hello; see sanitize_snapshot)
  either side         {"t":"bye"}
Remote commands (step U3): the hello may add `"caps":["cmd"]` and `"cv":1`;
  server -> program   {"t":"cmd","id":<32 hex>,"name":<ENABLED_COMMANDS>,"d":{...}}  (only if the program has the cap)
  program -> server   {"t":"result","id":...,"ok":true} or {"t":"result","id":...,"ok":false,"code":<RESULT_CODES>,
                       "msg":<text, only for "invalid">,"reason":<LOAD_REASONS, only for load-plan>}
The program re-validates every command with `validate_command` (it does not trust the server) and the server maps the
result codes to fixed texts (the program's text never reaches the browser).
The server closes a connection that is silent for STALE_AFTER seconds, after MAX_LIFETIME seconds,
and re-reads the authorisation (token family) every FAMILY_RECHECK seconds.
"""
from __future__ import annotations

import json
import re

__all__ = [
    "PROTOCOL_VERSION", "SUPPORTED_VERSIONS", "HEARTBEAT", "STALE_AFTER", "MAX_MSG", "HELLO_TIMEOUT",
    "FAMILY_RECHECK", "MAX_LIFETIME", "WS_PATH", "CLOSE_REPLACED", "CLOSE_BAD_VERSION",
    "CLOSE_REVOKED", "CLOSE_TOO_BIG", "CLOSE_RESTART", "T_WELCOME", "T_HELLO", "T_PING", "T_PONG",
    "T_BYE", "welcome_message", "T_SNAPSHOT", "SNAPSHOT_VERSION", "SUPPORTED_SNAPSHOT_VERSIONS",
    "MAX_SNAPSHOT_TARGET", "SNAPSHOT_FIELDS", "HIDDEN_ROW_LABELS", "SnapshotError", "sanitize_snapshot",
    "T_CMD", "T_RESULT", "CAP_CMD", "COMMAND_VERSION", "REMOTE_COMMANDS", "LOCAL_ONLY", "ENABLED_COMMANDS",
    "CMD_TIMEOUT", "cmd_timeout", "MAX_CMD_BYTES", "MAX_CMD_DATA", "MAX_RESULT_BYTES", "MAX_CAPS", "CMD_ID_RE",
    "RESULT_CODES", "LOAD_REASONS", "MAX_RESULT_MSG", "SIMBRIEF_RE", "FIELD_KEYS", "PLAN_RESET", "CLR_KEYS", "CLR_MAX",
    "MODES", "CL_MODES", "CommandError", "validate_command", "sanitize_result", "parse_caps",
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
T_CMD, T_RESULT = "cmd", "result"

SNAPSHOT_VERSION = 1
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({1})
MAX_SNAPSHOT_TARGET = 65536   # bytes: the program trims (waypoints, then monitor) above 96 KB, the tests keep the worst case here
MAX_DEPTH = 8
KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
HIDDEN_ROW_LABELS = frozenset({"latitude", "longitude"})  # rows with these labels never leave the PC
# The "Position" row of flight[] is the only place a position may leave the PC: formatted text with 4 decimals, never numbers.
POSITION_LABEL = "position"
POSITION_DASH = "\u2014"
POSITION_RE = re.compile(r"(\d{1,2})\.\d{4}\u00b0 [NS]  (\d{1,3})\.\d{4}\u00b0 [EW]", re.ASCII)
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
    "detail": _s(300), "gate": _s(120), "show": ("bool",), "layout": _s(24), "expected": _s(120), "wx_hint": _s(16),
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
    # extended in U3 (briefing view on the site)
    "simbrief": _s(40), "info": _s(300), "labels": ("list", ("pair", 32, 40), 24),
    "modes": ("list", _s(16), 4), "cl_modes": ("list", _s(16), 4), "rev": ("int",), "dll_missing": ("bool",),
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


def _position_ok(value) -> bool:
    """A Position value is either the dash or the formatted text with degrees in range (never raw numbers)."""
    if not isinstance(value, str) or len(value) > 40:
        return False
    if value == POSITION_DASH:
        return True
    m = POSITION_RE.fullmatch(value)
    return bool(m) and int(m.group(1)) <= 90 and int(m.group(2)) <= 180


def _flight_rows(raw, phase) -> list:
    """Rows of flight[] for the site: Latitude/Longitude rows dropped; Position kept only if well formed and the
    program is not in the briefing; a malformed Position row is dropped (no error)."""
    rows = []
    for r in raw:
        if isinstance(r, (list, tuple)) and r and _hidden(r[0]):
            continue
        if isinstance(r, (list, tuple)) and r and isinstance(r[0], str) and r[0].strip().lower() == POSITION_LABEL:
            if phase == "briefing" or len(r) != 2 or r[0] != "Position" or not _position_ok(r[1]):
                continue
        rows.append(r)
    return rows


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
            out[key] = _walk(_flight_rows(raw, data.get("phase")), schema, key, 0)
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
            if _hidden(r[0]) or (isinstance(r[0], str) and r[0].strip().lower() == POSITION_LABEL):
                continue  # the monitor never carries a position (only flight[] does, validated)
            rows.append(_walk(list(r), _MONITOR_ROW, "monitor.row", 3))
        rows_total += len(rows)
        out.append([name, rows])
    if rows_total > MAX_MONITOR_ROWS:
        raise SnapshotError("monitor: too many rows")
    return out


# ---------------------------------------------------------------- remote commands (U3)

CAP_CMD = "cmd"          # capability announced in the hello: the program accepts remote commands
COMMAND_VERSION = 1      # "cv" of the hello (no PROTOCOL_VERSION bump)

# Commands a browser may ever send through the server, and the ones switched on today (a subset).
REMOTE_COMMANDS = ("fields", "clearance", "checklist-phase", "load-plan", "phase", "sim-retry")
# Commands that touch the local machine: they stay on the PC for good, the server answers 404.
LOCAL_ONLY = ("simdll-fetch", "thrust-cal")
ENABLED_COMMANDS = ("fields", "clearance", "checklist-phase", "phase", "load-plan", "sim-retry")

CMD_TIMEOUT = {"default": 8.0, "load-plan": 20.0}   # seconds the server waits for the result
MAX_CMD_BYTES = 4096      # a whole cmd message
MAX_CMD_DATA = 2048       # the "d" of a command
MAX_RESULT_BYTES = 1024   # a whole result message
MAX_RESULT_MSG = 120
MAX_CAPS, MAX_CAP_LEN = 8, 16
CMD_ID_RE = re.compile(r"[0-9a-f]{32}")  # always used with fullmatch (a "$" would accept a trailing newline)
RESULT_CODES = ("invalid", "busy", "failed", "disabled")
LOAD_REASONS = ("network", "not_found", "bad_plan")  # fixed vocabulary for a failed load-plan

# SimBrief Pilot ID or username (shared with the account settings).
SIMBRIEF_RE = re.compile(r"[A-Za-z0-9 ._-]{1,40}", re.ASCII)

# Copies of the State constants (state.py is not importable by the server): test_wire checks they are identical.
FIELD_KEYS = ("origin", "destination", "dep_time", "runway", "ta", "thr_red_agl", "accel_agl", "v1", "vr", "v2", "flap",
              "fob", "zfw", "zfwcg", "taxiway", "atis_letter", "atis_rwy", "atis_wind", "atis_qnh", "atis_tl",
              "datis_letter", "datis_rwy", "datis_wind", "datis_qnh")
PLAN_RESET = ("runway", "ta", "v1", "vr", "v2", "flap", "fob", "zfw")
CLR_KEYS = ("c", "w", "r", "a", "f", "t")
CLR_MAX = {"c": 40, "w": 12, "r": 400, "a": 20, "f": 20, "t": 4}
MODES = ("normal", "training")
CL_MODES = ("complete", "simplified")
_FIELD_MAX = 40
_PHASE_MAX = 96


def cmd_timeout(name: str) -> float:
    return CMD_TIMEOUT.get(name, CMD_TIMEOUT["default"])


class CommandError(ValueError):
    """The command does not match its schema (fixed, short messages: safe to show)."""


def _text(v, limit: int, what: str) -> str:
    if not isinstance(v, str):
        raise CommandError(f"{what}: expected text")
    if len(v) > limit:
        raise CommandError(f"{what}: text too long")
    if _CONTROL.search(v):
        raise CommandError(f"{what}: invalid characters")
    return v


def _only(data: dict, allowed) -> None:
    for k in data:
        if k not in allowed:
            raise CommandError("unknown field")


def _v_fields(d: dict) -> dict:
    out: dict = {}
    for k, v in d.items():
        if k in FIELD_KEYS:
            out[k] = _text(v, _FIELD_MAX, k)
        elif k == "mode":
            if v not in MODES or not isinstance(v, str):
                raise CommandError("mode: not allowed")
            out[k] = v
        elif k == "checklist_mode":
            if v not in CL_MODES or not isinstance(v, str):
                raise CommandError("checklist_mode: not allowed")
            out[k] = v
        elif k == "reset":
            if not isinstance(v, list) or len(v) > 8 or any(not isinstance(x, str) or x not in PLAN_RESET for x in v):
                raise CommandError("reset: not allowed")
            out[k] = list(v)
        else:
            raise CommandError("unknown field")
    return out


def _v_clearance(d: dict) -> dict:
    if "reset" in d:
        if d != {"reset": True} or d["reset"] is not True:
            raise CommandError("reset must be alone and true")
        return {"reset": True}
    if not d:
        raise CommandError("nothing to change")
    out = {}
    for k, v in d.items():
        if k not in CLR_KEYS:
            raise CommandError("unknown field")
        out[k] = _text(v, CLR_MAX[k], k)
        if k == "t" and not re.fullmatch(r"[0-7]{0,4}", out[k]):
            raise CommandError("squawk must be up to 4 digits 0-7")
    return out


def _v_cl_phase(d: dict) -> dict:
    if len(d) != 1:
        raise CommandError("exactly one of phase or step")
    if "phase" in d:
        return {"phase": _text(d["phase"], _PHASE_MAX, "phase")}
    if "step" in d:
        v = d["step"]
        if isinstance(v, bool) or not isinstance(v, int) or v not in (1, -1):
            raise CommandError("step must be 1 or -1")
        return {"step": v}
    raise CommandError("unknown field")


def _v_phase(d: dict) -> dict:
    if d == {"phase": "flying"} and d["phase"] == "flying":
        return {"phase": "flying"}
    if len(d) == 2 and d.get("phase") == "briefing" and d.get("confirm") is True:
        return {"phase": "briefing", "confirm": True}
    raise CommandError("phase not allowed")


def _v_load_plan(d: dict) -> dict:
    _only(d, ("simbrief",))
    if "simbrief" not in d:
        return {}
    v = d["simbrief"]
    if (not isinstance(v, str) or not SIMBRIEF_RE.fullmatch(v) or not v.strip()
            or not SIMBRIEF_RE.fullmatch(v.strip())):  # load_plan strips: a blank value would erase the saved ID
        raise CommandError("simbrief: not allowed")
    return {"simbrief": v}


def _v_empty(d: dict) -> dict:
    if d:
        raise CommandError("no data expected")
    return {}


_VALIDATORS = {"fields": _v_fields, "clearance": _v_clearance, "checklist-phase": _v_cl_phase, "phase": _v_phase,
               "load-plan": _v_load_plan, "sim-retry": _v_empty}


def validate_command(name, data) -> dict:
    """Clean copy of the command data, or CommandError. Pure and stdlib only: used by the server AND by the program
    (which never trusts the server). Unknown keys are errors, types are exact (a bool is not an int)."""
    if not isinstance(name, str) or name not in ENABLED_COMMANDS:
        raise CommandError("unknown command")
    if not isinstance(data, dict):
        raise CommandError("data must be an object")
    try:
        size = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        raise CommandError("data not serialisable") from None
    if size > MAX_CMD_DATA:
        raise CommandError("data too big")
    return _VALIDATORS[name](data)


def parse_caps(data) -> tuple:
    """Capabilities of a hello (list of up to 8 short texts); anything else counts as none."""
    caps = data.get("caps") if isinstance(data, dict) else None
    if (not isinstance(caps, list) or len(caps) > MAX_CAPS
            or any(not isinstance(c, str) or not 1 <= len(c) <= MAX_CAP_LEN for c in caps)):
        return ()
    return tuple(caps)


def sanitize_result(data, expected_id=None):
    """Clean result from the program, or None when malformed. Only the fixed vocabulary survives (code, reason);
    `msg` is kept (<=120 characters) but the server never forwards it."""
    if not isinstance(data, dict) or data.get("t") != T_RESULT:
        return None
    rid = data.get("id")
    if not isinstance(rid, str) or not CMD_ID_RE.fullmatch(rid) or (expected_id is not None and rid != expected_id):
        return None
    ok = data.get("ok")
    if not isinstance(ok, bool):
        return None
    out: dict = {"id": rid, "ok": ok}
    if ok:
        return out
    code = data.get("code")
    if not isinstance(code, str) or code not in RESULT_CODES:
        return None
    out["code"] = code
    msg = data.get("msg")
    if isinstance(msg, str) and len(msg) <= MAX_RESULT_MSG:
        out["msg"] = _CONTROL.sub(" ", msg)
    reason = data.get("reason")
    if isinstance(reason, str) and reason in LOAD_REASONS:
        out["reason"] = reason
    return out
