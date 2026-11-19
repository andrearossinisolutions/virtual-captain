"""Latch delle voci della checklist soddisfatte: una voce resta verde anche se il valore cambia per l'azione successiva (es. Flaps 1 poi Flaps 0)."""
from __future__ import annotations

LATCH_STABLE_S = 2.0  # secondi di "ok" grezzo continuo (gate aperto) prima di congelare la voce
LATCH_MAX_GAP_S = 1.5  # campioni piu' distanti (cambio fase, sim offline, ritorno dopo minuti) non contano come continuita': il timer riparte


class Latch:
    """Voci congelate (tutte le voci con link, non solo quelle con gate: voluto da Andrea). Si aggiorna solo da State.set_monitor (observe), mai dallo snapshot. Lo stato grezzo (senza latch) decide."""

    def __init__(self, stable_s: float = LATCH_STABLE_S):
        self.stable_s = stable_s
        self._ids: set = set()
        self._last: dict = {}  # id voce -> istante dell'ultimo campione osservato
        self._since: dict = {}  # id voce -> istante da cui lo stato grezzo e' ok senza interruzioni

    def observe(self, item_id: str, raw_ok: bool, now: float) -> bool:
        """raw_ok: stato grezzo ok (implica gate aperto). True se la voce si congela in questa chiamata. Voci gia' congelate: ignorate."""
        if item_id in self._ids:
            return False
        last, self._last[item_id] = self._last.get(item_id), now
        if last is not None and now - last > LATCH_MAX_GAP_S:
            self._since.pop(item_id, None)  # buco nei campioni: la stabilita' non e' dimostrata
        if not raw_ok:
            self._since.pop(item_id, None)
            return False
        t0 = self._since.setdefault(item_id, now)
        if now - t0 >= self.stable_s:
            self._ids.add(item_id)
            self._since.pop(item_id, None)
            return True
        return False

    def ids(self) -> frozenset:
        return frozenset(self._ids)

    def __contains__(self, item_id) -> bool:
        return item_id in self._ids

    def clear(self, ids=None) -> None:
        """Azzera le voci indicate (e i loro timer); None = tutte."""
        if ids is None:
            self.clear_all()
            return
        ids = set(ids)
        self._ids -= ids
        for i in ids:
            self._since.pop(i, None)
            self._last.pop(i, None)

    def clear_all(self) -> None:
        self._ids.clear()
        self._since.clear()
        self._last.clear()

    def to_dict(self) -> dict:
        return {"latched": sorted(self._ids)}

    def from_dict(self, data) -> None:
        """Ripristina da to_dict; tollerante: dato non valido o voci non testuali => ignorati (i timer ripartono)."""
        self.clear_all()
        if not isinstance(data, dict) or not isinstance(data.get("latched"), list):
            return
        self._ids = {i for i in data["latched"] if isinstance(i, str) and i}
