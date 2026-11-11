"""Voce locale: pyttsx3 (SAPI su Windows) in un thread dedicato; in assenza stampa soltanto.

Un motore nuovo per ogni frase: su Windows riusare lo stesso motore fa spesso parlare solo la prima volta.
"""
from __future__ import annotations

import os
import queue
import threading

try:
    import pyttsx3
except Exception:  # noqa: BLE001
    pyttsx3 = None

_q: queue.Queue = queue.Queue()
_listed = False
# Preferenze, in ordine: variabile d'ambiente CAPTAIN_VOICE (parte del nome), poi voci inglesi note.
_PREFERRED = ("david", "mark", "zira", "hazel", "george", "en-us", "en-gb", "english")


def _pick_voice(e):
    """Sceglie una voce inglese; se non ce n'è, lascia quella di default e lo dice una volta."""
    global _listed
    voices = e.getProperty("voices")
    wanted = [os.environ.get("CAPTAIN_VOICE", "").lower()] if os.environ.get("CAPTAIN_VOICE") else []
    for key in wanted + list(_PREFERRED):
        for v in voices:
            hay = f"{v.name} {v.id} {getattr(v, 'languages', '')}".lower()
            if key and key in hay:
                e.setProperty("voice", v.id)
                e.setProperty("rate", 175)
                return
    if not _listed:
        _listed = True
        print("[VOCE] nessuna voce inglese trovata. Disponibili: "
              + ", ".join(v.name for v in voices), flush=True)


def _worker():
    try:  # COM va inizializzato in ogni thread (pywin32, installato con pyttsx3 su Windows)
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    while True:
        text = _q.get()
        try:
            if pyttsx3:
                e = pyttsx3.init()
                _pick_voice(e)
                e.say(text)
                e.runAndWait()
                e.stop()
                del e
        except Exception as ex:  # noqa: BLE001
            print(f"[VOCE] errore: {ex}", flush=True)
        finally:
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


def say(text: str):
    """Stampa e mette in coda la frase (non blocca il ciclo di lettura)."""
    print(f"[CAPITANO] {text}", flush=True)
    _q.put(text)


def wait():
    """Aspetta che il capitano finisca di parlare (da chiamare prima di uscire)."""
    _q.join()
