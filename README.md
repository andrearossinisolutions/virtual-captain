# Virtual Captain

Comandante virtuale per MSFS 2024: tu sei il First Officer, lui ti ricorda cosa fare (positive rate/gear up, acceleration altitude, transition altitude, 10.000 ft). Legge i dati dal simulatore via SimConnect (solo lettura, nessuna scrittura sul simulatore; solo Fenix A320), parla con la voce locale di Windows.

- `python launcher.py [argomenti]` (o doppio clic su `start.bat` su Windows) — fa `git pull --ff-only` e poi avvia `main.py` con gli stessi argomenti; se il pull fallisce lo scrive e avvia la versione presente
- `python main.py` — avvia la pagina web del briefing e la pagina è raggiungibile su questo PC (127.0.0.1). Si compila il briefing (SimBrief ID or username → "Load latest plan", campi modificabili in ogni momento); il lettore SimConnect parte all'apertura del programma; con **Briefing complete** il capitano legge i dati e parla; **Back to briefing** mette in pausa.
- `python main.py --test-links` — prova dei collegamenti: valuta i link di `checklist_links.json` come verificati sui valori reali e mostra accanto a ogni voce il grezzo letto (es. `S_OH_ELEC_BAT1=1`)
- `python main.py --sim` — decollo simulato, senza SimConnect (prova voce e soglie)
- `--port 8080`, `--host <IP>` — la pagina si lega di default a 127.0.0.1 (solo questo PC); nessuna regola del firewall, nessun permesso di amministratore
- Dal briefing si sceglie **Checklists: Complete / Simplified**: la modalità Simplified nasconde le voci elencate in `checklist_simplified.json` (id di `checklist.json`); il cockpit state e i valori reali restano completi.
- Collegamenti della checklist (`checklist_links.json`): `{"show": [chiavi]}` mostra solo il valore letto senza esito (nella pagina sempre la riga grigia "NOW: valore"); `"optional": true` rende azzurro (○, non conta come out of spec) ciò che non è conforme (es. Flaps FULL as required).
- **`checklist.json` è la fonte di verità** della checklist (fasi, sotto-fasi, voci, id): si modifica a mano. `tools/build_checklist.py` era solo lo strumento della prima generazione dal testo sorgente e non va più usato per sovrascriverlo.
- **Departure Clearance e CRAFT**: le fasi sono FMGS INITIALIZATION, Departure Clearance (voci CLEARANCE e blocco CRAFT dopo "Obtain Departure Clearance") e FMGS PERFORMANCE & GLARESHIELD. Il blocco CRAFT (voce `type: "craft"`, nessun link ammesso) ha cinque campi da compilare dal tablet: Cleared to e Route (precompilati dal piano SimBrief, con "Reset to plan"), Altitude, Frequency (departure) e Transponder (squawk, 4 cifre 0-7). I valori stanno solo in memoria (`/api/clearance`, snapshot `clearance`): si azzerano al riavvio, a un nuovo piano e al ritorno in briefing; non entrano in `~/.virtual-captain.json`.
- Valore atteso nei collegamenti: `{"key": k, "eq_ref": "clr.t"}` confronta con un riferimento chiuso (`clr.c`/`clr.r`/`clr.a`/`clr.f`/`clr.t`, `fld.runway`, `fld.ta`, `plan.sid`) invece di un'etichetta fissa (solo variabili senza mappa di etichette, numeriche confrontate normalizzate; atteso vuoto = nessun esito, solo NOW); `{"show_ref": "clr.a"}` mostra solo "Expected: <valore>" per le voci senza variabile reale (Initial Climb, T/O RWY, SID, Transition Altitude).
- - QNH reale: partenza e arrivo confrontano il QNH impostato (EFIS 1) con il METAR di aviationweather.gov (±1 hPa; ripiego sul METAR del piano SimBrief); STD = rosso.
- **Detent delle leve** (nessuna calibrazione): `thrust.py` li legge direttamente dalle LVar del Fenix, per ogni leva `A_FC_THROTTLE_*_INPUT` (`thr_in_l`/`thr_in_r`, 0..5) e `A_FC_THROTTLE_*_GATE` (`thr_gate_l`/`thr_gate_r`, 10..5, gate = 10 - input); REVERSE, REVERSE IDLE, IDLE, CLIMB, FLX, TOGA = 0..5 e valori `thr_det_l`/`thr_det_r` (BETWEEN se input e gate non coincidono con un detent, n/d se non leggibili). Collegamento `{"detent": "IDLE"}` o `{"detent": ["FLX", "TOGA"]}`: ok solo se entrambe le leve sono nel detent; sempre attivo. Altri aerei senza queste LVar: n/d.
- `python test_brain.py`, `python test_web.py` e gli altri `test_*.py` (`test_thrust.py` per i detent) — test (logica e API della pagina)

Scheda e decisioni nel second brain: `life/virtual-captain/progetto.md`.

## SimConnect (dal 17/11/2026, al posto di AAO)

- Legge ~2 volte al secondo (cache aggiornata da un thread, il ciclo principale la legge ogni 0,5 s; dati piu' vecchi di 5 s = non connesso). Una variabile assente o sconosciuta vale **0** (nessun "n/d"); compatibilita' solo Fenix A320, nessun ripiego sulle variabili standard.
- La **SimConnect.dll** (64 bit) si cerca in ordine: `--simconnect-dll PERCORSO`, variabile `CAPTAIN_SIMCONNECT_DLL`, accanto a `main.py`, `vendor/`, SDK di MSFS (`MSFS_SDK`, `C:\MSFS 2024 SDK`), pacchetto pip. Se manca, nelle impostazioni della pagina compare **Download SimConnect.dll**: dopo una conferma che mostra origine, SHA-256 e dimensione scarica da PyPI (`files.pythonhosted.org`) il pacchetto SimConnect fissato per hash, ne estrae solo la DLL e la salva in `vendor/` (ignorata da git). **Retry** riprova subito.
- Diagnosi in sola lettura: `python simconnect_backend.py --probe [--simconnect-dll PERCORSO]` stampa i valori letti (0 = assente o sconosciuta).
- Rimossi `--aao`, `CAPTAIN_AAO_URL` e l'indirizzo AAO nella pagina; il vecchio `aao_url` in `~/.virtual-captain.json` viene ignorato. Ritorno alla versione con AAO: tag `v0.1.0`.

## Collegamento al server pubblico (uplink, U1)

Il programma si collega da solo al server pubblico (`https://virtualcaptain.rossinisolutions.com`, sovrascrivibile con `--server-url`): e' **acceso di default**. La pagina locale resta sempre attiva e SimConnect e la voce non dipendono mai dal collegamento (nessun blocco se il server e' irraggiungibile).

- **Nuova dipendenza**: `websockets` (fissata in `requirements.txt`). Il launcher installa da solo i requirements (`pip install -r requirements.txt`) quando il file cambia (hash in `.requirements.sha256`, locale); se pip fallisce lo scrive e avvia comunque. Se il pull aggiorna `launcher.py` stesso la nuova logica parte dal lancio successivo: dopo quel primo aggiornamento fai un secondo avvio o `pip install -r requirements.txt` a mano. Per i test: `pip install -r requirements-dev.txt` (pytest, non serve sul PC di gioco). Senza la libreria il programma parte lo stesso e lo scrive in console.
- Al primo avvio, senza token, la finestra **Link to server** mostra un **codice** e il link (pulsanti Open e Copy, niente QR): apri il link, accedi al sito, controlla il codice e digita gli ultimi 4 caratteri; poi la pagina `/waiting` diventa "Program connected".
- Opzioni: `--server` (abilita e salva), `--no-server` o `--local` (nessun uplink in questa esecuzione), `--server-url URL` (salvato; http solo su localhost), `--link` (nuovo collegamento), `--unlink` (revoca sul server e cancella i token, poi esce; se il server non risponde i token restano per riprovare). `--no-remote-commands` (o `"remote_commands": false` in `uplink.json`) impedisce al sito di comandare il programma: il sito mostra solo lo stato. Comandi ammessi dal sito (riconvalidati sul PC): campi e CRAFT, fasi della checklist, caricamento del piano SimBrief, Briefing complete / ritorno al briefing, Retry di SimConnect; download della DLL e calibrazione delle leve restano solo sulla pagina locale.
- I token sono salvati in `%APPDATA%\VirtualCaptain\link.dat` (cifrati con DPAPI, legati all'utente di Windows e a questo PC; se `LOCALAPPDATA` manca si usa `APPDATA`); su altri sistemi in `~/.config/virtual-captain/link.dat` con permessi 0600, **non cifrati** (avviso in console). Accanto, `uplink.json` (acceso/spento, indirizzo) e il lock di istanza: una seconda istanza parte senza uplink.
- Stati ed errori dell'uplink nel log, in console solo con `--no-gui` (mai token ne' codici nei log). Se un'altra istanza prende il collegamento (altro PC, stesso account) questa non si riconnette; se l'autorizzazione e' revocata si riavvia il programma o si usa `--link`.
- **Cartella `server/`**: il programma richiede `server/wire.py` accanto a `protocol.py` (`protocol.py` lo importa). Si aggiorna con `git pull` (launcher), mai copiando a mano singoli file.
- `--link`: i vecchi token restano finche' il nuovo collegamento non riesce; poi i nuovi li sostituiscono e si tenta (best effort, 3 s) di revocare la famiglia vecchia.
- Test: `python3 -m pytest test_secure_store.py test_uplink.py`.

### Snapshot del volo e pagina `/flight` (U2)

Con il collegamento attivo il programma manda al server, al massimo 1 volta al secondo e solo se cambia, uno **snapshot** del volo (`snapshot.py`, filtrato da un'allowlist in `server/wire.py`). **La posizione (lat/lon) e i dati sensibili (SimBrief ID, errori) non escono dal PC.** Dal sito, `Start flight` apre `/flight`: pagina in **sola lettura** con le stesse viste del pannello (checklist della fase corrente, pill, cockpit, log); in Briefing mostra solo "Briefing in progress on the PC". Il thread dell'uplink legge solo `State.snapshot()`: non tocca SimConnect ne' la voce. Nessuna nuova dipendenza. Test: `python3 -m pytest test_snapshot.py test_uplink.py test_flight_page.py server/`.

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

Cartella `server/` (FastAPI + uvicorn, dietro Apache): al passo 1 serve solo una pagina segnaposto e `/healthz` (con lo sha del deploy), nessuna API, nessun legame col programma sul PC; dal passo U2 serve anche `/app.js` e `/app.css` da `web/` (whitelist) per la pagina `/flight`. Il deploy e' automatico: un push su `main` che tocca `server/` (escluse `server/deploy/` e i `.md`), `web/` o il workflow lancia `.github/workflows/deploy-server.yml` sul runner del VPS. **La repo e' pubblica: nessun segreto nei file.** Preparazione del VPS (passi A-I, verifiche e ritorni indietro): `server/DEPLOY.md`.

Prova locale (venv fuori dalla repo): `pip install -r server/requirements-dev.txt`, poi `python -m pytest server/test_server.py` (senza fastapi i test del server vengono saltati) e `uvicorn server.app:app --host 127.0.0.1 --port 8100`.

## File di sessione del volo

`~/.virtual-captain-session.json` (separato da `~/.virtual-captain.json`) contiene i **dati del volo** (campi, piano senza coordinate, clearance con squawk, fase, stato del capitano e della checklist) per riprendere dopo un crash o un riavvio. Scrittura atomica con permessi 0600 su macOS/Linux; **su Windows 0600 non vale**: il file e' leggibile dall'utente del PC come gli altri file della home. Si cancella con End flight o con un nuovo piano; non e' mai scritto ne' letto con `--sim`. Si ripristina solo se e' stato salvato meno di 6 ore prima; in volo il capitano non ripete callout ne' briefing parlato e, se il sim mostra l'aereo fermo a terra a motori spenti, il volo e' considerato finito. Fine volo rilevata (dopo essere stati in aria in questa esecuzione: a terra, fermo, motori spenti, parking brake): solo il file viene cancellato, il volo resta in "flying" con checklist e stato (il ritorno al briefing e' End flight manuale). "Essere stati in aria" e' solo in memoria: se il programma si riavvia dopo l'atterraggio la fine volo non si rileva, ed e' un fallimento sicuro (il file resta e basta End flight). Con End flight il file e' cancellato e il briefing resta compilato sulla pagina ma senza file (un riavvio lo perde); un nuovo "Briefing complete" parte come volo nuovo, con il briefing parlato. Un riavvio prima di "Briefing complete" riparte dal briefing.
