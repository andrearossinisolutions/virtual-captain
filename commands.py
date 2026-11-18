"""Comandi della pagina sullo State: esecuzione, riconvalida e worker per l'uplink (U3).

Un solo posto per i comandi che modificano lo State: le route locali di web.py e il collegamento al server pubblico
usano `run_command`. Il programma NON si fida del server: i comandi remoti passano da `wire.validate_command` e solo
da lui. Qui non compare nulla di cio' che tocca la macchina locale (scaricamenti, regolazione delle leve, lettura
del simulatore, voce): quei comandi restano solo nella pagina locale. Il worker non scrive mai il contenuto dei comandi in console.
Solo libreria standard: nessun import di fastapi, uvicorn o websockets.
"""
from __future__ import annotations

import queue
import threading
import time
import urllib.error
from collections import deque
from typing import Callable, Optional

from server import wire

COMMANDS = ("fields", "load-plan", "phase", "sim-retry", "checklist-phase", "clearance")
CACHE_IDS = 64           # ultimi id visti: un id ripetuto si ignora
RATE_MAX, RATE_WINDOW = 10, 10.0   # al massimo 10 comandi in 10 s, poi "busy"
MIN_GAP = {"load-plan": 10.0, "sim-retry": 5.0}   # secondi tra due esecuzioni dello stesso comando


class BadRequest(ValueError):
    """Richiesta non valida per lo State (per la pagina locale: 400)."""


def run_command(st, name: str, data) -> None:
    """Esegue il comando con la semantica della pagina locale. BadRequest = richiesta non valida; ogni altra
    eccezione e' un errore di esecuzione. Nessuna validazione dei tipi qui: per i comandi remoti serve prima
    `wire.validate_command`."""
    if name == "fields":
        st.update(data)
    elif name == "load-plan":
        st.load_plan(str(data.get("simbrief", "")))
    elif name == "phase":
        st.set_phase(data.get("phase", ""))
    elif name == "sim-retry":
        st.sim_retry()
    elif name == "checklist-phase":
        try:
            if not isinstance(data, dict):
                raise ValueError("body must be an object")
            if "phase" in data:
                st.set_cl_phase(data["phase"] if isinstance(data["phase"], str) else None)
            else:
                st.set_cl_phase(data.get("step"))
        except ValueError as e:
            raise BadRequest(str(e)) from None
    elif name == "clearance":
        try:
            if data.get("reset") is True:
                st.reset_clearance()
            else:
                st.set_clearance(data)
        except (ValueError, AttributeError) as e:
            raise BadRequest(str(e)) from None
    else:
        raise KeyError(name)


def _load_plan_reason(exc: BaseException) -> str:
    """Motivo fisso di un caricamento del piano fallito (mai il testo dell'eccezione)."""
    if isinstance(exc, urllib.error.HTTPError):
        return "not_found" if exc.code in (400, 404) else "network"
    if isinstance(exc, (urllib.error.URLError, OSError, TimeoutError)):
        return "network"
    return "bad_plan"


def execute(st, name: str, data) -> dict:
    """Comando remoto: riconvalida e poi esecuzione. Ritorna la parte del risultato dopo l'id:
    {"ok": True} oppure {"ok": False, "code": ..., ["msg"|"reason"]}. Mai eccezioni."""
    try:
        clean = wire.validate_command(name, data)
    except wire.CommandError as e:
        return {"ok": False, "code": "invalid", "msg": str(e)[:wire.MAX_RESULT_MSG]}
    if name == "load-plan" and "simbrief" not in clean:
        clean = {"simbrief": getattr(st, "simbrief", "")}  # il codice SimBrief gia' noto al programma
    try:
        run_command(st, name, clean)
    except BadRequest as e:
        return {"ok": False, "code": "invalid", "msg": str(e)[:wire.MAX_RESULT_MSG]}
    except Exception as e:  # noqa: BLE001  (mai il testo dell'errore verso il server)
        if name == "load-plan":
            return {"ok": False, "code": "failed", "reason": _load_plan_reason(e)}
        return {"ok": False, "code": "failed"}
    return {"ok": True}


class CommandWorker:
    """Esegue i comandi remoti fuori dal thread del collegamento (un load-plan lento non ferma ping e snapshot).

    `submit` non blocca mai e puo' essere chiamato solo dal thread della sessione; i risultati escono da `results`
    (queue.Queue di messaggi gia' pronti) e li spedisce lo stesso thread che possiede il socket. Un comando alla volta:
    il secondo risponde "busy"."""

    def __init__(self, st, enabled: bool = True, mono: Callable[[], float] = time.monotonic,
                 executor: Optional[Callable[[object, str, dict], dict]] = None):
        self.st, self.enabled, self.mono = st, enabled, mono
        self._execute = executor or execute
        self.results: "queue.Queue[dict]" = queue.Queue()
        self._jobs: "queue.Queue" = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._busy = False
        self._closed = False
        self._ids: deque = deque(maxlen=CACHE_IDS)
        self._times: deque = deque()
        self._last: dict = {}
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="commands", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._closed = True
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass  # il worker e' occupato: a fine lavoro vede _closed ed esce; il risultato non verra' piu' letto

    def _answer(self, cmd_id: str, **body) -> None:
        self.results.put({"t": wire.T_RESULT, "id": cmd_id, **body})

    def submit(self, cmd_id, name, data) -> None:
        if not isinstance(cmd_id, str) or not wire.CMD_ID_RE.fullmatch(cmd_id):
            return  # senza id valido non c'e' a chi rispondere
        with self._lock:
            if cmd_id in self._ids:
                return  # ripetuto: ignorato
            self._ids.append(cmd_id)
            if not self.enabled:
                return self._answer(cmd_id, ok=False, code="disabled")
            now = self.mono()
            while self._times and now - self._times[0] > RATE_WINDOW:
                self._times.popleft()
            if len(self._times) >= RATE_MAX:
                return self._answer(cmd_id, ok=False, code="busy")
            self._times.append(now)
            try:
                clean = wire.validate_command(name, data)
            except wire.CommandError as e:
                return self._answer(cmd_id, ok=False, code="invalid", msg=str(e)[:wire.MAX_RESULT_MSG])
            gap = MIN_GAP.get(name)
            if (gap is not None and name in self._last and now - self._last[name] < gap) or self._busy:
                return self._answer(cmd_id, ok=False, code="busy")
            if gap is not None:
                self._last[name] = now
            self._busy = True
            self._jobs.put_nowait((cmd_id, name, clean))

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            cmd_id, name, clean = job
            try:
                out = self._execute(self.st, name, clean)
            except Exception:  # noqa: BLE001  (execute non dovrebbe lanciare; comunque mai il contenuto)
                out = {"ok": False, "code": "failed"}
            with self._lock:
                self._busy = False
            self._answer(cmd_id, **out)
            if self._closed:
                return

    def drain(self) -> list:
        """Risultati pronti (non bloccante)."""
        out = []
        while True:
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                return out
