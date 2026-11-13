# Virtual Captain

Comandante virtuale per MSFS 2024: tu sei il First Officer, lui ti ricorda cosa fare (positive rate/gear up, acceleration altitude, transition altitude, 10.000 ft). Legge i dati dal simulatore via WebAPI di AAO (Axis and Ohs), parla con la voce locale di Windows.

- `python main.py` — avvia la pagina web del briefing e stampa l'indirizzo da aprire dal tablet (via Tailscale). Dal tablet si compila il briefing (SimBrief ID or username → "Load latest plan", campi modificabili in ogni momento); con **Briefing complete** il programma sul PC legge AAO e il capitano parla; **Back to briefing** mette in pausa.
- `python main.py --test-links` — prova dei collegamenti: valuta i link di `checklist_links.json` come verificati sui valori reali e mostra accanto a ogni voce il grezzo letto (es. `S_OH_ELEC_BAT1=1`)
- `python main.py --sim` — decollo simulato, senza AAO (prova voce e soglie)
- `--port 8080`, `--host <IP>`, `--no-firewall` — al primo avvio su Windows il programma chiede il permesso (UAC) di creare la regola del firewall per la porta, solo per la rete Tailscale (100.64.0.0/10, quindi non dipende dall'IP); — la pagina si lega solo all'IP di Tailscale (mai a tutte le reti); senza Tailscale resta su localhost
- Dal briefing si sceglie **Checklists: Complete / Simplified**: la modalità Simplified nasconde le voci elencate in `checklist_simplified.json` (id di `checklist.json`); il cockpit state e i valori reali restano completi.
- `python test_brain.py` e `python test_web.py` — test (logica e API della pagina)

Scheda e decisioni nel second brain: `life/virtual-captain/progetto.md`.
