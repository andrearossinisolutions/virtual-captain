"""SMTP configuration read from the environment (never from files in the repo)."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urlparse


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


DEFAULT_DB_PATH = "/var/www/virtual-captain/shared/virtual-captain.db"


class AppConfigError(Exception):
    """Invalid application configuration. Names variables, never values."""


@dataclass(frozen=True)
class AppConfig:
    public_base_url: str = ""
    db_path: str = DEFAULT_DB_PATH
    secret_key: str = field(default="", repr=False)
    ip_hourly: int = 5
    ip_daily: int = 20
    mail_addr_hourly: int = 3
    mail_daily: int = 200
    login_max_failures: int = 5
    lockout_seconds: int = 900
    login_ip_per_15min: int = 30
    preview_ip_hourly: int = 20
    device_start_hourly: int = 10
    device_start_daily: int = 30
    device_poll_ip_minute: int = 60
    device_token_ip_minute: int = 30
    device_code_ttl: int = 600
    device_interval: int = 5
    device_access_ttl: int = 600
    device_refresh_ttl: int = 90 * 86400
    device_lookup_max_failures: int = 5
    device_lookup_lock_seconds: int = 900
    agent_max: int = 50                  # connected programs at once (RAM)
    agent_ip_per_minute: int = 30        # handshakes per IP per minute
    agent_msgs_per_second: int = 20      # messages per second per connection
    agent_stale_after: float = 12.0
    agent_hello_timeout: float = 5.0
    agent_family_recheck: float = 10.0
    agent_max_lifetime: float = 86400.0
    account_user_minute: int = 10        # signed-in writes/hashes per user per minute (RAM)
    account_ip_minute: int = 60          # the same, per IP
    hash_queue_max: int = 8              # hashes waiting or running; above it: 503
    remote_commands: bool = True         # VC_REMOTE_COMMANDS=off: kill switch, the command route answers 503


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise AppConfigError(f"{name} must be a number") from None
    if value < 1:
        raise AppConfigError(f"{name} must be positive")
    return value


def check_base_url(base: str) -> None:
    if _has_newline(base) or "#" in base or "?" in base or base.count("/") != 2:
        raise AppConfigError("PUBLIC_BASE_URL must be an origin (scheme and host only)")
    try:
        parts = urlparse(base)
        host = parts.hostname
    except ValueError:
        raise AppConfigError("PUBLIC_BASE_URL is not a valid URL") from None
    local = parts.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")
    if not (parts.scheme == "https" and host) and not local:
        raise AppConfigError("PUBLIC_BASE_URL must be an https origin")


def check_secret(base: str, secret: str) -> None:
    if base and not secret:
        raise AppConfigError("SECRET_KEY is required when PUBLIC_BASE_URL is set "
                             "(set SECRET_KEY first, then PUBLIC_BASE_URL)")
    if secret and len(secret) < 32:
        raise AppConfigError("SECRET_KEY too short (min 32 characters)")


def load_app_config(env: Mapping[str, str] = os.environ) -> AppConfig:
    base = (env.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    secret = (env.get("SECRET_KEY") or "").strip()
    if base:
        check_base_url(base)
    check_secret(base, secret)
    return AppConfig(
        public_base_url=base,
        db_path=(env.get("DB_PATH") or "").strip() or DEFAULT_DB_PATH,
        secret_key=secret,
        ip_hourly=_int(env, "RATE_IP_HOURLY", 5),
        ip_daily=_int(env, "RATE_IP_DAILY", 20),
        mail_addr_hourly=_int(env, "RATE_MAIL_ADDR_HOURLY", 3),
        mail_daily=_int(env, "RATE_MAIL_DAILY", 200),
        login_max_failures=_int(env, "LOGIN_MAX_FAILURES", 5),
        lockout_seconds=_int(env, "LOCKOUT_MINUTES", 15) * 60,
        login_ip_per_15min=_int(env, "RATE_LOGIN_IP_15MIN", 30),
        preview_ip_hourly=_int(env, "RATE_PREVIEW_IP_HOURLY", 20),
        device_start_hourly=_int(env, "RATE_DEVICE_START_IP_HOURLY", 10),
        device_start_daily=_int(env, "RATE_DEVICE_START_IP_DAILY", 30),
        device_poll_ip_minute=_int(env, "RATE_DEVICE_POLL_IP_MINUTE", 60),
        device_token_ip_minute=_int(env, "RATE_DEVICE_TOKEN_IP_MINUTE", 30),
        device_code_ttl=_int(env, "DEVICE_CODE_TTL_SECONDS", 600),
        device_interval=_int(env, "DEVICE_POLL_INTERVAL_SECONDS", 5),
        device_access_ttl=_int(env, "DEVICE_ACCESS_TTL_SECONDS", 600),
        device_refresh_ttl=_int(env, "DEVICE_REFRESH_TTL_DAYS", 90) * 86400,
        device_lookup_max_failures=_int(env, "DEVICE_LOOKUP_MAX_FAILURES", 5),
        device_lookup_lock_seconds=_int(env, "DEVICE_LOOKUP_LOCK_MINUTES", 15) * 60,
        agent_max=_int(env, "AGENT_MAX", 50),
        agent_ip_per_minute=_int(env, "RATE_AGENT_IP_MINUTE", 30),
        agent_msgs_per_second=_int(env, "AGENT_MSGS_PER_SECOND", 20),
        agent_stale_after=float(_int(env, "AGENT_STALE_SECONDS", 12)),
        agent_hello_timeout=float(_int(env, "AGENT_HELLO_TIMEOUT_SECONDS", 5)),
        agent_family_recheck=float(_int(env, "AGENT_FAMILY_RECHECK_SECONDS", 10)),
        agent_max_lifetime=float(_int(env, "AGENT_MAX_LIFETIME_SECONDS", 86400)),
        account_user_minute=_int(env, "RATE_ACCOUNT_USER_MINUTE", 10),
        account_ip_minute=_int(env, "RATE_ACCOUNT_IP_MINUTE", 60),
        hash_queue_max=_int(env, "HASH_QUEUE_MAX", 8),
        remote_commands=(env.get("VC_REMOTE_COMMANDS") or "").strip().lower() != "off",
    )
