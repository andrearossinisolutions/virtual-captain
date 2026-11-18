"""Contratto tra pagina e server (solo costanti e documentazione, nessuna logica).

Il Captain locale espone gia' queste route POST in web.py (`/api/<nome>`); il futuro
server pubblico inoltrera' al Captain solo i comandi in REMOTE_COMMANDS.
"""
from __future__ import annotations

PROTOCOL_VERSION = 1

# Comandi che un browser remoto puo' inviare (POST /api/<nome>).
REMOTE_COMMANDS = ("fields", "clearance", "checklist-phase", "load-plan", "phase")

# Comandi che toccano la macchina locale: restano solo sul Captain, mai via server.
LOCAL_ONLY = ("simdll-fetch", "thrust-cal", "sim-retry")
