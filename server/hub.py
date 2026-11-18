"""In-memory registry of the connected programs: one per account (user_id).

Lives in the RAM of the single uvicorn worker (--workers 1 is mandatory): no lock is needed
because every access happens on the one event loop, without awaiting in between."""
from __future__ import annotations

import json
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
    snapshot_text: Optional[str] = None   # sanitised view, serialised once (compact JSON, ASCII)
    seq: int = 0
    snap_at: float = 0.0
    spoke_n: Optional[int] = None
    epoch: int = 0                        # unique per connection (hub counter): a seq that restarts at 1 never looks 'unchanged'
    spoke_at: Optional[float] = None      # when spoke_n last changed (the program's own spoke_ago is not sent)


class Hub:
    def __init__(self, stale_after: float = wire.STALE_AFTER,
                 monotonic: Callable[[], float] = time.monotonic):
        self.stale_after, self.monotonic = stale_after, monotonic
        self._agents: Dict[int, AgentConn] = {}
        self._epoch = 0

    def new_conn(self, websocket, family_id: str) -> AgentConn:
        now = self.monotonic()
        self._epoch += 1
        return AgentConn(websocket, family_id, now, now, epoch=self._epoch)

    def register(self, user_id: int, conn: AgentConn) -> Optional[AgentConn]:
        """Becomes the account's connection; returns the previous one (to close with 4001)."""
        old = self._agents.get(user_id)
        self._agents[user_id] = conn
        return old

    def unregister(self, user_id: int, conn: AgentConn) -> bool:
        """Removes `conn` only if it is still the current one (a replaced one must not evict its successor)."""
        if self._agents.get(user_id) is conn:
            del self._agents[user_id]
            return True
        return False

    def touch(self, conn: AgentConn) -> None:
        conn.last_seen = self.monotonic()

    def status(self, user_id: int) -> bool:
        conn = self._agents.get(user_id)
        return conn is not None and self.monotonic() - conn.last_seen < self.stale_after

    def store_snapshot(self, conn: AgentConn, seq: int, view: dict) -> None:
        now = self.monotonic()
        spoke_n = view.get("spoke_n")
        if conn.snapshot_text is not None and spoke_n != conn.spoke_n:
            conn.spoke_at = now  # a first snapshot has no known age: the page does not show a stale phrase as new
        conn.spoke_n = spoke_n
        conn.seq, conn.snap_at = seq, now
        conn.snapshot_text = json.dumps(view, separators=(",", ":"), sort_keys=True)

    def flight_state(self, user_id: int) -> Optional[dict]:
        """State for the browser of `user_id`: None when no live program (absent or silent), else
        {"seq", "age" (seconds since the program was last heard: snapshot or heartbeat), "spoke_ago", "text"}."""
        conn = self._agents.get(user_id)
        now = self.monotonic()
        if conn is None or now - conn.last_seen >= self.stale_after:
            return None
        age = max(0.0, now - max(conn.snap_at, conn.last_seen)) if conn.snapshot_text is not None else None
        spoke_ago = None if conn.spoke_at is None else max(0.0, now - conn.spoke_at)
        return {"seq": conn.seq, "epoch": conn.epoch, "age": age, "spoke_ago": spoke_ago, "text": conn.snapshot_text}

    def count(self) -> int:
        return len(self._agents)
