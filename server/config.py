"""SMTP configuration read from the environment (never from files in the repo)."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping


ADDR_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
"""Plain ASCII address: no display name, no lists, no unicode local part."""


class MailConfigError(Exception):
    """Configuration missing or invalid. The message names variables, never values."""


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    mail_from: str
    from_name: str = ""
    timeout: float = 15.0
    envelope_from: str = ""

    def __repr__(self) -> str:  # explicit: the password is never shown
        return (f"SmtpConfig(host={self.host!r}, port={self.port}, "
                f"user=<set>, password=<hidden>, mail_from=<set>)")

    __str__ = __repr__


def _has_newline(value: str) -> bool:
    return "\r" in value or "\n" in value


def load_smtp_config(env: Mapping[str, str] = os.environ) -> SmtpConfig:
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
    missing = [n for n in required if not (env.get(n) or "").strip()]
    if missing:
        raise MailConfigError("missing variables: " + ", ".join(missing))
    try:
        port = int(env["SMTP_PORT"].strip())
        timeout = float((env.get("SMTP_TIMEOUT") or "15").strip())
    except ValueError:
        raise MailConfigError("SMTP_PORT / SMTP_TIMEOUT must be numbers") from None
    if not (0 < port < 65536) or not math.isfinite(timeout) or timeout <= 0:
        raise MailConfigError("SMTP_PORT / SMTP_TIMEOUT out of range")
    password = env["SMTP_PASSWORD"]
    if not password.isascii():
        raise MailConfigError("SMTP_PASSWORD must be plain ASCII")
    if _has_newline(password):
        raise MailConfigError("SMTP_PASSWORD contains a line break")
    cfg = SmtpConfig(
        host=env["SMTP_HOST"].strip(),
        port=port,
        user=env["SMTP_USER"].strip(),
        password=password,
        mail_from=env["MAIL_FROM"].strip(),
        from_name=(env.get("MAIL_FROM_NAME") or "").strip(),
        timeout=timeout,
        envelope_from=(env.get("MAIL_ENVELOPE_FROM") or "").strip(),
    )
    for name, value in (("SMTP_HOST", cfg.host), ("SMTP_USER", cfg.user),
                        ("MAIL_FROM", cfg.mail_from), ("MAIL_FROM_NAME", cfg.from_name),
                        ("MAIL_ENVELOPE_FROM", cfg.envelope_from)):
        if _has_newline(value):
            raise MailConfigError(f"{name} contains a line break")
    if not ADDR_RE.match(cfg.mail_from) or (cfg.envelope_from and not ADDR_RE.match(cfg.envelope_from)):
        raise MailConfigError("MAIL_FROM / MAIL_ENVELOPE_FROM must be plain addresses")
    return cfg
