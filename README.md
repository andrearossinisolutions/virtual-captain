# Virtual Captain

Comandante virtuale per MSFS 2024: tu sei il First Officer, lui ti ricorda cosa fare (positive rate/gear up, acceleration altitude, transition altitude, 10.000 ft). Legge i dati dal simulatore via WebAPI di AAO (Axis and Ohs), parla con la voce locale di Windows.

- `python main.py` — Briefing (SimBrief ID or username → "Load latest plan", editable fields) e poi volo
- `python main.py --sim` — decollo simulato, senza AAO (prova voce e soglie)
- `python test_brain.py` — test della logica (pytest o diretto)

Scheda e decisioni nel second brain: `life/virtual-captain/progetto.md`.
