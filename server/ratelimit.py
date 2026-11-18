"""Sliding-window rate limits and login lockout, stored in the DB (survive restarts).
Keys (IPs, addresses) are stored only as keyed hashes."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Callable, Sequence, Tuple

from server.db import Database

DAY = 86400.0


class RateLimiter:
    def __init__(self, db: Database, clock: Callable[[], float], secret: str = ""):
        self.db, self.clock, self._secret = db, clock, secret.encode("utf-8")

    def key(self, raw: str) -> str:
        return hmac.new(self._secret, raw.encode("utf-8", "replace"), hashlib.sha256).hexdigest()

    def allow(self, bucket: str, raw_key: str, limits: Sequence[Tuple[int, float]]) -> bool:
        """True and records the event if every (max, seconds) window has room."""
        now, key = self.clock(), self.key(raw_key)
        with self.db.tx() as c:
            for maximum, seconds in limits:
                n = c.execute("SELECT COUNT(*) FROM rate_events WHERE bucket=? AND key=? AND ts>?",
                              (bucket, key, now - seconds)).fetchone()[0]
                if n >= maximum:
                    return False
            c.execute("INSERT INTO rate_events(bucket, key, ts) VALUES (?,?,?)", (bucket, key, now))
        return True

    def is_locked(self, raw_key: str, max_failures: int, window: float) -> bool:
        """Locked for `window` seconds after the N-th failure made within `window`."""
        now, key = self.clock(), self.key(raw_key)
        with self.db.tx() as c:
            rows = c.execute("SELECT ts FROM login_failures WHERE key=? ORDER BY ts DESC LIMIT ?",
                             (key, max_failures)).fetchall()
        if len(rows) < max_failures:
            return False
        return now - rows[0][0] < window and rows[0][0] - rows[-1][0] < window

    def record_failure(self, raw_key: str) -> None:
        now = self.clock()
        with self.db.tx() as c:
            c.execute("INSERT INTO login_failures(key, ts) VALUES (?,?)", (self.key(raw_key), now))

    def clear_failures(self, raw_key: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM login_failures WHERE key=?", (self.key(raw_key),))

    def purge(self) -> None:
        """Drop old events (called periodically by Accounts, not on every request)."""
        now = self.clock()
        with self.db.tx() as c:
            c.execute("DELETE FROM rate_events WHERE ts < ?", (now - 2 * DAY,))
            c.execute("DELETE FROM login_failures WHERE ts < ?", (now - 2 * DAY,))


def group_ip(text: str) -> str:
    """IPv4 as is; IPv6 grouped by /64 (one subscriber owns a whole /64); other text unchanged."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return text
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{text}/64", strict=False))
    return str(ip)


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True
