"""Contratto tra pagina, programma e server (solo costanti e documentazione, nessuna logica).

Il Captain locale espone gia' queste route POST in web.py (`/api/<nome>`); il futuro
server pubblico inoltrera' al Captain solo i comandi in REMOTE_COMMANDS.
Le costanti del collegamento (versione, heartbeat, codici di chiusura) vivono in server/wire.py
(solo libreria standard, condiviso col server) e sono riesposte qui.
"""
from __future__ import annotations

from server.wire import *  # noqa: F401,F403  (PROTOCOL_VERSION, SUPPORTED_VERSIONS, HEARTBEAT, ...)

# Comandi che un browser remoto puo' inviare (POST /api/<nome>).
REMOTE_COMMANDS = ("fields", "clearance", "checklist-phase", "load-plan", "phase")

# Comandi che toccano la macchina locale: restano solo sul Captain, mai via server.
LOCAL_ONLY = ("simdll-fetch", "thrust-cal", "sim-retry")
