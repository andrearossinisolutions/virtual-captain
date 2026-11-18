"""Contratto tra pagina, programma e server (solo costanti e documentazione, nessuna logica).

Il Captain locale espone queste route POST in web.py (`/api/<nome>`); il server pubblico inoltra al Captain solo i
comandi in ENABLED_COMMANDS (REMOTE_COMMANDS, LOCAL_ONLY, validate_command: tutto in server/wire.py).
Le costanti del collegamento (versione, heartbeat, codici di chiusura) vivono in server/wire.py
(solo libreria standard, condiviso col server) e sono riesposte qui.
"""
from __future__ import annotations

from server.wire import *  # noqa: F401,F403  (PROTOCOL_VERSION, SUPPORTED_VERSIONS, HEARTBEAT, ...)
