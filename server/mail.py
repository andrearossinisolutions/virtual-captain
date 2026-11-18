"""Minimal SMTP sender (stdlib only). Not imported by app.py yet (step 2a)."""
from __future__ import annotations

import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, format_datetime, make_msgid
from datetime import datetime, timezone

from server.config import ADDR_RE, MailConfigError, SmtpConfig

__all__ = ["MailConfigError", "MailSendError", "send_mail", "check_connection"]


class MailSendError(Exception):
    """Generic failure. `kind` is one of: tls, auth, timeout, connection, sender,
    recipient, internal. Never carries the server reply."""

    def __init__(self, kind: str):
        super().__init__(f"mail error: {kind}")
        self.kind = kind


def _bad(value: str) -> bool:
    return "\r" in value or "\n" in value


def build_message(cfg: SmtpConfig, to: str, subject: str, body: str) -> EmailMessage:
    if not to or _bad(to) or not ADDR_RE.match(to):
        raise MailConfigError("invalid recipient")
    if _bad(subject):
        raise MailConfigError("invalid subject")
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.mail_from)) if cfg.from_name else cfg.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = make_msgid(domain=cfg.mail_from.rsplit("@", 1)[1])
    envelope = cfg.envelope_from or cfg.mail_from
    if envelope != cfg.mail_from:
        msg["Reply-To"] = cfg.mail_from
    msg.set_content(body)
    return msg


def _open(cfg: SmtpConfig, smtp_factory):
    """Connect, STARTTLS (verified), login. Returns the open session."""
    smtp = smtp_factory(cfg.host, cfg.port, timeout=cfg.timeout)
    try:
        smtp.ehlo()
        if not smtp.has_extn("starttls"):
            raise MailSendError("tls")
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        smtp.login(cfg.user, cfg.password)
    except BaseException:
        _close(smtp)
        raise
    return smtp


def _close(smtp) -> None:
    try:
        smtp.quit()
    except Exception:
        try:
            smtp.close()
        except Exception:
            pass


def _guard(fn):
    """Map any failure to MailSendError without chaining (no credentials in tracebacks)."""
    try:
        return fn()
    except MailSendError:
        raise
    except smtplib.SMTPAuthenticationError:
        err = "auth"
    except smtplib.SMTPSenderRefused:
        err = "sender"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError):
        err = "recipient"
    except (ssl.SSLError, ssl.CertificateError):
        err = "tls"
    except (socket.timeout, TimeoutError):
        err = "timeout"
    except (OSError, smtplib.SMTPException):
        err = "connection"
    except Exception:
        err = "internal"
    raise MailSendError(err) from None


def send_mail(cfg: SmtpConfig, to: str, subject: str, body: str, *,
              smtp_factory=smtplib.SMTP) -> None:
    msg = build_message(cfg, to, subject, body)
    envelope = cfg.envelope_from or cfg.mail_from

    def run():
        smtp = _open(cfg, smtp_factory)
        try:
            smtp.send_message(msg, from_addr=envelope, to_addrs=[to])
        finally:
            _close(smtp)

    _guard(run)


def check_connection(cfg: SmtpConfig, *, smtp_factory=smtplib.SMTP) -> None:
    """Connection + STARTTLS + login, nothing sent."""
    def run():
        _close(_open(cfg, smtp_factory))

    _guard(run)
