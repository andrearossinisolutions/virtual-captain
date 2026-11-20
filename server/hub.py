"""In-memory registry of the connected programs: one per account (user_id).

Lives in the RAM of the single uvicorn worker (--workers 1 is mandatory): no lock is needed
because every access happens on the one event loop, without awaiting in between."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from server import wire


@dataclass
class AgentConn:
    websocket: Any
    family_id: str
    connected_at: float
    last_seen: float
    extra: dict = field(default_factory=dict)
    # last flight snapshot: RAM only, dies with the connection (never written anywhere, never logged)
    snapshot_text: Optional[str] = None   # sanitised view, serialised once (compact JSON, ASCII): the join of the sections
    # sections of the view: key -> (compact JSON text of that section or None = tombstone of a removed one, ver);
    # ver = seq of the message that wrote it. No history: only the current state (U4, for the SSE diff).
    sections: dict = field(default_factory=dict)
    view: Optional[dict] = None           # the same state as parsed values (the base of the next delta)
    user_id: Optional[int] = None         # set by Hub.register (subscribers are notified by account)
    seq: int = 0
    snap_at: float = 0.0
    spoke_n: Optional[int] = None
    epoch: int = 0                        # unique per connection (hub counter): a seq that restarts at 1 never looks 'unchanged'
    spoke_at: Optional[float] = None      # when spoke_n last changed (the program's own spoke_ago is not sent)
    caps: tuple = ()                      # capabilities announced in the hello (e.g. ("cmd",))
    cmd_version: int = 0                  # "cv" of the hello: 0 = a program that knows nothing about remote commands
    busy: bool = False                    # a command is in flight on this connection (one at a time)
    pending: Optional[tuple] = None       # (id, future) of that command; only a result with this id is accepted
    _send_lock: Optional[asyncio.Lock] = None

    def send_lock(self) -> asyncio.Lock:
        """Serialises the sends on the socket (pong, commands). Created on first use, inside the running loop."""
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        return self._send_lock


class Hub:
    def __init__(self, stale_after: float = wire.STALE_AFTER,
                 monotonic: Callable[[], float] = time.monotonic):
        self.stale_after, self.monotonic = stale_after, monotonic
        self._agents: Dict[int, AgentConn] = {}
        # random start per process: after a restart a browser still holding an old epoch never sees a collision
        self._epoch = secrets.randbits(30)
        self._subs: Dict[int, set] = {}   # user_id -> asyncio.Event of the subscribers (SSE, step C4)

    def new_conn(self, websocket, family_id: str) -> AgentConn:
        now = self.monotonic()
        self._epoch += 1
        return AgentConn(websocket, family_id, now, now, epoch=self._epoch)

    def register(self, user_id: int, conn: AgentConn) -> Optional[AgentConn]:
        """Becomes the account's connection; returns the previous one (to close with 4001)."""
        old = self._agents.get(user_id)
        conn.user_id = user_id
        self._agents[user_id] = conn
        self._notify(user_id)
        return old

    def unregister(self, user_id: int, conn: AgentConn) -> bool:
        """Removes `conn` only if it is still the current one (a replaced one must not evict its successor)."""
        if self._agents.get(user_id) is conn:
            del self._agents[user_id]
            self._notify(user_id)
            return True
        return False

    def touch(self, conn: AgentConn) -> None:
        conn.last_seen = self.monotonic()

    def status(self, user_id: int) -> bool:
        conn = self._agents.get(user_id)
        return conn is not None and self.monotonic() - conn.last_seen < self.stale_after

    # ---- subscribers (the SSE endpoint of C4 uses them): one Event per subscriber, set at every change of the account
    def subscribe(self, user_id: int) -> asyncio.Event:
        ev = asyncio.Event()
        self._subs.setdefault(user_id, set()).add(ev)
        return ev

    def unsubscribe(self, user_id: int, ev: asyncio.Event) -> None:
        subs = self._subs.get(user_id)
        if subs is not None:
            subs.discard(ev)
            if not subs:
                del self._subs[user_id]

    def _notify(self, user_id: Optional[int]) -> None:
        for ev in self._subs.get(user_id, ()):
            ev.set()

    @staticmethod
    def _dump(value) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def join_sections(sections: dict) -> str:
        """The whole snapshot from the sections (tombstones skipped), keys sorted: byte for byte what
        `json.dumps(view, separators=(",", ":"), sort_keys=True)` gives (keys are [a-z0-9_], so no escaping)."""
        return "{" + ",".join('"%s":%s' % (k, sections[k][0]) for k in sorted(sections) if sections[k][0] is not None) + "}"

    @staticmethod
    def sections_size(sections: dict) -> int:
        return sum(len(t) for t, _ in sections.values() if t is not None)

    def _commit(self, conn: AgentConn, seq: int, view: dict, sections: dict) -> None:
        now = self.monotonic()
        spoke_n = view.get("spoke_n")
        if conn.snapshot_text is not None and spoke_n != conn.spoke_n:
            conn.spoke_at = now  # a first snapshot has no known age: the page does not show a stale phrase as new
        conn.spoke_n = spoke_n
        conn.seq, conn.snap_at = seq, now
        conn.sections, conn.view = sections, view
        conn.snapshot_text = self.join_sections(sections)
        self._notify(conn.user_id)

    def store_snapshot(self, conn: AgentConn, seq: int, view: dict) -> bool:
        """A full snapshot replaces the whole state; sections that were there and are gone become tombstones.
        False, and nothing changes, when the state exceeds MAX_STATE_BYTES (the same cap as for a delta)."""
        sections = {k: (self._dump(v), seq) for k, v in view.items()}
        if self.sections_size(sections) > wire.MAX_STATE_BYTES:
            return False
        for k, (text, ver) in conn.sections.items():
            if k not in sections:
                sections[k] = (None, seq) if text is not None else (None, ver)
        self._commit(conn, seq, view, sections)
        return True

    def store_delta(self, conn: AgentConn, seq: int, view: dict, changed, removed) -> bool:
        """A delta already applied by `wire.apply_delta` (`view` = the new whole state; `changed` / `removed` = the keys of
        the message). False, and nothing changes, when the new state would exceed MAX_STATE_BYTES."""
        sections = dict(conn.sections)
        for k in changed:
            if k in view:
                sections[k] = (self._dump(view[k]), seq)
        for k in removed:
            if k in sections and sections[k][0] is not None and k not in view:
                sections[k] = (None, seq)   # an already removed section keeps the ver of its first tombstone
        if self.sections_size(sections) > wire.MAX_STATE_BYTES:
            return False
        self._commit(conn, seq, view, sections)
        return True

    def flight_state(self, user_id: int) -> Optional[dict]:
        """State for the browser of `user_id`: None when no live program (absent or silent), else
        {"seq", "age" (seconds since the program was last heard: snapshot or heartbeat), "spoke_ago", "text", "cmd"}."""
        conn = self._agents.get(user_id)
        now = self.monotonic()
        if conn is None or now - conn.last_seen >= self.stale_after:
            return None
        age = max(0.0, now - max(conn.snap_at, conn.last_seen)) if conn.snapshot_text is not None else None
        spoke_ago = None if conn.spoke_at is None else max(0.0, now - conn.spoke_at)
        return {"seq": conn.seq, "epoch": conn.epoch, "age": age, "spoke_ago": spoke_ago, "text": conn.snapshot_text,
                "cmd": "cmd" in conn.caps}

    def stream_state(self, user_id: int) -> Optional[tuple]:
        """For the SSE stream: (epoch, seq, sections) of the live program of `user_id`, None when absent or silent
        or with no snapshot yet. `sections` is the live dict (key -> (text or None, ver)): read it without awaiting."""
        conn = self._agents.get(user_id)
        if conn is None or self.monotonic() - conn.last_seen >= self.stale_after or conn.snapshot_text is None:
            return None
        return conn.epoch, conn.seq, conn.sections

    def resolve_result(self, conn: AgentConn, res: Optional[dict]) -> None:
        """A sanitised result of the program: accepted only for THIS connection's pending command (id equal),
        unknown, repeated or late ones are ignored."""
        if res is None or conn.pending is None or conn.pending[0] != res.get("id"):
            return
        fut = conn.pending[1]
        conn.pending = None
        if not fut.done():
            fut.set_result(res)

    def drop_pending(self, conn: AgentConn) -> None:
        """The connection is gone: whoever waits gets 'offline'."""
        if conn.pending is not None and not conn.pending[1].done():
            conn.pending[1].set_result({"offline": True})
        conn.pending = None

    async def send_command(self, user_id: int, name: str, data: dict, timeout: float,
                           send_timeout: float = 5.0) -> tuple:
        """Sends a command to the program of `user_id` and waits for its result.
        Returns (status, result): status in offline | unsupported | disabled | busy | timeout | result.
        The pending slot is always freed, even if the browser that asked goes away (task cancelled)."""
        conn = self._agents.get(user_id)
        if conn is None or self.monotonic() - conn.last_seen >= self.stale_after:
            return "offline", None
        if "cmd" not in conn.caps:
            return ("disabled" if conn.cmd_version else "unsupported"), None
        if conn.busy:
            return "busy", None
        cmd_id = secrets.token_hex(16)
        fut = asyncio.get_running_loop().create_future()
        conn.busy, conn.pending = True, (cmd_id, fut)
        try:
            text = json.dumps({"t": "cmd", "id": cmd_id, "name": name, "d": data}, separators=(",", ":"))
            try:
                async with conn.send_lock():
                    await asyncio.wait_for(conn.websocket.send_text(text), send_timeout)
            except Exception:  # noqa: BLE001  (the socket is dying: never log what was in the command)
                return "offline", None
            try:
                res = await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                return "timeout", None
            if res.get("offline"):
                return "offline", None
            return "result", res
        finally:
            if conn.pending is not None and conn.pending[0] == cmd_id:
                conn.pending = None
            conn.busy = False

    def is_online(self, user_id: int, family_id: str) -> bool:
        """True while the live connection of `user_id` belongs to this authorisation family."""
        conn = self._agents.get(user_id)
        return (conn is not None and conn.family_id == family_id
                and self.monotonic() - conn.last_seen < self.stale_after)

    async def _close_conn(self, conn: AgentConn, code: int, timeout: float) -> None:
        self.drop_pending(conn)  # whoever waits for a result gets "offline" right away
        try:
            await asyncio.wait_for(conn.websocket.close(code=code), timeout)
        except Exception:  # noqa: BLE001  (already closed, or too slow: give up)
            pass

    async def close_family(self, user_id: int, family_id: str, code: int, timeout: float = 5.0) -> None:
        """Closes the live connection of `user_id` if it belongs to `family_id` (no DB access)."""
        conn = self._agents.get(user_id)
        if conn is not None and conn.family_id == family_id:
            await self._close_conn(conn, code, timeout)

    async def close_user(self, user_id: int, code: int, timeout: float = 5.0) -> None:
        """Closes the live connection of `user_id`, whatever its family (no DB access)."""
        conn = self._agents.get(user_id)
        if conn is not None:
            await self._close_conn(conn, code, timeout)

    def count(self) -> int:
        return len(self._agents)
