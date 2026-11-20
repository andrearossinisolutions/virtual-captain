"""Snapshot del volo per il server pubblico: solo cio' che e' nell'allowlist di server/wire.py.

Logica pura, nessun import di SimConnect, voce, server web o librerie di rete: il thread dell'uplink la usa
dopo aver preso `State.snapshot()`. La posizione esce solo come riga "Position" di flight[] gia' formattata
(4 decimali, vedi wire._flight_rows), mai in briefing; Latitude, Longitude e le coordinate dei waypoint non sono
nell'allowlist.
"""
from __future__ import annotations

import hashlib
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


def section_hashes(view: dict) -> dict:
    """Hash of each top-level section (the unit of the diff)."""
    return {k: hashlib.sha1(dumps(v).encode("utf-8")).digest() for k, v in view.items()}


def diff_sections(old_hashes: dict, view: dict) -> tuple:
    """(changed sections, removed keys, new hashes) of `view` against the hashes of the last state sent.
    Removals are tombstones (`rm`). When the phase changes `flight` always travels with it (its content depends on
    the phase: see wire.apply_delta)."""
    new = section_hashes(view)
    changed = {k: view[k] for k, h in new.items() if old_hashes.get(k) != h}
    if "phase" in changed and "flight" in view:
        changed["flight"] = view["flight"]
    removed = sorted(k for k in old_hashes if k not in new)
    return changed, removed, new
