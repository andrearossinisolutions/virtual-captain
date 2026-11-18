"""In-memory registry of the connected programs: one per account (user_id).

Lives in the RAM of the single uvicorn worker (--workers 1 is mandatory): no lock is needed
because every access happens on the one event loop, without awaiting in between."""
from __future__ import annotations

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


class Hub:
    def __init__(self, stale_after: float = wire.STALE_AFTER,
                 monotonic: Callable[[], float] = time.monotonic):
        self.stale_after, self.monotonic = stale_after, monotonic
        self._agents: Dict[int, AgentConn] = {}

    def new_conn(self, websocket, family_id: str) -> AgentConn:
        now = self.monotonic()
        return AgentConn(websocket, family_id, now, now)

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

    def count(self) -> int:
        return len(self._agents)
