"""Windows firewall: no longer used (Tailscale removed, the page listens on 127.0.0.1 only).

Kept only so that old imports do not break; nothing is created, no prompt, no administrator permission.
"""
from __future__ import annotations


def ensure(port: int) -> bool:
    """No firewall rule is needed for a page bound to 127.0.0.1."""
    return True
