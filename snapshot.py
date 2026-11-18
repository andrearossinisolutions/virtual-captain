"""Snapshot del volo per il server pubblico: solo cio' che e' nell'allowlist di server/wire.py.

Logica pura, nessun import di SimConnect, voce, server web o librerie di rete: il thread dell'uplink la usa
dopo aver preso `State.snapshot()`. La posizione (lat/lon) non esce mai: Position, Latitude, Longitude e le
coordinate dei waypoint non sono nell'allowlist.
"""
from __future__ import annotations

import json

from server import wire

SOFT_LIMIT = 96 * 1024  # byte: oltre, si tolgono prima i waypoint del piano, poi il monitor del cockpit


def dumps(view: dict) -> str:
    """Forma compatta e stabile (chiavi ordinate): stessa vista -> stesso testo, utile per l'hash."""
    return json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def remote_view(snap: dict) -> dict:
    """Vista remota di `State.snapshot()`. SnapshotError se non rispetta l'allowlist."""
    view = wire.sanitize_snapshot(snap)
    if len(dumps(view).encode("utf-8")) > SOFT_LIMIT and view.get("plan", {}).get("waypoints"):
        view["plan"] = dict(view["plan"], waypoints=[])
    if len(dumps(view).encode("utf-8")) > SOFT_LIMIT and view.get("monitor"):
        view["monitor"] = []
    return view
