"""Audit log (journald only). Events carry a numeric user id or a short keyed hash, never an
email address, IP, token or device name."""
from __future__ import annotations

import logging

log = logging.getLogger("virtual_captain.audit")


def audit(event: str, user_id: int | None = None, ref: str = "") -> None:
    """`ref` is only ever a short HMAC (RateLimiter.key(...)[:8]) for events without a user id."""
    if user_id is not None:
        log.info("%s uid=%d", event, int(user_id))
    elif ref:
        log.info("%s ref=%s", event, ref[:8])
    else:
        log.info("%s", event)
