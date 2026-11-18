"""Manual mail test: python -m server.mailtest <recipient> [--check] [--envelope-from ADDR]
Prints only OK or the TYPE of error. Never prints passwords or variable values.
Wrong command-line usage is handled by argparse (exit code 2, usage message only)."""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import datetime, timezone

from server.config import ADDR_RE, MailConfigError, load_smtp_config
from server.mail import MailSendError, check_connection, send_mail

def main(argv=None, env=None, smtp_factory=None) -> int:
    p = argparse.ArgumentParser(prog="server.mailtest")
    p.add_argument("recipient", nargs="?")
    p.add_argument("--check", action="store_true", help="connect, STARTTLS and login only")
    p.add_argument("--envelope-from", dest="envelope_from")
    a = p.parse_args(argv)
    kw = {"smtp_factory": smtp_factory} if smtp_factory else {}
    try:
        if not a.check and not (a.recipient and ADDR_RE.match(a.recipient)):
            raise MailConfigError("invalid recipient")
        cfg = load_smtp_config(os.environ if env is None else env)
        if a.envelope_from:
            if not ADDR_RE.match(a.envelope_from):
                raise MailConfigError("invalid envelope address")
            cfg = dataclasses.replace(cfg, envelope_from=a.envelope_from)
        if a.check:
            check_connection(cfg, **kw)
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            send_mail(cfg, a.recipient, "Virtual Captain: mail test",
                      f"This is a test message from Virtual Captain.\nSent at {now}.\n", **kw)
    except MailConfigError as e:
        print(f"ERROR config: {e}", file=sys.stderr)
        return 2
    except MailSendError as e:
        print(f"ERROR {e.kind}", file=sys.stderr)
        return 3
    except Exception:  # never show a traceback: it could carry credentials
        print("ERROR internal", file=sys.stderr)
        return 3
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
