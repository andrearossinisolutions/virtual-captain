# Virtual Captain

Comandante virtuale per MSFS 2024: tu sei il First Officer, lui ti ricorda cosa fare (positive rate/gear up, acceleration altitude, transition altitude, 10.000 ft). Legge i dati dal simulatore via SimConnect (solo lettura, nessuna scrittura sul simulatore; solo Fenix A320), parla con la voce locale di Windows.

- `python launcher.py [argomenti]` (o doppio clic su `start.bat` su Windows) — fa `git pull --ff-only` e poi avvia `main.py` con gli stessi argomenti; se il pull fallisce lo scrive e avvia la versione presente
- `python main.py` — avvia la pagina web del briefing e stampa l'indirizzo da aprire dal tablet (via Tailscale). Dal tablet si compila il briefing (SimBrief ID or username → "Load latest plan", campi modificabili in ogni momento); il lettore SimConnect parte all'apertura del programma; con **Briefing complete** il capitano legge i dati e parla; **Back to briefing** mette in pausa.
- `python main.py --test-links` — prova dei collegamenti: valuta i link di `checklist_links.json` come verificati sui valori reali e mostra accanto a ogni voce il grezzo letto (es. `S_OH_ELEC_BAT1=1`)
- `python main.py --sim` — decollo simulato, senza SimConnect (prova voce e soglie)
- `--port 8080`, `--host <IP>`, `--no-firewall` — al primo avvio su Windows il programma chiede il permesso (UAC) di creare la regola del firewall per la porta, solo per la rete Tailscale (100.64.0.0/10, quindi non dipende dall'IP); — la pagina si lega solo all'IP di Tailscale (mai a tutte le reti); senza Tailscale resta su localhost
- Dal briefing si sceglie **Checklists: Complete / Simplified**: la modalità Simplified nasconde le voci elencate in `checklist_simplified.json` (id di `checklist.json`); il cockpit state e i valori reali restano completi.
- Collegamenti della checklist (`checklist_links.json`): `{"show": [chiavi]}` mostra solo il valore letto senza esito (nella pagina sempre la riga grigia "NOW: valore"); `"optional": true` rende azzurro (○, non conta come out of spec) ciò che non è conforme (es. Flaps FULL as required).
- **`checklist.json` è la fonte di verità** della checklist (fasi, sotto-fasi, voci, id): si modifica a mano. `tools/build_checklist.py` era solo lo strumento della prima generazione dal testo sorgente e non va più usato per sovrascriverlo.
- **Departure Clearance e CRAFT**: le fasi sono FMGS INITIALIZATION, Departure Clearance (voci CLEARANCE e blocco CRAFT dopo "Obtain Departure Clearance") e FMGS PERFORMANCE & GLARESHIELD. Il blocco CRAFT (voce `type: "craft"`, nessun link ammesso) ha cinque campi da compilare dal tablet: Cleared to e Route (precompilati dal piano SimBrief, con "Reset to plan"), Altitude, Frequency (departure) e Transponder (squawk, 4 cifre 0-7). I valori stanno solo in memoria (`/api/clearance`, snapshot `clearance`): si azzerano al riavvio, a un nuovo piano e al ritorno in briefing; non entrano in `~/.virtual-captain.json`.
- Valore atteso nei collegamenti: `{"key": k, "eq_ref": "clr.t"}` confronta con un riferimento chiuso (`clr.c`/`clr.r`/`clr.a`/`clr.f`/`clr.t`, `fld.runway`, `fld.ta`, `plan.sid`) invece di un'etichetta fissa (solo variabili senza mappa di etichette, numeriche confrontate normalizzate; atteso vuoto = nessun esito, solo NOW); `{"show_ref": "clr.a"}` mostra solo "Expected: <valore>" per le voci senza variabile reale (Initial Climb, T/O RWY, SID, Transition Altitude).
- - QNH reale: partenza e arrivo confrontano il QNH impostato (EFIS 1) con il METAR di aviationweather.gov (±1 hPa; ripiego sul METAR del piano SimBrief); STD = rosso.
- **Calibrazione thrust levers** (Technical settings › Thrust levers calibration, anche in Briefing con SimConnect collegato): il wizard fa muovere entrambe le leve insieme su FULL REVERSE, REVERSE IDLE, IDLE, CLIMB, FLEX/MCT, TOGA (Next a ogni passo, Back/Skip/Cancel). Registra tutte le variabili thrust (mediana delle ultime 3 letture) e per ogni leva sceglie la prima variabile guida con i 6 valori monotoni (L: `thr_in_l`, `thr_pos_1`, `thr_gate_l`; R: `thr_in_r`, `thr_pos_2`, `thr_gate_r`); `thr_pos_3/4` e `thr_rev_*` solo diagnostica. Il risultato sta in `~/.virtual-captain.json` (`thrust_cal`) e produce i valori virtuali `thr_det_l`/`thr_det_r` (REVERSE, REVERSE IDLE, IDLE, CLIMB, FLX, TOGA, BETWEEN). Collegamento `{"detent": "IDLE"}` o `{"detent": ["FLX", "TOGA"]}`: ok solo se entrambe le leve sono nel detent; senza calibrazione solo "NOW". "Clear calibration" la cancella.
- `python test_brain.py`, `python test_web.py` e gli altri `test_*.py` (`test_thrust.py` per la calibrazione) — test (logica e API della pagina)

Scheda e decisioni nel second brain: `life/virtual-captain/progetto.md`.

## SimConnect (dal 17/11/2026, al posto di AAO)

- Legge ~2 volte al secondo (cache aggiornata da un thread, il ciclo principale la legge ogni 0,5 s; dati piu' vecchi di 5 s = non connesso). Una variabile assente o sconosciuta vale **0** (nessun "n/d"); compatibilita' solo Fenix A320, nessun ripiego sulle variabili standard.
- La **SimConnect.dll** (64 bit) si cerca in ordine: `--simconnect-dll PERCORSO`, variabile `CAPTAIN_SIMCONNECT_DLL`, accanto a `main.py`, `vendor/`, SDK di MSFS (`MSFS_SDK`, `C:\MSFS 2024 SDK`), pacchetto pip. Se manca, nelle impostazioni della pagina compare **Download SimConnect.dll**: dopo una conferma che mostra origine, SHA-256 e dimensione scarica da PyPI (`files.pythonhosted.org`) il pacchetto SimConnect fissato per hash, ne estrae solo la DLL e la salva in `vendor/` (ignorata da git). **Retry** riprova subito.
- Diagnosi in sola lettura: `python simconnect_backend.py --probe [--simconnect-dll PERCORSO]` stampa i valori letti (0 = assente o sconosciuta).
- Rimossi `--aao`, `CAPTAIN_AAO_URL` e l'indirizzo AAO nella pagina; il vecchio `aao_url` in `~/.virtual-captain.json` viene ignorato. Ritorno alla versione con AAO: tag `v0.1.0`.

## Anti-Ice dinamico (Engine Anti-Ice)

Le voci anti-ice della checklist mostrano un testo calcolato al posto di "AS REQUIRED"; sul sim si fa solo lettura.
Logica pura in `icing.py` (`icing_assessment`, `ai_rule`); soglie e liste di fenomeni in `monitor.py` (costanti `AI_*`).
Regole e soglie vengono da PPRuNe, guide FlyByWire e flashcards, non dal FCOM Fenix: da verificare sul FCOM Fenix.

- **Icing conditions**: temperatura <= 10 C e umidita' visibile dal METAR. Contano RA, DZ, SN, SG, PL, GR, GS, UP, FG, TS, CB/TCU, VV; BR solo con visibilita' <= 1600 m (anche una visibilita' <= 1600 m senza codice); in volo anche BKN/OVC (FEW/SCT escluse). HZ, FU, DU, SA, IC, CAVOK, NSW non contano.
- **Temperatura**: a terra quella del METAR di partenza (ripiego sulla SAT del sim se a terra e diversa da 0), in volo la TAT (`A:TOTAL AIR TEMPERATURE`, da verificare dal vivo). Variabile assente = 0.
- **METAR assente**: nessun esito colorato, testo "no METAR, check manually" (mai "no icing" senza dati). Il METAR viene da aviationweather.gov con ripiego sul piano SimBrief; le tabelle ATIS non hanno il campo Weather.
- **METAR usato**: l'online non scaduto (1 h) e' l'unico dato, senza fusione per campo col piano; il METAR del piano SimBrief vale solo se l'online manca o e' scaduto e non ha altra scadenza (puo' essere vecchio). Il METAR e' "sufficiente" solo con il testo grezzo (`raw`): un dato parziale vale come METAR assente. Nel testo ATTIVO compaiono ICAO, fenomeni, visibilita' e temperatura, cosi' si vede cosa e' stato usato.
- **Visibilita' US**: la riga Vis mostra anche i METAR US in miglia ("Vis 10 SM"): voluto.
- **Voci**: dopo l'avviamento (a terra, METAR di partenza) ATTIVA: con icing eng1_ai e eng2_ai devono essere ON, altrimenti rosso. After takeoff e climb solo promemoria (mai colorate). Descent preparation ATTIVA: SAT < -40 C richiede ON (indipendente dal METAR); con SAT >= -40 richiede ON se il METAR di destinazione da' icing conditions con TAT <= 10 C. Parking invariato (OFF), wing anti-ice in approach rimandato.
- **Check** `{"ai": "ground|takeoff|climb|descent"}` in `checklist_links.json` (solo da solo, mai dentro `all`).
- Test: `python3 test_icing.py`, `test_weather.py`, `test_monitor.py`, `test_checklist.py`, `test_checklist_eval.py`.

## Pagina web

La pagina vive in `web/` (`index.html`, `app.css`, `app.js`). `web.py` la assembla in una sola pagina con CSS e JS inline (`build_page()`, servita su `/`) e serve `/app.css` e `/app.js` da una whitelist. `protocol.py` elenca i comandi remoti e quelli solo locali.

## Server pubblico

Cartella `server/` (FastAPI + uvicorn, dietro Apache): al passo 1 serve solo una pagina segnaposto e `/healthz` (con lo sha del deploy), nessuna API, nessun legame col programma sul PC; non serve ancora `web/`. Il deploy e' automatico: un push su `main` che tocca `server/` (escluse `server/deploy/` e i `.md`), `web/` o il workflow lancia `.github/workflows/deploy-server.yml` sul runner del VPS. **La repo e' pubblica: nessun segreto nei file.** Preparazione del VPS (passi A-I, verifiche e ritorni indietro): `server/DEPLOY.md`.

Prova locale (venv fuori dalla repo): `pip install -r server/requirements-dev.txt`, poi `python -m pytest server/test_server.py` (senza fastapi i test del server vengono saltati) e `uvicorn server.app:app --host 127.0.0.1 --port 8100`.
