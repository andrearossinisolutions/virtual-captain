# Virtual Captain

Comandante virtuale per MSFS 2024: tu sei il First Officer, lui ti ricorda cosa fare (positive rate/gear up, acceleration altitude, transition altitude, 10.000 ft). Legge i dati dal simulatore via WebAPI di AAO (Axis and Ohs), parla con la voce locale di Windows.

- `python main.py` — avvia la pagina web del briefing e stampa l'indirizzo da aprire dal tablet (via Tailscale). Dal tablet si compila il briefing (SimBrief ID or username → "Load latest plan", campi modificabili in ogni momento); con **Briefing complete** il programma sul PC legge AAO e il capitano parla; **Back to briefing** mette in pausa.
- `python main.py --sim` — decollo simulato, senza AAO (prova voce e soglie)
- `--port 8080`, `--host <IP>` — la pagina si lega solo all'IP di Tailscale (mai a tutte le reti); senza Tailscale resta su localhost
- `python test_brain.py` e `python test_web.py` — test (logica e API della pagina)

Scheda e decisioni nel second brain: `life/virtual-captain/progetto.md`.
