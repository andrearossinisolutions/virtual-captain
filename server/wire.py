"""Wire contract between the PC program and the public server (constants only, stdlib only).

The program connects to `wss://<base>/ws/agent` with `Authorization: Bearer <access token>`
(never a query string). Messages are JSON text frames with a `t` field:
  server -> program   {"t":"welcome","v":1,"heartbeat":5,"max_msg":262144}
  program -> server   {"t":"hello","v":1,"app":"virtual-captain/<ver>","platform":"<os>"}  (within HELLO_TIMEOUT)
  program -> server   {"t":"ping"}  every HEARTBEAT seconds;  server -> program {"t":"pong"}
  either side         {"t":"bye"}
The server closes a connection that is silent for STALE_AFTER seconds, after MAX_LIFETIME seconds,
and re-reads the authorisation (token family) every FAMILY_RECHECK seconds.
"""
from __future__ import annotations

__all__ = [
    "PROTOCOL_VERSION", "SUPPORTED_VERSIONS", "HEARTBEAT", "STALE_AFTER", "MAX_MSG", "HELLO_TIMEOUT",
    "FAMILY_RECHECK", "MAX_LIFETIME", "WS_PATH", "CLOSE_REPLACED", "CLOSE_BAD_VERSION",
    "CLOSE_REVOKED", "CLOSE_TOO_BIG", "CLOSE_RESTART", "T_WELCOME", "T_HELLO", "T_PING", "T_PONG",
    "T_BYE", "welcome_message",
]

PROTOCOL_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})

HEARTBEAT = 5          # seconds between the program's pings
STALE_AFTER = 12       # seconds of silence before the server drops the connection
MAX_MSG = 262144       # bytes per message (the uvicorn --ws-max-size must match)
HELLO_TIMEOUT = 5      # seconds to send the hello after the welcome
FAMILY_RECHECK = 10    # seconds between re-reads of the token family
MAX_LIFETIME = 86400   # a connection lives at most 24 h, then the program reconnects

WS_PATH = "/ws/agent"

CLOSE_REPLACED = 4001     # another instance of the same account took over
CLOSE_BAD_VERSION = 4002  # protocol version not supported
CLOSE_REVOKED = 4003      # authorisation revoked or expired
CLOSE_TOO_BIG = 4004      # message too big or too frequent
CLOSE_RESTART = 4005      # reserved: uvicorn closes with 1012 on a restart; the program reconnects on ANY close
#                           except 4001/4002/4003, so the code is informational only

T_WELCOME, T_HELLO, T_PING, T_PONG, T_BYE = "welcome", "hello", "ping", "pong", "bye"


def welcome_message() -> dict:
    return {"t": T_WELCOME, "v": PROTOCOL_VERSION, "heartbeat": HEARTBEAT, "max_msg": MAX_MSG}
