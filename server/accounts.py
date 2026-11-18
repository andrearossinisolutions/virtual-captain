"""Account logic: registration, verification, sessions, password and email changes.
No HTTP here (see api_auth.py). Nothing sensitive is ever logged."""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Callable, Optional

from server import mail_templates as tpl
from server.config import AppConfig
from server.db import Database
from server.ratelimit import DAY, RateLimiter
from server.security import (hash_password, is_token_shaped, new_token, normalize_email,
                             normalize_login_password, normalize_password, same, token_hash,
                             verify_dummy, verify_password)

log = logging.getLogger("virtual_captain.accounts")

VERIFY_TTL = 24 * 3600
RESET_TTL = 3600
SESSION_ABSOLUTE = 30 * DAY
SESSION_IDLE = 7 * DAY
SESSION_TOUCH_EVERY = 60.0  # last_seen is refreshed at most once a minute (the idle limit is days)
MAX_SESSIONS = 10
CLEANUP_EVERY = 600.0
ROTATED_KEEP = 7 * DAY
SIMBRIEF_RE = re.compile(r"[A-Za-z0-9 ._-]{1,40}", re.ASCII)  # Pilot ID or username


def normalize_simbrief(value) -> Optional[str]:
    """Stripped SimBrief Pilot ID or username, "" if empty, None if not acceptable."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value and not SIMBRIEF_RE.fullmatch(value):
        return None
    return value

Mail = tuple  # (to, subject, body)


class AccountError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


BAD_REQUEST = (400, "invalid_request")
BAD_TOKEN = (400, "invalid_token")
BAD_CREDENTIALS = (401, "invalid_credentials")
LOCKED = (429, "too_many_attempts")


class Accounts:
    def __init__(self, config: AppConfig, db: Database, mailer: Callable[[str, str, str], None],
                 clock: Callable[[], float]):
        self.cfg, self.db, self.mailer, self.clock = config, db, mailer, clock
        self.limiter = RateLimiter(db, clock, config.secret_key)
        self._last_cleanup = None

    def maybe_cleanup(self) -> None:
        """First call after startup, then at most every 10 minutes: expired sessions, used or
        expired tokens, old rate events and failures."""
        now = self.clock()
        if self._last_cleanup is not None and now - self._last_cleanup < CLEANUP_EVERY:
            return
        self._last_cleanup = now
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE created_at < ? OR last_seen < ?",
                      (now - SESSION_ABSOLUTE, now - SESSION_IDLE))
            c.execute("DELETE FROM email_tokens WHERE used_at IS NOT NULL OR expires_at < ?", (now,))
            c.execute("UPDATE device_codes SET request_ip='', request_ua='' WHERE expires_at < ?",
                      (now,))  # explicit, then the rows go
            c.execute("DELETE FROM device_codes WHERE expires_at < ?", (now,))
            # rotated/revoked refresh tokens are kept a while for reuse detection
            c.execute("DELETE FROM device_tokens WHERE expires_at < ? OR revoked_at < ?",
                      (now, now - ROTATED_KEEP))
        self.limiter.purge()

    @staticmethod
    def _revoke_devices(c, user_id: int, now: float) -> None:
        """Every device token of the account stops working; codes approved but not yet
        collected are dropped (rows stay a while so a replayed refresh token is detected)."""
        c.execute("UPDATE device_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                  (now, user_id))
        c.execute("DELETE FROM device_codes WHERE user_id=?", (user_id,))

    # ---- mail ----
    def deliver(self, mail: Mail) -> None:
        """Send one mail; failures are logged without addresses or content."""
        try:
            self.mailer(*mail)
        except Exception as exc:  # the caller already answered: never raise here
            log.warning("mail delivery failed (%s)", getattr(exc, "kind", type(exc).__name__))

    def _mail_allowed(self, addr: str, group: str) -> bool:
        """Per-address cap plus a daily cap per mail type (verify / reset / other), so a flood of
        sign-ups cannot starve password resets. Known trade-off: an attacker who spends the
        per-address quota can delay a legitimate mail to that address for up to an hour."""
        return (self.limiter.allow("mail_addr", addr, [(self.cfg.mail_addr_hourly, 3600)])
                and self.limiter.allow("mail_global_" + group, "all", [(self.cfg.mail_daily, DAY)]))

    def _ip_gate(self, ip: str, bucket: str = "ip_signup") -> None:
        if not self.limiter.allow(bucket, ip, [(self.cfg.ip_hourly, 3600), (self.cfg.ip_daily, DAY)]):
            raise AccountError(*LOCKED)

    # ---- tokens ----
    def _create_token(self, c, user_id: int, kind: str, ttl: float, new_email: str = "") -> str:
        c.execute("DELETE FROM email_tokens WHERE user_id=? AND kind=?", (user_id, kind))
        token = new_token()
        c.execute("INSERT INTO email_tokens(user_id,kind,token_hash,new_email,expires_at) "
                  "VALUES (?,?,?,?,?)",
                  (user_id, kind, token_hash(token), new_email, self.clock() + ttl))
        return token

    def _find_token(self, c, token, kinds):
        if not is_token_shaped(token):
            return None
        h = token_hash(token)
        row = c.execute("SELECT * FROM email_tokens WHERE token_hash=?", (h,)).fetchone()
        if (row is None or not same(row["token_hash"], h) or row["used_at"] is not None
                or row["expires_at"] <= self.clock() or row["kind"] not in kinds):
            return None
        return row

    def _peek(self, token, kinds) -> bool:
        """True if the token exists, is unused and not expired. Does NOT consume it."""
        with self.db.tx() as c:
            return self._find_token(c, token, kinds) is not None

    def _consume(self, c, row) -> None:
        cur = c.execute("UPDATE email_tokens SET used_at=? WHERE id=? AND used_at IS NULL",
                        (self.clock(), row["id"]))
        if cur.rowcount != 1:
            raise AccountError(*BAD_TOKEN)

    # ---- registration / verification ----
    async def register(self, email, simbrief, ip: str) -> Optional[Mail]:
        """Always the same answer. No password here: it is chosen on the verification page, so
        nobody can pre-register someone else's address with a password of their own (pre-hijack).
        New address: an unverified account with NO usable password and the verify link. Unverified
        address: the link is sent again and a non-empty SimBrief value replaces the stored one (last
        registration wins); the password is never touched. Verified address: the 'someone
        tried to register' mail, without a link."""
        self.maybe_cleanup()
        em = normalize_email(email)
        sb = normalize_simbrief("" if simbrief is None else simbrief)
        if em is None or sb is None:
            raise AccountError(*BAD_REQUEST)
        self._ip_gate(ip)
        with self.db.tx() as c:
            existing = c.execute("SELECT verified FROM users WHERE email=?", (em,)).fetchone()
        allowed = self._mail_allowed(em, "other" if existing and existing["verified"] else "verify")
        token = None
        with self.db.tx() as c:
            user = c.execute("SELECT id, verified FROM users WHERE email=?", (em,)).fetchone()
            if user is None:
                try:  # pw_hash '' is never a valid argon2 hash: the account cannot log in
                    cur = c.execute("INSERT INTO users(email,pw_hash,verified,simbrief_id,created_at) "
                                    "VALUES (?,'',0,?,?)", (em, sb, self.clock()))
                    uid = cur.lastrowid
                except sqlite3.IntegrityError:
                    uid = None
            elif not user["verified"]:
                uid = user["id"]
                if sb:  # the last registration wins for the SimBrief value (password untouched)
                    c.execute("UPDATE users SET simbrief_id=? WHERE id=?", (sb, uid))
            else:
                uid = None
            if uid is not None and allowed:  # a suppressed mail keeps the previous link valid
                token = self._create_token(c, uid, "verify", VERIFY_TTL)
        if not allowed:
            return None
        if token is not None:
            return (em,) + tpl.verify_mail(self.cfg.public_base_url, token)
        if existing and existing["verified"]:
            return (em,) + tpl.already_registered_mail()
        return None

    def verify_preview(self, token, ip: str) -> dict:
        """Does NOT consume the token. Own, wider bucket (the page calls it on every load)."""
        if not self.limiter.allow("preview_ip", ip, [(self.cfg.preview_ip_hourly, 3600)]):
            raise AccountError(*LOCKED)
        with self.db.tx() as c:
            row = self._find_token(c, token, ("verify", "email_change"))
        return {"valid": row is not None, "kind": row["kind"] if row else None}

    async def verify(self, token, password, ip: str):
        """Consumes the token. 'verify': the password chosen here is stored, the account becomes
        verified and a session is opened (returns session token and csrf). 'email_change': no
        password, the new address is activated and every session is closed."""
        self._ip_gate(ip, "verify_ip")
        with self.db.tx() as c:
            row = self._find_token(c, token, ("verify", "email_change"))
        if row is None:
            raise AccountError(*BAD_TOKEN)  # unknown/expired/used: no hash computed
        pw_hash = None
        if row["kind"] == "verify":
            pw = normalize_password(password)
            if pw is None:
                raise AccountError(*BAD_REQUEST)  # token not consumed: the user can retry
            pw_hash = await hash_password(pw)  # one hash on this path
        session = None
        with self.db.tx() as c:
            row = self._find_token(c, token, ("verify", "email_change"))
            if row is None:
                raise AccountError(*BAD_TOKEN)
            self._consume(c, row)
            if row["kind"] == "verify":
                cur = c.execute("UPDATE users SET pw_hash=?, verified=1 WHERE id=? AND verified=0",
                                (pw_hash, row["user_id"]))
                if cur.rowcount != 1:
                    raise AccountError(*BAD_TOKEN)
                c.execute("DELETE FROM email_tokens WHERE user_id=?", (row["user_id"],))
            else:
                if c.execute("SELECT 1 FROM users WHERE email=?", (row["new_email"],)).fetchone():
                    raise AccountError(*BAD_TOKEN)
                c.execute("UPDATE users SET email=?, verified=1 WHERE id=?",
                          (row["new_email"], row["user_id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
                c.execute("DELETE FROM email_tokens WHERE user_id=? AND kind='reset'",
                          (row["user_id"],))
        if row["kind"] == "verify":
            session = self._new_session(row["user_id"])
        return row["kind"], session

    # ---- login / sessions ----
    async def login(self, email, password, ip: str):
        self.maybe_cleanup()
        if not self.limiter.allow("login_ip", ip, [(self.cfg.login_ip_per_15min, 900)]):
            raise AccountError(*LOCKED)
        em = normalize_email(email)
        pw = normalize_login_password(password)
        key = em or (email[:254].strip().lower() if isinstance(email, str) else "")
        if self.limiter.is_locked(key, self.cfg.login_max_failures, self.cfg.lockout_seconds):
            raise AccountError(*LOCKED)
        user = None
        if em is not None:
            with self.db.tx() as c:
                user = c.execute("SELECT id, pw_hash, verified FROM users WHERE email=?",
                                 (em,)).fetchone()
        ok = False
        if pw is not None:
            ok = (await verify_password(user["pw_hash"], pw)) if user else await verify_dummy(pw)
        if not (ok and user and user["verified"] and user["pw_hash"]):
            self.limiter.record_failure(key)
            raise AccountError(*BAD_CREDENTIALS)
        self.limiter.clear_failures(key)
        return self._new_session(user["id"])

    def _new_session(self, user_id: int):
        token, csrf, now = new_token(), new_token(24), self.clock()
        with self.db.tx() as c:
            c.execute("INSERT INTO sessions(user_id,token_hash,csrf,created_at,last_seen) "
                      "VALUES (?,?,?,?,?)", (user_id, token_hash(token), csrf, now, now))
            c.execute("DELETE FROM sessions WHERE user_id=? AND id NOT IN "
                      "(SELECT id FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                      (user_id, user_id, MAX_SESSIONS))
        return token, csrf

    def session(self, token) -> Optional[dict]:
        if not is_token_shaped(token):
            return None
        h, now = token_hash(token), self.clock()
        with self.db.tx() as c:
            row = c.execute("SELECT s.id AS sid, s.token_hash, s.csrf, s.created_at, s.last_seen, "
                            "u.id AS uid, u.email, u.verified, u.simbrief_id FROM sessions s "
                            "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (h,)).fetchone()
            if row is None or not same(row["token_hash"], h):
                return None
            if now - row["created_at"] > SESSION_ABSOLUTE or now - row["last_seen"] > SESSION_IDLE:
                c.execute("DELETE FROM sessions WHERE id=?", (row["sid"],))
                return None
            if now - row["last_seen"] >= SESSION_TOUCH_EVERY:  # hot polling must not write to the DB every second
                c.execute("UPDATE sessions SET last_seen=? WHERE id=?", (now, row["sid"]))
        return {"sid": row["sid"], "uid": row["uid"], "email": row["email"],
                "verified": bool(row["verified"]), "simbrief_id": row["simbrief_id"],
                "csrf": row["csrf"]}

    def logout(self, sess: dict) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE id=?", (sess["sid"],))

    # ---- authenticated changes ----
    async def _confirm(self, sess: dict, password) -> None:
        pw = normalize_login_password(password)
        if self.limiter.is_locked(sess["email"], self.cfg.login_max_failures,
                                  self.cfg.lockout_seconds):
            raise AccountError(*LOCKED)
        with self.db.tx() as c:
            row = c.execute("SELECT pw_hash FROM users WHERE id=?", (sess["uid"],)).fetchone()
        if pw is None or row is None or not await verify_password(row["pw_hash"], pw):
            self.limiter.record_failure(sess["email"])
            raise AccountError(*BAD_CREDENTIALS)
        self.limiter.clear_failures(sess["email"])

    async def change_password(self, sess: dict, current, new) -> None:
        new_pw = normalize_password(new)
        if new_pw is None:
            raise AccountError(*BAD_REQUEST)
        await self._confirm(sess, current)
        new_hash = await hash_password(new_pw)
        with self.db.tx() as c:
            c.execute("UPDATE users SET pw_hash=? WHERE id=?", (new_hash, sess["uid"]))
            c.execute("DELETE FROM sessions WHERE user_id=? AND id<>?", (sess["uid"], sess["sid"]))
            self._revoke_devices(c, sess["uid"], self.clock())
            c.execute("DELETE FROM email_tokens WHERE user_id=? AND kind='reset'", (sess["uid"],))

    async def change_email(self, sess: dict, new_email, password) -> Optional[Mail]:
        em = normalize_email(new_email)
        if em is None:
            raise AccountError(*BAD_REQUEST)
        await self._confirm(sess, password)
        with self.db.tx() as c:
            unusable = em == sess["email"] or c.execute("SELECT 1 FROM users WHERE email=?",
                                                        (em,)).fetchone()
        if unusable or not self._mail_allowed(em, "verify"):
            return None  # same answer as success: no way to probe addresses
        with self.db.tx() as c:
            token = self._create_token(c, sess["uid"], "email_change", VERIFY_TTL, em)
        return (em,) + tpl.email_change_mail(self.cfg.public_base_url, token)

    def set_simbrief_id(self, sess: dict, value) -> str:
        value = normalize_simbrief(value)
        if value is None:
            raise AccountError(*BAD_REQUEST)
        with self.db.tx() as c:
            c.execute("UPDATE users SET simbrief_id=? WHERE id=?", (value, sess["uid"]))
        return value

    async def delete_account(self, sess: dict, password) -> None:
        await self._confirm(sess, password)
        key = self.limiter.key(sess["email"])
        with self.db.tx() as c:
            self._revoke_devices(c, sess["uid"], self.clock())
            c.execute("DELETE FROM device_tokens WHERE user_id=?", (sess["uid"],))
            c.execute("DELETE FROM users WHERE id=?", (sess["uid"],))  # cascades
            c.execute("DELETE FROM login_failures WHERE key=?", (key,))
            c.execute("DELETE FROM rate_events WHERE key=?", (key,))

    # ---- password reset ----
    def request_reset(self, email, ip: str) -> Optional[Mail]:
        self.maybe_cleanup()
        em = normalize_email(email)
        if em is None:
            raise AccountError(*BAD_REQUEST)
        self._ip_gate(ip)
        with self.db.tx() as c:
            user = c.execute("SELECT id, verified FROM users WHERE email=?", (em,)).fetchone()
        if user is None or not user["verified"] or not self._mail_allowed(em, "reset"):
            return None  # a suppressed mail keeps the previous reset link valid
        with self.db.tx() as c:
            token = self._create_token(c, user["id"], "reset", RESET_TTL)
        return (em,) + tpl.reset_mail(self.cfg.public_base_url, token)

    async def reset_password(self, token, new, ip: str) -> None:
        pw = normalize_password(new)
        if pw is None:
            raise AccountError(*BAD_REQUEST)
        self._ip_gate(ip, "reset_ip")  # before any hashing (CPU/RAM protection)
        if not self._peek(token, ("reset",)):
            raise AccountError(*BAD_TOKEN)  # unknown/expired/used: no hash computed
        new_hash = await hash_password(pw)
        with self.db.tx() as c:
            row = self._find_token(c, token, ("reset",))
            if row is None:
                raise AccountError(*BAD_TOKEN)
            self._consume(c, row)
            uid = row["user_id"]
            email = c.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()["email"]
            c.execute("UPDATE users SET pw_hash=?, verified=1 WHERE id=?", (new_hash, uid))
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self._revoke_devices(c, uid, self.clock())
            c.execute("DELETE FROM email_tokens WHERE user_id=?", (uid,))
            c.execute("DELETE FROM login_failures WHERE key=?", (self.limiter.key(email),))
