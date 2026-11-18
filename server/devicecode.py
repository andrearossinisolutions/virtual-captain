"""Device-code login of the PC program (step 2c). No HTTP here (see api_device.py).

Codes and tokens are stored only as SHA-256 hashes and never logged. One program per account:
collecting a new authorisation revokes the account's previous device tokens."""
from __future__ import annotations

import re
import secrets
import sqlite3
from typing import Optional, Tuple

from server.accounts import LOCKED, AccountError, Accounts
from server.ratelimit import DAY
from server.security import is_token_shaped, new_token, same, token_hash

# no 0/O, 1/I/L: easy to read aloud and to type from a screen
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
USER_CODE_LEN = 8
MAX_PENDING = 5000
INTERVAL_STEP = 5
INTERVAL_MAX = 60
POLL_TOLERANCE = 0.5
UA_MAX = 200
NAME_MAX = 40
DEFAULT_NAME = "Virtual Captain program"
_NAME_RE = re.compile(r"[A-Za-z0-9 .-]{1,40}", re.ASCII)
LAST_USED_EVERY = 60.0
MISMATCH = (400, "confirm_mismatch")

INVALID_CODE = (404, "invalid_code")  # same answer: unknown, expired, already used


class DeviceError(AccountError):
    def __init__(self, status: int, code: str, extra: Optional[dict] = None):
        super().__init__(status, code)
        self.extra = extra or {}


def new_user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LEN))
    return raw[:4] + "-" + raw[4:]


def normalize_user_code(raw) -> Optional[str]:
    if not isinstance(raw, str) or len(raw) > 32:
        return None
    text = re.sub(r"[\s-]", "", raw).upper()
    if len(text) != USER_CODE_LEN or any(ch not in USER_CODE_ALPHABET for ch in text):
        return None
    return text[:4] + "-" + text[4:]


def normalize_confirm(raw) -> str:
    """Typed confirmation (last 4 characters); anything unusable becomes a value that never matches."""
    if not isinstance(raw, str) or len(raw) > 16:
        return "????"
    text = re.sub(r"[\s-]", "", raw).upper()
    return text if len(text) == 4 else "????"


def clean_ua(raw: str) -> str:
    return re.sub(r"[^\x20-\x7e]", "", raw or "")[:UA_MAX]


def clean_name(raw) -> str:
    if isinstance(raw, str) and _NAME_RE.fullmatch(raw.strip()):
        return raw.strip()
    return DEFAULT_NAME


class DeviceCodes:
    def __init__(self, accounts: Accounts):
        self.acc = accounts
        self.cfg, self.db, self.clock, self.limiter = (
            accounts.cfg, accounts.db, accounts.clock, accounts.limiter)

    # ---- program side ----
    def start(self, ip: str, ua: str, device_name) -> dict:
        self.acc.maybe_cleanup()
        c_ = self.cfg
        if not self.limiter.allow("device_start_ip", ip, [(c_.device_start_hourly, 3600),
                                                          (c_.device_start_daily, DAY)]):
            raise AccountError(*LOCKED)
        now, device_code = self.clock(), new_token(32)
        name, ua = clean_name(device_name), clean_ua(ua)
        with self.db.tx() as c:
            pending = c.execute("SELECT COUNT(*) FROM device_codes WHERE status='pending' "
                                "AND expires_at>?", (now,)).fetchone()[0]
            if pending >= MAX_PENDING:
                raise AccountError(*LOCKED)
            for _ in range(8):  # user_code is UNIQUE: retry on a (rare) collision
                code = new_user_code()
                c.execute("DELETE FROM device_codes WHERE user_code=? AND expires_at<=?",
                          (code, now))
                try:
                    c.execute("INSERT INTO device_codes(device_code_hash,user_code,status,created_at,"
                              "expires_at,interval,request_ip,request_ua,device_name) "
                              "VALUES (?,?,'pending',?,?,?,?,?,?)",
                              (token_hash(device_code), code, now, now + c_.device_code_ttl,
                               c_.device_interval, ip, ua, name))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise AccountError(500, "server_error")
        base = c_.public_base_url
        return {"device_code": device_code, "user_code": code,
                "verification_uri": base + "/link",
                "verification_uri_complete": base + "/link#" + code,
                "expires_in": c_.device_code_ttl, "interval": c_.device_interval}

    def poll(self, device_code, ip: str) -> dict:
        if not self.limiter.allow("device_poll_ip", ip, [(self.cfg.device_poll_ip_minute, 60)]):
            raise AccountError(*LOCKED)
        if not is_token_shaped(device_code):
            raise DeviceError(400, "expired_token")
        now, result = self.clock(), None
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM device_codes WHERE device_code_hash=?",
                            (token_hash(device_code),)).fetchone()
            if row is None or row["status"] in ("consumed", "expired"):
                result = DeviceError(400, "expired_token")
            elif row["expires_at"] <= now:
                c.execute("UPDATE device_codes SET status='expired', request_ip='', request_ua='' "
                          "WHERE device_code_hash=?", (row["device_code_hash"],))
                result = DeviceError(400, "expired_token")
            elif row["status"] == "denied":
                result = DeviceError(400, "access_denied")
            elif row["status"] == "approved":
                cur = c.execute("UPDATE device_codes SET status='consumed', request_ip='', "
                                "request_ua='' WHERE device_code_hash=? AND status='approved'",
                                (row["device_code_hash"],))
                if cur.rowcount != 1:
                    result = DeviceError(400, "expired_token")
                else:  # the one and only answer carrying the tokens
                    c.execute("UPDATE device_tokens SET revoked_at=? WHERE user_id=? "
                              "AND revoked_at IS NULL", (now, row["user_id"]))
                    result = self._issue(c, row["user_id"], new_token(12), row["device_name"], now)
            else:
                interval, last = row["interval"], row["last_poll_at"]
                too_fast = last is not None and now - last < interval - POLL_TOLERANCE
                if too_fast:
                    interval = min(interval + INTERVAL_STEP, INTERVAL_MAX)
                c.execute("UPDATE device_codes SET last_poll_at=?, attempts=attempts+1, interval=? "
                          "WHERE device_code_hash=?", (now, interval, row["device_code_hash"]))
                result = DeviceError(400, "slow_down" if too_fast else "authorization_pending",
                                     {"interval": interval})
        if isinstance(result, DeviceError):
            raise result
        return result

    def _issue(self, c, user_id: int, family: str, device_name: str, now: float) -> dict:
        access, refresh = new_token(32), new_token(32)
        c.execute("INSERT INTO device_tokens(refresh_hash,access_hash,access_expires_at,user_id,"
                  "device_name,created_at,last_used_at,expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
                  (token_hash(refresh), token_hash(access), now + self.cfg.device_access_ttl,
                   user_id, device_name, now, now, now + self.cfg.device_refresh_ttl, family))
        return {"access_token": access, "token_type": "Bearer",
                "expires_in": self.cfg.device_access_ttl, "refresh_token": refresh}

    def refresh(self, refresh_token, ip: str) -> dict:
        """Rotation: the presented refresh token is retired and a new pair is issued. A retired
        or revoked token presented again revokes the whole family (it may have been stolen)."""
        if not self.limiter.allow("device_token_ip", ip, [(self.cfg.device_token_ip_minute, 60)]):
            raise AccountError(*LOCKED)
        if not is_token_shaped(refresh_token):
            raise DeviceError(400, "invalid_grant")
        now, result = self.clock(), None
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM device_tokens WHERE refresh_hash=?",
                            (token_hash(refresh_token),)).fetchone()
            if row is None or row["expires_at"] <= now:
                result = DeviceError(400, "invalid_grant")
            elif row["revoked_at"] is not None:
                c.execute("UPDATE device_tokens SET revoked_at=? WHERE family_id=? "
                          "AND revoked_at IS NULL", (now, row["family_id"]))
                result = DeviceError(400, "invalid_grant")
            else:
                c.execute("UPDATE device_tokens SET revoked_at=? WHERE id=?", (now, row["id"]))
                result = self._issue(c, row["user_id"], row["family_id"], row["device_name"], now)
        if isinstance(result, DeviceError):
            raise result
        return result

    def revoke(self, token, ip: str) -> None:
        """Idempotent and generic: the program's whole authorisation (family) is revoked."""
        if not self.limiter.allow("device_token_ip", ip, [(self.cfg.device_token_ip_minute, 60)]):
            raise AccountError(*LOCKED)
        if not is_token_shaped(token):
            return
        h, now = token_hash(token), self.clock()
        with self.db.tx() as c:
            for row in c.execute("SELECT family_id FROM device_tokens WHERE refresh_hash=? "
                                 "OR access_hash=?", (h, h)).fetchall():
                c.execute("UPDATE device_tokens SET revoked_at=? WHERE family_id=? "
                          "AND revoked_at IS NULL", (now, row["family_id"]))

    def verify_access_token(self, token) -> Optional[int]:
        """user_id for a valid access token, else None. For the future uplink (not a route).
        Read only; last_used_at is written at most once a minute."""
        if not is_token_shaped(token):
            return None
        now = self.clock()
        with self.db.read() as c:
            row = c.execute("SELECT id, user_id, access_expires_at, expires_at, revoked_at, "
                            "last_used_at FROM device_tokens WHERE access_hash=?",
                            (token_hash(token),)).fetchone()
        if (row is None or row["revoked_at"] is not None or row["access_expires_at"] <= now
                or row["expires_at"] <= now):
            return None
        if row["last_used_at"] is None or now - row["last_used_at"] >= LAST_USED_EVERY:
            with self.db.tx() as c:
                c.execute("UPDATE device_tokens SET last_used_at=? WHERE id=?", (now, row["id"]))
        return row["user_id"]

    def verify_access(self, token) -> Optional[Tuple[int, str]]:
        """(user_id, family_id) for a valid access token, else None. Used by the uplink handshake;
        same checks as verify_access_token. Blocking (SQLite): call it in a thread pool."""
        if not is_token_shaped(token):
            return None
        now = self.clock()
        with self.db.read() as c:
            row = c.execute("SELECT id, user_id, family_id, access_expires_at, expires_at, "
                            "revoked_at, last_used_at FROM device_tokens WHERE access_hash=?",
                            (token_hash(token),)).fetchone()
        if (row is None or row["revoked_at"] is not None or row["access_expires_at"] <= now
                or row["expires_at"] <= now):
            return None
        if row["last_used_at"] is None or now - row["last_used_at"] >= LAST_USED_EVERY:
            with self.db.tx() as c:
                c.execute("UPDATE device_tokens SET last_used_at=? WHERE id=?", (now, row["id"]))
        return row["user_id"], row["family_id"]

    def family_active(self, family_id: str) -> bool:
        """True while the family still has a usable (not revoked, not expired) token: rotation
        keeps one live row, revoke / reuse / password change / account deletion leave none."""
        now = self.clock()
        with self.db.read() as c:
            row = c.execute("SELECT 1 FROM device_tokens WHERE family_id=? AND revoked_at IS NULL "
                            "AND expires_at>? LIMIT 1", (family_id, now)).fetchone()
        return row is not None

    # ---- website side (signed in) ----
    def _keys(self, sess: dict, ip: str) -> list:
        return [f"dcode:s:{sess['sid']}", f"dcode:i:{ip}", f"dcode:u:{sess['uid']}"]

    def _guard(self, keys: list) -> None:
        c = self.cfg
        if any(self.limiter.is_locked(k, c.device_lookup_max_failures, c.device_lookup_lock_seconds)
               for k in keys):
            raise AccountError(*LOCKED)

    def _wrong(self, keys: list, error: Optional[DeviceError] = None):
        for k in keys:  # never cleared on success: a valid code of one's own must not reset it
            self.limiter.record_failure(k)
        raise error or DeviceError(*INVALID_CODE)

    def lookup(self, sess: dict, user_code, ip: str) -> dict:
        keys, code, now = self._keys(sess, ip), normalize_user_code(user_code), self.clock()
        self._guard(keys)
        row = None
        if code is not None:
            with self.db.tx() as c:
                row = c.execute("SELECT * FROM device_codes WHERE user_code=? AND status='pending' "
                                "AND expires_at>?", (code, now)).fetchone()
                if row is not None:  # approve is possible only after this lookup, by this user
                    c.execute("UPDATE device_codes SET looked_up_by=? WHERE device_code_hash=?",
                              (sess["uid"], row["device_code_hash"]))
        if row is None:
            self._wrong(keys)
        return {"status": "pending", "requested_at": row["created_at"], "ip": row["request_ip"],
                "user_agent": row["request_ua"], "device_name": row["device_name"],
                "expires_in": max(0, int(row["expires_at"] - now))}

    def decide(self, sess: dict, user_code, ip: str, approve: bool, confirm=None) -> None:
        """Deny: any pending code. Approve: needs a previous lookup by the same user AND the last
        4 characters of the code typed by the user (what the PC shows), checked in constant time;
        both failures count as wrong attempts."""
        keys, code, now = self._keys(sess, ip), normalize_user_code(user_code), self.clock()
        self._guard(keys)
        failure = DeviceError(*INVALID_CODE)
        if code is not None:
            with self.db.tx() as c:  # only a pending, unexpired code; the approver owns the token
                row = c.execute("SELECT device_code_hash, looked_up_by FROM device_codes "
                                "WHERE user_code=? AND status='pending' AND expires_at>?",
                                (code, now)).fetchone()
                if row is not None and approve and row["looked_up_by"] != sess["uid"]:
                    row = None
                if row is not None and approve and not same(
                        normalize_confirm(confirm), code[-4:]):
                    failure, row = DeviceError(*MISMATCH), None
                if row is not None:
                    cur = c.execute("UPDATE device_codes SET status=?, user_id=?, request_ip='', "
                                    "request_ua='' WHERE device_code_hash=? AND status='pending'",
                                    ("approved" if approve else "denied", sess["uid"],
                                     row["device_code_hash"]))
                    if cur.rowcount == 1:
                        return
        self._wrong(keys, failure)
