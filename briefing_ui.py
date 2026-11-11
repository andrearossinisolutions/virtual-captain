"""Finestra Briefing (Tkinter): SimBrief ID, carica piano, campi modificabili."""
from __future__ import annotations
import json
from datetime import datetime
import pathlib
import tkinter as tk
from tkinter import ttk, messagebox

from simbrief import fetch_latest, to_briefing

CFG = pathlib.Path.home() / ".virtual-captain.json"
FIELDS = [("origin", "Departure"), ("destination", "Arrival"), ("runway", "Runway"),
          ("ta", "TA (ft)"), ("accel_agl", "Acceleration (ft AGL)"),
          ("v1", "V1"), ("vr", "VR"), ("v2", "V2"), ("flap", "Flap")]


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


FROM_PLAN = ["origin", "destination", "runway", "ta", "v1", "vr", "v2", "flap"]  # accel_agl è solo manuale


def _mode(m):
    """Valori vecchi (italiano) dal file di configurazione → inglese."""
    return {"allenamento": "training", "silenziosa": "silent"}.get(m, m if m in ("training", "silent") else "training")


def run() -> dict | None:
    cfg = json.loads(CFG.read_text()) if CFG.exists() else {}
    root = tk.Tk()
    root.title("Virtual Captain — Briefing")
    vars_ = {k: tk.StringVar(value=str(cfg.get(k, ""))) for k, _ in FIELDS}
    sb = tk.StringVar(value=cfg.get("simbrief", ""))
    mode = tk.StringVar(value=_mode(cfg.get("mode")))
    info = tk.StringVar()
    result: dict = {}

    f = ttk.Frame(root, padding=12)
    f.grid()
    ttk.Label(f, text="SimBrief ID or username").grid(row=0, column=0, sticky="w")
    ttk.Entry(f, textvariable=sb, width=18).grid(row=0, column=1)

    def load():
        try:
            data = to_briefing(fetch_latest(sb.get()))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("SimBrief", str(e))
            return
        # Tutto ciò che viene dal piano si sovrascrive; ciò che il piano non ha si svuota,
        # così non restano valori di un volo precedente (es. velocità da ricalcolare).
        missing = []
        for k in FROM_PLAN:
            v = data.get(k)
            vars_[k].set("" if v is None else str(v))
            if v is None:
                missing.append(dict(FIELDS)[k])
        info.set(f"{data['origin']} → {data['destination']}  (generated: {_when(data['generated'])})"
                 + (f"\nNot in plan, enter manually: {', '.join(missing)}" if missing else ""))

    ttk.Button(f, text="Load latest plan", command=load).grid(row=0, column=2, padx=6)
    ttk.Label(f, textvariable=info, foreground="#06c").grid(row=1, column=0, columnspan=3, sticky="w")
    for i, (k, label) in enumerate(FIELDS, start=2):
        ttk.Label(f, text=label).grid(row=i, column=0, sticky="w")
        ttk.Entry(f, textvariable=vars_[k], width=18).grid(row=i, column=1)
    r = len(FIELDS) + 2
    ttk.Label(f, text="Mode").grid(row=r, column=0, sticky="w")
    ttk.Combobox(f, textvariable=mode, values=["training", "silent"], width=15,
                 state="readonly").grid(row=r, column=1)

    def done():
        def num(k, d):
            try:
                return int(vars_[k].get())
            except ValueError:
                return d
        result.update(ta=num("ta", 5000), accel_agl=num("accel_agl", 1500), mode=mode.get(),
                      v1=num("v1", None), vr=num("vr", None), v2=num("v2", None))
        CFG.write_text(json.dumps({**{k: v.get() for k, v in vars_.items()},
                                   "simbrief": sb.get(), "mode": mode.get()}))
        root.destroy()

    ttk.Button(f, text="Briefing complete", command=done).grid(row=r + 1, column=0, columnspan=3, pady=8)
    root.mainloop()
    return result or None
