# Deploy del server pubblico (VPS IONOS)

Sito: `virtualcaptain.rossinisolutions.com` (+ `www`). Ubuntu 24.04, Apache, systemd, runner GitHub self-hosted.
Passo 1: solo pagina segnaposto e `/healthz`. Si fa tutto a mano sul VPS **prima** del primo push.

## ATTENZIONE: la repo e' PUBBLICA

- **Nessun segreto nella repo**: niente password, token, chiavi, `.env`. I segreti vivono solo in `/etc/virtual-captain/env` sul VPS. In `server/env.example` ci sono solo i nomi.
- Il runner self-hosted esegue codice sul VPS: su GitHub, **Settings > Actions > General > Fork pull request workflows > "Require approval for all outside collaborators"**.
- **Mai** usare `pull_request` o `pull_request_target` nei workflow che girano sul runner self-hosted (chiunque potrebbe far eseguire codice sul VPS). Il workflow `deploy-server.yml` parte solo da push su `main` e da avvio manuale.

Convenzione: `$` = comando da digitare. Sudo chiede la password (la digiti tu). Ogni passo ha **Verifica** e **Ritorno indietro**.

## A. Verifiche preliminari

```
$ python3 --version              # atteso 3.12.x
$ python3 -m venv --help | head -1
$ python3 -c "import ensurepip"  # nessun errore
$ rsync --version | head -1
$ curl --version | head -1
$ free -m                        # serve memoria libera sufficiente: il service e' limitato a 300 MB (con poca RAM libera valuta di abbassare MemoryMax)
$ sudo ss -ltnp | grep 8100      # nessun output: la porta e' libera
```
Se manca `python3-venv`: `sudo apt install python3-venv`. Se manca rsync/curl: `sudo apt install rsync curl`.
**Ritorno indietro**: nulla da annullare (solo letture; `apt remove` per i pacchetti aggiunti).

## B. Cartelle

```
$ sudo mkdir -p /var/www/virtual-captain/{current,shared,previous,scripts}
$ sudo chown -R <UTENTE_VPS>:<UTENTE_VPS> /var/www/virtual-captain
$ sudo chown root:root /var/www/virtual-captain/scripts
```
**Verifica**: `ls -la /var/www/virtual-captain` mostra le quattro cartelle (`venv` nasce al primo deploy).
**Ritorno indietro**: `sudo rm -r /var/www/virtual-captain` (solo se e' tutto da rifare).

## C. Installare deploy.sh

Dalla copia della repo (o incollando il file) :
```
$ sudo install -o root -m 755 -g root server/deploy/deploy.sh /var/www/virtual-captain/scripts/deploy.sh
```
**Verifica**: `ls -l /var/www/virtual-captain/scripts/deploy.sh` -> `-rwxr-xr-x root root`; `bash -n` sullo stesso file senza errori.
**Ritorno indietro**: `sudo rm /var/www/virtual-captain/scripts/deploy.sh`.
Nota: root-owned di proposito, cosi' il runner non puo' riscriverlo. Ogni modifica a `deploy.sh` va reinstallata a mano con lo stesso comando.

## D. File env (vuoto per ora)

```
$ sudo mkdir -p /etc/virtual-captain
$ sudo touch /etc/virtual-captain/env
$ sudo chown root:root /etc/virtual-captain/env
$ sudo chmod 600 /etc/virtual-captain/env
```
**Verifica**: `sudo ls -l /etc/virtual-captain/env` -> `-rw------- root root`.
**Ritorno indietro**: `sudo rm /etc/virtual-captain/env`.

## E. Servizio systemd (abilitato, NON avviato)

```
$ sudo cp server/deploy/virtual-captain.service /etc/systemd/system/virtual-captain.service
$ sudo systemctl daemon-reload
$ sudo systemctl enable virtual-captain
```
Non avviarlo: il codice arriva col primo deploy.
**Verifica**: `systemctl is-enabled virtual-captain` -> `enabled`; `systemd-analyze verify` va fatto solo dopo il primo deploy (prima segnala l'errore atteso che manca l'eseguibile in `venv/`).
Il file `virtual-captain.service` ha `User=`/`Group=` dell'utente reale: sostituiscilo con il tuo (`<UTENTE_VPS>`) prima di copiarlo. Se il service non parte, il primo sospetto sono le restrizioni di sicurezza aggiunte (`ProtectSystem=strict`, `PrivateDevices`, `ProtectKernelTunables`, `RestrictAddressFamilies`): non provate sul VPS, rimuovile una alla volta guardando `journalctl -u virtual-captain -e`.
**Ritorno indietro**: `sudo systemctl disable virtual-captain && sudo rm /etc/systemd/system/virtual-captain.service && sudo systemctl daemon-reload`.

## F. Sudoers (riavvio senza password, solo di questo servizio)

```
$ sudo visudo -f /etc/sudoers.d/virtual-captain
```
Incolla la riga di `server/deploy/sudoers-virtual-captain` (`<UTENTE_VPS> ALL=(root) NOPASSWD: /usr/bin/systemctl restart virtual-captain`, con il tuo utente al posto del segnaposto), salva. Controlla il percorso: `which systemctl` deve essere `/usr/bin/systemctl`.
**Verifica**: `sudo visudo -c`; `sudo -l -U <UTENTE_VPS> | grep virtual-captain`.
**Ritorno indietro**: `sudo rm /etc/sudoers.d/virtual-captain`.

## G. Apache porta 80 + certificato

```
$ sudo cp server/deploy/apache-80.conf /etc/apache2/sites-available/virtual-captain.conf
$ sudo a2ensite virtual-captain
$ sudo apachectl configtest && sudo systemctl reload apache2
$ sudo certbot --apache -d virtualcaptain.rossinisolutions.com -d www.virtualcaptain.rossinisolutions.com
```
Il DNS deve gia' puntare all'IP del VPS (<IP_VPS>; controlla con `dig +short virtualcaptain.rossinisolutions.com`).
**Verifica**: `curl -I http://virtualcaptain.rossinisolutions.com` risponde (redirect a https se scegli la redirect in certbot); `sudo ls /etc/letsencrypt/live/`.
**Ritorno indietro**: `sudo a2dissite virtual-captain && sudo systemctl reload apache2`; il certificato: `sudo certbot delete --cert-name virtualcaptain.rossinisolutions.com`.

## H. Apache SSL definitivo

certbot ha creato `/etc/apache2/sites-available/virtual-captain-le-ssl.conf`. Confrontalo con `server/deploy/apache-ssl.conf.example` e porta dentro: redirect di `www`, HSTS `max-age=31536000` (senza includeSubDomains né preload), `ProxyPass` di `/` e `/ws/` verso `127.0.0.1:8100`, mantenendo le righe `SSLCertificate*` generate da certbot. Moduli: `sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers ssl`.
```
$ sudo apachectl configtest && sudo systemctl reload apache2
```
**Verifica**: finche' il servizio non gira, `https://virtualcaptain.rossinisolutions.com` da' 503: e' normale. `https://www...` deve fare 301 verso il dominio senza www.
**Ritorno indietro**: rimetti la copia di backup del file (fanne una prima: `sudo cp virtual-captain-le-ssl.conf virtual-captain-le-ssl.conf.bak`) e `sudo systemctl reload apache2`.

## I. Runner e primo push

1. Su GitHub, **Settings > Actions > General > "Fork pull request workflows from outside collaborators"** (il nome puo' cambiare): scegli **"Require approval for all outside collaborators"** e salva. Va fatto PRIMA di registrare il runner.
2. Controlla il runner: `systemctl status 'actions.runner.*'` (attivo, etichette `self-hosted, linux, x64`, utente `<UTENTE_VPS>` che puo' scrivere in `/var/www/virtual-captain`). La repo `virtual-captain` deve essere collegata al runner (se il runner e' per repo, va registrato anche per questa).
3. Solo ora fai il push (o "Run workflow" dalla scheda Actions).

**Verifica**:
```
$ curl -s https://virtualcaptain.rossinisolutions.com/healthz      # {"status":"ok","rev":"<sha>"}
$ curl -s -D - -o /dev/null https://virtualcaptain.rossinisolutions.com/ | grep -iE 'content-security|nosniff|x-robots'
$ curl -s -o /dev/null -w '%{http_code}\n' https://virtualcaptain.rossinisolutions.com/docs   # 404
```
Anche 404 per `/openapi.json`, `/api/x`, `/app.js`, `/server/app.py`. Un push che tocca solo i programmi del PC (o `server/*.md`, `server/deploy/**`) **non** deve far partire il workflow; uno su `server/` o `web/` si', e il `rev` cambia.
**Ritorno indietro (codice)**: il deploy fallito torna da solo a `previous/`. A mano: `rsync -a --delete /var/www/virtual-captain/previous/ /var/www/virtual-captain/current/ && sudo systemctl restart virtual-captain`. Prova il rollback una volta.
**Ritorno indietro (tutto)**: `sudo systemctl disable --now virtual-captain`, `a2dissite`, rimuovi cartelle e sudoers come sopra.

## Limiti noti

Gli header di sicurezza non vengono aggiunti alle risposte 500 non gestite (limite di Starlette): in quel caso si vede un 500 nudo.

## Log

`journalctl -u virtual-captain -e`, `sudo tail /var/log/apache2/virtual-captain-error.log`.

## Email (passo 2a: prova di invio)

Il server non importa ancora il modulo mail: questo passo serve solo a provare che l'invio funziona. **Non** serve un deploy per cambiare le variabili, ma il codice (`server/mail.py`, `mailtest.py`) deve essere gia' arrivato in `/var/www/virtual-captain/current` col deploy normale.

### Variabili

In `/etc/virtual-captain/env` (root, 600), **NOME=valore, una per riga, senza spazi attorno a `=`**:
```
SMTP_HOST=<server SMTP del provider>
SMTP_PORT=587
SMTP_USER=<casella principale autenticata>
SMTP_PASSWORD=<password della casella>
MAIL_FROM=<indirizzo mittente (alias)>
MAIL_FROM_NAME=Virtual Captain          (opzionale)
SMTP_TIMEOUT=15                         (opzionale)
MAIL_ENVELOPE_FROM=<casella principale> (opzionale, vedi "Alias rifiutato")
```
Scrivile a mano: `sudo nano /etc/virtual-captain/env`. **La password mai in chat, mai in un comando che resta nella history.** Alternativa che non lascia traccia nella history:
```
$ [ -e /etc/virtual-captain/env ] || sudo install -m 600 -o root -g root /dev/null /etc/virtual-captain/env
$ read -rs -p "Password: " P; echo
$ printf '\nSMTP_PASSWORD=%s\n' "$P" | sudo tee -a /etc/virtual-captain/env > /dev/null; unset P
```
`read -rs -p` funziona in **bash** (sul VPS); in zsh `-p` significa altro (coprocess). Il `\n` iniziale evita che la riga si attacchi all'ultima se il file non finisce con un a-capo (lascia al massimo una riga vuota).
Attenzione: il file e' letto da systemd, non da una shell. **Niente virgolette se non servono**. Rischi reali di `EnvironmentFile`: un backslash finale (continua la riga), spazi iniziali/finali tolti, virgolette ai due lati interpretate (e tolte); `$` e `%` nei valori non quotati **non** vengono espansi; un `#` a meta' riga **non** e' un commento. Se l'autenticazione fallisce senza motivo, prova una password senza questi caratteri. La password deve essere ASCII semplice (senza accenti) e su una sola riga.
L'utente del servizio non legge il file, ma systemd si': `-p EnvironmentFile=` con `sudo systemd-run` funziona anche con il file root 600.
**Verifica**: `sudo ls -l /etc/virtual-captain/env` -> `-rw------- root root` (mai `cat` del file in chat).
**Ritorno indietro**: togli la riga con `sudo nano` (se una variabile e' duplicata vale l'ultima).

### Prova

Prima solo connessione e login (non invia nulla):
```
$ sudo systemd-run --quiet --wait --pipe --collect -p User=<UTENTE_VPS> -p WorkingDirectory=/var/www/virtual-captain/current -p EnvironmentFile=/etc/virtual-captain/env /var/www/virtual-captain/venv/bin/python -m server.mailtest --check
```
Poi l'invio vero (stesso comando, `--check` sostituito dal destinatario):
```
$ sudo systemd-run --quiet --wait --pipe --collect -p User=<UTENTE_VPS> -p WorkingDirectory=/var/www/virtual-captain/current -p EnvironmentFile=/etc/virtual-captain/env /var/www/virtual-captain/venv/bin/python -m server.mailtest <destinatario>
```
Esito: `OK`, oppure `ERROR <tipo>` con tipo `config` (variabile mancante o non valida: ne indica il nome; destinatario non valido), `auth` (utente/password), `tls` (STARTTLS assente o certificato non valido), `timeout`, `connection`, `sender` (mittente rifiutato), `recipient` (destinatario rifiutato), `internal` (errore imprevisto, nessun dettaglio). Exit code diverso da 0 in errore (2 per config o uso sbagliato del comando, 3 per gli altri). Il destinatario e' un indirizzo semplice ASCII, senza nome ne' liste.
Fai **due prove**: una su una casella Gmail e una su una casella del dominio.

### Controlli di consegna (SPF/DKIM/DMARC)

```
$ dig +short TXT <dominio-mittente>                      # record SPF (v=spf1 ...)
$ dig +short TXT <selettore>._domainkey.<dominio-mittente>   # DKIM (selettore indicato dal provider)
$ dig +short TXT _dmarc.<dominio-mittente>               # DMARC
```
Poi apri la mail ricevuta (Gmail: tre puntini > "Mostra originale") e controlla `Authentication-Results`: servono `spf=pass`, `dkim=pass` e la mail in **inbox** (non spam). **Non si passa al passo 2b finche' non e' cosi'.**

### Alias rifiutato

Se il server rifiuta il mittente (errore di tipo `sender`), prova a impostare `MAIL_ENVELOPE_FROM=<casella principale>`: la mail parte con quell'indirizzo come mittente di busta, il `From` resta l'alias e viene aggiunto `Reply-To` con l'alias. Si puo' provare anche da riga di comando: `... -m server.mailtest <destinatario> --envelope-from <casella principale>`. Ultima spiaggia: `MAIL_FROM` = casella principale.

## Account (passo 2b-1: API; 2b-2: pagine web)

Nuove variabili in `/etc/virtual-captain/env` (NOME=valore, una per riga; i valori non vanno in repo ne' in chat):

- `SECRET_KEY` (**obbligatoria se imposti `PUBLIC_BASE_URL`**, min 32 caratteri): sale degli hash di IP e indirizzi nei limiti di frequenza. Generala a mano sul VPS: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` e incolla il risultato con `sudo nano /etc/virtual-captain/env`. **Se il servizio non parte dopo aver toccato `/etc/virtual-captain/env`, guarda `journalctl -u virtual-captain -e`** (il messaggio nomina la variabile sbagliata, mai il valore). Se la cambi, i contatori dei limiti si azzerano. **Impostala PRIMA di `PUBLIC_BASE_URL`**: con `PUBLIC_BASE_URL` e senza `SECRET_KEY` il servizio rifiuta di partire (messaggio chiaro nel journal).
- `PUBLIC_BASE_URL`: origine pubblica https senza slash finale e senza percorso (`http` ammesso solo per localhost/127.0.0.1/::1). Serve per i link nelle mail e per il controllo `Origin`. Se manca, l'API `/api/auth/*` **non viene montata** e nel journal compare un warning ("NOT mounted").
- `DB_PATH` (facoltativa): file SQLite. Default `/var/www/virtual-captain/shared/virtual-captain.db`. Viene creato col primo uso, permessi 600 (anche `-wal`/`-shm`); la cartella `shared` deve essere scrivibile dall'utente del servizio.
- Limiti (facoltative, default tra parentesi): `RATE_IP_HOURLY` (5), `RATE_IP_DAILY` (20), `RATE_MAIL_ADDR_HOURLY` (3), `RATE_MAIL_DAILY` (200, **per tipo di mail**: verifica, reset, altre), `LOGIN_MAX_FAILURES` (5), `LOCKOUT_MINUTES` (15), `RATE_LOGIN_IP_15MIN` (30), `RATE_PREVIEW_IP_HOURLY` (20, bucket dedicato all'anteprima del link in `/verify`; la verifica vera resta a 5/ora con `RATE_IP_HOURLY`). I limiti per IP valgono separatamente per registrazione/forgot, reset, verifica e anteprima (quattro bucket distinti); gli IPv6 sono raggruppati per /64.

### X-Forwarded-For

L'IP del client si legge da `X-Forwarded-For` **solo** se la richiesta arriva da 127.0.0.1 (Apache), e si usa l'**ultimo** valore, se e' un indirizzo IP valido (i precedenti li puo' falsificare il client; un valore non valido si ignora e vale l'IP della connessione). Il vhost deve quindi passare `X-Forwarded-For` (lo fa `ProxyPass`) e il servizio deve ascoltare solo su 127.0.0.1.

### Token e query string

Nessun token viaggia in query string: i link nelle mail usano il frammento (`/verify#<token>`, `/reset#<token>`, che non arriva al server) e la pagina li manda in POST nel corpo JSON (`/api/auth/verify/preview` per la sola anteprima, che non consuma; `/api/auth/verify` consuma).

### Dimensione delle richieste

L'app legge il corpo a blocchi con tetto di 8 KB (413 oltre; Content-Length non fidato). **Da applicare da Andrea** nel vhost SSL, dentro il blocco che fa il proxy: `LimitRequestBody 16384`, cosi' Apache scarta i corpi enormi prima dell'app (gia' presente in `apache-ssl.conf.example`; vedi la sezione "Messa in sicurezza operativa (C4)").

### Compromessi noti (decisioni consapevoli)

- **Pre-hijack chiuso** (decisione 2b-2): la registrazione non riceve password; l'account nasce senza password utilizzabile e la password si sceglie solo nella pagina `/verify`, dopo il click sul link arrivato nella casella. Chi registra l'indirizzo di un altro non ottiene mai una password valida. Residuo minimo, solo sul SimBrief: su un account non verificato l'ultima registrazione vince anche sul valore SimBrief (se non vuoto), quindi chi registra l'indirizzo di un altro puo' fissarlo; la vittima lo vede e lo modifica da `/account`. La password non cambia mai.
- **Lockout dell'account**: dopo N tentativi falliti l'indirizzo e' bloccato per 15 minuti anche per il proprietario (chi conosce un indirizzo puo' quindi bloccarlo a ripetizione); il controllo del blocco e la registrazione del fallimento non sono un'unica operazione atomica, quindi tentativi paralleli possono superare di poco la soglia. Reset password e lockout: il reset azzera i fallimenti.
- **Quota mail per indirizzo** (3/ora): un attaccante puo' esaurirla per ritardare una mail legittima verso quell'indirizzo fino a un'ora. Quando una mail viene soppressa dai limiti, il link gia' emesso resta valido (non viene sostituito).
- Cambio password e cambio email confermato invalidano i token di reset gia' emessi.
- Pulizia automatica (sessioni scadute, token usati/scaduti, eventi vecchi) al primo uso e al massimo ogni 10 minuti; massimo 10 sessioni per utente (le piu' vecchie vengono revocate).

I backup del DB non ci sono ancora (previsto `sqlite3 .backup` con timer piu' avanti).

## Pagine web (passo 2b-2)

Le pagine vivono in `server/public/` (inglese, nessuno script o stile inline, CSP invariata) e sono servite da `app.py` con una whitelist di route, **solo se `PUBLIC_BASE_URL` e' impostata** (cioe' con l'API montata). Senza, `/` resta il segnaposto "coming soon" e tutte le altre pagine rispondono 404: il sito non si rompe. Dal passo U2 con l'API montata sono serviti anche `/app.js` e `/app.css` da `current/web/` (whitelist esatta, niente percorsi) per la pagina `/flight`; il resto di `web/` (compreso `index.html`) **non** e' servito.

| Percorso | Cosa fa |
|---|---|
| `/` | Home con i link a Sign in / Create account (solo con l'API montata) |
| `/register` | Email + "SimBrief Pilot ID or username" (facoltativo); risposta sempre uguale |
| `/verify#<token>` | Anteprima del link (non lo consuma), poi scelta della password con conferma; a fine verifica si apre la sessione e si va a `/waiting`. Per il cambio email: un solo bottone di conferma, poi si rifa' il login |
| `/login`, `/forgot`, `/reset#<token>` | Accesso, richiesta e impostazione della nuova password |
| `/account` | Email e SimBrief (modifica), cambio password, cambio email (conferma dalla nuova casella), cancellazione (password + testo DELETE), logout |
| `/waiting` | "Program not connected", bottone "Start flight" disattivato (attivo con il programma collegato: apre `/flight`), nessun dato di volo |
| `/flight` | Pagina di volo in sola lettura (stessa `app.js`/`app.css` del programma, `data-mode="remote"`): vedi il passo U2 |

Le pagine protette (`/account`, `/waiting`) leggono `/api/auth/me` e mandano a `/login` senza sessione; `/login` e `/register` mandano a `/waiting` se si e' gia' dentro. Il token dei link sta nel frammento: il JS lo legge, lo toglie subito dall'URL (`history.replaceState`) e lo manda solo nel corpo JSON di una POST. I form sono `method="post"` senza `action`, cosi' se lo script non carica nessun dato finisce nell'URL.

### Flusso di registrazione (cambia rispetto a 2b-1)

1. `POST /api/auth/register` con solo `email` (+ `simbrief_id` facoltativo): crea l'account non verificato **senza password** e manda `/verify#<token>`. Risposta identica per indirizzo nuovo ed esistente; su un indirizzo non verificato rinvia il link e aggiorna il SimBrief se indicato (la password non si tocca); su uno verificato manda la mail "sign-up attempt" senza link.
2. `POST /api/auth/verify/preview` `{token}`: dice se il link e' valido e di che tipo, non consuma (bucket per IP proprio, 20/ora).
3. `POST /api/auth/verify` `{token, password}` (min 10, max 128): imposta la password, verifica l'account, consuma il token e apre la sessione (cookie + `csrf_token`). Password non valida: 400 e il token **non** si consuma. Bucket per IP stretto (5/ora). Per il cambio email non serve password e non si apre la sessione.
4. Il login richiede account verificato e password impostata.

Sul vhost SSL ricorda `LimitRequestBody 16384` (vedi sopra).

### Come provarle

Dopo il deploy imposta `SECRET_KEY` e poi `PUBLIC_BASE_URL` in `/etc/virtual-captain/env` (prima i limiti SMTP gia' presenti), riavvia il servizio e apri `https://<tuo dominio>/`: devono comparire i link. Crea un account, apri il link ricevuto per mail, scegli la password, arrivi su `/waiting`.

Le risposte di `/api/auth/*` hanno `Cache-Control: no-store`.

## Collegamento del programma sul PC: device-code (passo 2c)

Nessuna variabile obbligatoria nuova e nessun cambio al deploy: le route `/api/device/*` e la pagina `/link` si montano insieme all'API account (cioe' solo con `PUBLIC_BASE_URL`). La migrazione 3 del DB (tabelle `device_codes` e `device_tokens`) parte da sola al primo uso. Le risposte di `/api/device/*` hanno `Cache-Control: no-store`.

Variabili facoltative (default tra parentesi): `RATE_DEVICE_START_IP_HOURLY` (10) e `RATE_DEVICE_START_IP_DAILY` (30), `RATE_DEVICE_POLL_IP_MINUTE` (60), `RATE_DEVICE_TOKEN_IP_MINUTE` (30, refresh e revoca), `DEVICE_CODE_TTL_SECONDS` (600), `DEVICE_POLL_INTERVAL_SECONDS` (5), `DEVICE_ACCESS_TTL_SECONDS` (600), `DEVICE_REFRESH_TTL_DAYS` (90), `DEVICE_LOOKUP_MAX_FAILURES` (5), `DEVICE_LOOKUP_LOCK_MINUTES` (15).

### Flusso

1. Il programma chiama `POST /api/device/start` (JSON, senza login): riceve `device_code` (segreto, solo per il programma), `user_code` `ABCD-EFGH` (8 caratteri, senza 0/O/1/I/L), `verification_uri` (`<PUBLIC_BASE_URL>/link`), `verification_uri_complete` (con il codice nel **frammento**: `/link#ABCD-EFGH`, non in query), `expires_in` (600) e `interval` (5).
2. L'utente apre `/link` (o il link completo/QR), accede se serve (dopo il login si torna sempre a `/link`, il codice resta solo nella memoria della scheda), vede IP, ora e user agent della richiesta e l'avviso "Only approve if you started this on your own PC", poi preme **Approve** o **Deny**.
3. Il programma fa polling di `POST /api/device/poll` `{device_code}` ogni `interval` secondi: `authorization_pending`, `slow_down` (troppo frequente: l'intervallo sale di 5 s, max 60), `expired_token` (scaduto, sconosciuto o gia' ritirato), `access_denied`. Appena approvato, **una sola** risposta contiene `access_token` (opaco, 10 minuti) e `refresh_token` (90 giorni); il codice passa a `consumed` e il poll successivo fallisce.
4. `POST /api/device/token` `{grant_type: "refresh_token", refresh_token}`: ruota la coppia (il vecchio refresh si invalida). Un refresh gia' ruotato presentato di nuovo **revoca l'intera famiglia** (puo' essere rubato; se la risposta di rotazione si perde per rete, serve un nuovo collegamento). `POST /api/device/revoke` `{token}` (refresh o access): idempotente, risposta sempre `{"ok": true}`, revoca tutta l'autorizzazione del programma.

Lato server `DeviceCodes.verify_access_token(token)` (in `app.state.devices`) restituisce lo `user_id` di un access token valido: serve al futuro uplink, non e' una route. Il token vale solo per l'account che ha approvato.

### Regole

- **Un solo programma attivo per account**: quando un nuovo codice viene ritirato col poll, i token precedenti dell'account vengono revocati (l'ultimo sostituisce).
- Cambio password, reset password e cancellazione account revocano **tutti** i token del programma (e scartano i codici approvati e non ancora ritirati). Il cambio email no.
- `lookup`, `approve`, `deny` richiedono sessione, CSRF e Origin. **Anti-phishing**: `approve` e' accettato solo se lo stesso utente ha fatto prima la `lookup` del codice e se il corpo contiene `confirm` = gli **ultimi 4 caratteri** del codice, digitati a mano nella pagina (campo separato, mai precompilato, anche se il codice arriva da `/link#CODE`; confronto a tempo costante); senza lookup o con `confirm` errato la richiesta e' rifiutata e **conta come tentativo errato**. La pagina mostra il codice in grande, tiene il bottone Approve disattivato finche' i 4 caratteri non coincidono e presenta nome, IP, ora e user agent come dati dichiarati dal richiedente, non verificati (il nome, solo lettere/cifre/spazio/punto/trattino, max 40, e' sempre testo in un riquadro a parte, mai titolo). `deny` non richiede ne' lookup ne' conferma.

Tentativi: massimo 5 codici errati per sessione, per IP e per account (blocco 15 minuti); codice inesistente, scaduto o gia' usato danno la stessa risposta `invalid_code`. Il fallimento non si azzera col successo (un codice proprio valido non resetta il conteggio).
- Nel DB solo hash SHA-256 di `device_code`, access e refresh token. L'IP della richiesta (gli IPv6 gia' raggruppati per /64) e lo user agent (200 caratteri) restano nella riga solo finche' il codice e' in attesa, per mostrarli nella pagina di approvazione: si azzerano a decisione (approvazione o rifiuto), al ritiro o alla prima pulizia dopo la scadenza (che cancella la riga intera; fino ad allora, al massimo ~10 minuti oltre la scadenza se nessuno usa il sito). Mai nei log.
- Pulizia (stessa cadenza di 10 minuti): codici scaduti; token scaduti; token ruotati o revocati dopo 7 giorni (fino ad allora servono a riconoscere un refresh riusato).
- Limiti: al massimo 5000 codici in attesa (un DoS solo teorico: riempirli richiede migliaia di IP, perché `/start` è limitato per IP, e i codici scadono in 10 minuti).

### Come provarlo

Da Mac (sostituisci il dominio; nulla di segreto nei comandi):

```
curl -s -X POST https://<dominio>/api/device/start -H 'Content-Type: application/json' -d '{"device_name":"Mac test"}'
curl -s -X POST https://<dominio>/api/device/poll -H 'Content-Type: application/json' -d '{"device_code":"<device_code ricevuto>"}'
```

Il poll risponde `{"error":"authorization_pending",...}`. Apri nel browser, gia' loggato, `https://<dominio>/link#<user_code>` (o `/link` e digita il codice), controlla IP e ora, premi **Approve**. Ripeti il poll: restituisce `access_token` e `refresh_token` **una volta sola** (non incollarli in chat ne' in file); un secondo poll deve rispondere `expired_token`. Con `/waiting` -> "Link a program" si arriva alla stessa pagina.

### Note e compromessi (passo 2c)

- **Retry del refresh (m6)**: nessuna finestra di tolleranza. Se il programma ripresenta lo stesso refresh (risposta persa, o due richieste in parallelo) la famiglia viene revocata e serve un nuovo collegamento; con due `/token` simultanei sullo stesso refresh il primo ruota e il secondo e' trattato come riuso (coperto da test con thread). Da valutare nel client dell'uplink: serializzare i refresh e, se serve, una breve tolleranza lato server.
- **Righe ruotate (m4)**: restano 7 giorni per riconoscere il riuso; oltre, un refresh vecchio da' solo `invalid_grant` senza revocare la famiglia. Il cambio email non revoca i token.
- `verify_access_token` legge con una connessione in sola lettura e aggiorna `last_used_at` al massimo una volta al minuto. La migrazione 4 aggiunge `looked_up_by` a `device_codes`.
- Il codice conservato nella scheda durante il login (`sessionStorage`) scade dopo 10 minuti e si cancella al logout e alla registrazione.

## Snapshot del volo e pagina /flight (passo U2)

Il programma manda al server uno **snapshot** del volo (al massimo 1 al secondo, solo se cambia) e la pagina `/flight` lo mostra in sola lettura. Nessuna variabile nuova, **nessun cambio al service e ad Apache**: basta il deploy del codice.

- Messaggio `{"t":"snapshot","sv":1,"seq":N,"d":{...}}` (solo dopo l'`hello`). `d` passa da un'**allowlist** (`server/wire.py`, `sanitize_snapshot`): chiavi di primo livello sconosciute scartate; dentro le sezioni note le chiavi sconosciute sono scartate ma un tipo o un limite sbagliato rende lo snapshot non valido. **La posizione non esce dal PC**: righe Position/Latitude/Longitude tolte, waypoint senza coordinate. Esclusi anche SimBrief ID, info, errori di SimConnect, DLL, valori grezzi.
- Il server tiene l'ultimo snapshot **solo in RAM, dentro la connessione** (si cancella alla disconnessione o alla sostituzione 4001); mai nei log (nei log restano solo "agent connected/disconnected"). Chiusure: 4004 oltre 4 snapshot in 2 s o 1 MiB in 10 s; 4002 `sv` non supportata; 1008 dopo 5 snapshot non validi di fila (uno solo viene ignorato).
- `GET /api/flight/state[?seq=N]`: **solo cookie di sessione** (l'account e' quello della sessione, nessun id nell'URL), 401 senza sessione, 429 con `Retry-After` (limiti in RAM: 120/min per utente, 600/min per IP, mai sul DB), `Cache-Control: no-store`, `Vary: Cookie`. Risposte: `{"connected":false}` (programma assente o silenzioso oltre 12 s), `{"connected":true,"seq","age","spoke_ago","snapshot"}`; con `seq` uguale a quello attuale `"snapshot":null,"unchanged":true`. `age` = secondi dall'ultimo segnale del programma (snapshot o heartbeat).
- `accounts.session()` aggiorna `last_seen` al massimo una volta al minuto (il polling non scrive sul DB ogni secondo; il limite di inattivita' e' di giorni).
- `/flight`: polling ogni 1 s con il programma collegato, ogni 3 s altrimenti; banner "Program not connected" (dati nascosti), "Briefing in progress on the PC", "No update for N s" oltre 20 s.

### Come provarlo (U2)

Con il programma avviato e collegato (anche con `--sim`): da `/waiting` premi **Start flight**. In Briefing la pagina dice "Briefing in progress on the PC"; premi "Briefing complete" sul PC e dopo ~1 s compaiono pill, checklist e log. Chiudi il programma: entro ~12 s "Program not connected" e i dati spariscono. Ritorno indietro: ripristina il deploy precedente (nessuna modifica di service, Apache o variabili).

## Uplink del programma (passo U1)

Il programma sul PC, collegato col device-code, apre `wss://<dominio>/ws/agent`; la pagina `/waiting` mostra **Program connected** e abilita **Start flight** (per ora senza dati di volo né destinazione).

### Endpoint e protocollo

- `WS /ws/agent`: header `Authorization: Bearer <access token>` (**mai nella query string**: un token in query vale come assente). Il rifiuto avviene **prima** dell'accept, con status HTTP: 403 se c'e' un qualsiasi header `Origin` (i browser non possono aprirlo), 429 troppi tentativi dallo stesso IP (30/minuto), 503 troppi programmi collegati (50), 401 token assente, scaduto, revocato o errato.
- `GET /api/agent/status`: cookie di sessione, `{"connected": true|false}`, `Cache-Control: no-store`, 401 senza sessione. La pagina `/waiting` lo interroga ogni 3 secondi.
- Messaggi JSON con campo `t`: il server manda `welcome` (`v`, `heartbeat`, `max_msg`); il programma risponde con `hello` (`v`, `app`, `platform`) entro 5 s, poi `ping` ogni 5 s (risposta `pong`); `bye` per chiudere. Il contratto sta in `server/wire.py` (la root lo riesporta in `protocol.py`).
- Codici di chiusura: 4001 sostituito da un'altra istanza dello stesso account; 4002 versione del protocollo non supportata; 4003 autorizzazione revocata o scaduta; 4004 messaggio troppo grande o troppo frequente (max 256 KiB, 20 al secondo); 4005 riservato (al riavvio del server uvicorn chiude con 1012: il programma si ricollega con qualunque chiusura diversa da 4001/4002/4003).
- Limiti: un solo programma per account; silenzio oltre 12 s = connessione chiusa e `connected:false`; la famiglia di token viene riletta ogni 10 s (la revoca da cancellazione account, cambio/reset password, `/api/device/revoke` o nuovo codice chiude il collegamento entro ~10 s); ogni collegamento dura al massimo 24 ore, poi il programma si ricollega da solo. Variabili opzionali (solo nomi in `env.example`): `AGENT_MAX`, `RATE_AGENT_IP_MINUTE`, `AGENT_MSGS_PER_SECOND`, `AGENT_STALE_SECONDS`, `AGENT_HELLO_TIMEOUT_SECONDS`, `AGENT_FAMILY_RECHECK_SECONDS`, `AGENT_MAX_LIFETIME_SECONDS`.
- Nei log: solo "agent connected/disconnected (N online)". Mai token, indirizzi o dati utente.

### Cosa fa Andrea sul VPS (a mano, deploy.sh e workflow non cambiano)

1. Dopo il deploy del codice (che porta anche `websockets==15.0.1` nel venv con `pip install -r`), aggiorna il service con le due nuove opzioni di uvicorn (`--ws-max-size 262144 --timeout-graceful-shutdown 5`): copia `server/deploy/virtual-captain.service` (ricordando di rimettere il tuo `User=`/`Group=`) oppure aggiungi le due opzioni alla riga `ExecStart` del file gia' installato:
```
$ sudo cp server/deploy/virtual-captain.service /etc/systemd/system/virtual-captain.service   # poi correggi User/Group
$ sudo systemctl daemon-reload
$ sudo systemctl restart virtual-captain
```
2. Controlla il journal: **non** deve comparire `No supported WebSocket library detected` (uvicorn senza `[standard]` usa la libreria `websockets` installata dal requirements): `journalctl -u virtual-captain -e --no-pager | grep -i websocket`.
3. Apache **invariato**: il vhost SSL ha gia' `ProxyPass /ws/` verso `ws://127.0.0.1:8100/ws/` (serve `proxy_wstunnel`, gia' nell'elenco dei moduli).
4. **`--workers 1` e' obbligatorio**: l'elenco dei programmi collegati (l'Hub) sta nella RAM del processo; con piu' worker due collegamenti dello stesso account potrebbero finire in processi diversi.

**Ritorno indietro**: rimetti il service precedente (senza le due opzioni), `daemon-reload` e `restart`; senza le opzioni il limite dei messaggi WebSocket e' 1 MiB (il server rifiuta comunque oltre 256 KiB) e l'arresto puo' attendere i collegamenti aperti.

### Come provarlo (U1)

Con il programma sul PC avviato (`python main.py`, di default collega al server) e un account verificato: al primo avvio la console mostra il codice (e un QR); approvalo da `/link` (ultimi 4 caratteri), poi apri `/waiting` da loggato: dopo pochi secondi il titolo diventa **Program connected** e **Start flight** si abilita (mostra solo un messaggio). Chiudi il programma: entro ~12 secondi torna **Program not connected**.

### Note dopo la review (U1)

- Prova manuale del rifiuto: `curl -i -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://<dominio>/ws/agent` (senza token) deve rispondere **HTTP 401**; con `-H 'Origin: https://x.example'` risponde 403. Con uvicorn reale (implementazione legacy di websockets) i rifiuti arrivano come status HTTP 401/403, coperti da un test su loopback.
- Le chiusure e gli invii lato server hanno un tempo massimo (5 s; 2 s per chiudere la vecchia connessione sostituita): un socket che non risponde non blocca le altre.
- Dopo `invalid_grant`, 4003 o 3 rifiuti 401 di fila su token appena rinnovati il programma NON chiede da solo un nuovo codice: resta in "relink_needed" e dice di riavviare o usare `--link`. Un nuovo codice automatico e' da valutare in un passo successivo.
- Sul PC Windows i token stanno in `%LOCALAPPDATA%\VirtualCaptain\` (non roaming).

## Messa in sicurezza operativa (C4)

Nessun cambio al codice dell'app: sono file di esempio da installare a mano sul VPS (sudo). Un push che tocca solo `server/deploy/**` non fa partire il deploy, quindi **non** installa nulla da solo. Ordine consigliato: Apache, backup, logrotate. Dopo ogni passo fai la verifica.

### 1. Apache: header, limite del body, X-Forwarded-For

Il file `server/deploy/apache-ssl.conf.example` ora contiene: `LimitRequestBody 16384`, HSTS `max-age=31536000` (**senza** includeSubDomains e preload: li aggiungi tu quando confermi che l'HTTPS va bene ovunque) e `RequestHeader unset X-Forwarded-For` prima del proxy. L'unset elimina l'XFF mandato dal client; `mod_proxy` poi aggiunge l'IP reale come unico valore, e l'app (che usa l'ultimo valore, solo da 127.0.0.1) continua a vedere l'IP vero. `LimitRequestBody` vale solo per i corpi HTTP: il WebSocket `/ws/` non e' toccato (l'upgrade non ha corpo, i frame sono limitati da `--ws-max-size`). Il sito non ha upload.

```
$ sudo cp /etc/apache2/sites-available/virtual-captain-le-ssl.conf /etc/apache2/sites-available/virtual-captain-le-ssl.conf.bak
$ sudo nano /etc/apache2/sites-available/virtual-captain-le-ssl.conf   # aggiungi le tre direttive (o confronta con l'esempio), tieni le righe SSLCertificate*
$ sudo apachectl configtest && sudo systemctl reload apache2
```
**Verifica** (dal Mac, dominio reale):
```
$ curl -s -D - -o /dev/null https://virtualcaptain.rossinisolutions.com/ | grep -i strict-transport      # max-age=31536000, nessun includeSubDomains
$ head -c 20000 /dev/zero | tr '\0' 'a' > /tmp/big.txt
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' --data-binary @/tmp/big.txt https://virtualcaptain.rossinisolutions.com/api/auth/login   # 413 (Apache)
$ curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' -d '{"email":"x@example.com","password":"x"}' https://virtualcaptain.rossinisolutions.com/api/auth/login   # risposta dell'app (400/401/429), non 413
```
Per l'XFF, prova diretta: aggiungi **temporaneamente** nel vhost SSL, sopra `CustomLog`, `LogFormat "%h XFF=[%{X-Forwarded-For}i]" xfftest` e cambia l'ultimo argomento di `CustomLog ... combined` in `xfftest` (o aggiungi una seconda riga `CustomLog ${APACHE_LOG_DIR}/virtual-captain/xfftest.log xfftest`); `configtest` e reload. Poi dal Mac: `curl -s -o /dev/null -H 'X-Forwarded-For: 1.2.3.4' https://virtualcaptain.rossinisolutions.com/healthz`. Nota: `%{X-Forwarded-For}i` mostra l'header **come arriva ad Apache**, prima dell'unset, quindi vedrai `1.2.3.4`; la prova che l'unset funziona e' la seconda: ripeti piu' volte `POST /api/auth/login` con XFF diversi (`1.2.3.4`, `5.6.7.8`, ...) e verifica che scatti comunque il 429 dello stesso bucket (se l'XFF finto passasse, ogni valore avrebbe un bucket nuovo e non scatterebbe mai). **Poi ripristina** il vhost dal `.bak` o togli le righe di prova e ricarica.
Prova anche che il WebSocket e il login web funzionino ancora (apri `/waiting` o `/flight` da loggato).
**Ritorno indietro**: `sudo cp ...le-ssl.conf.bak ...le-ssl.conf && sudo systemctl reload apache2`.

### 2. Backup giornaliero del database

Serve `sqlite3` sul VPS (`sudo apt install sqlite3`). Lo script fa `sqlite3 .backup` (copia consistente a servizio acceso) in `/var/www/virtual-captain/shared/backup/`, verifica l'`integrity_check` della copia, la salva con permessi 600 (cartella 700) e tiene le ultime 7. Exit code: 0 ok, 1 configurazione/DB mancante, 2 backup fallito, 3 copia corrotta. Nei log solo nomi file. I backup contengono dati degli account (hash di password e token): restano sul VPS, non copiarli in chat ne' in repo.

```
$ sudo apt install sqlite3        # se manca
$ sudo install -o root -g root -m 755 server/deploy/backup-db.sh /var/www/virtual-captain/scripts/backup-db.sh
$ sudo cp server/deploy/virtual-captain-backup.service /etc/systemd/system/virtual-captain-backup.service
$ sudo cp server/deploy/virtual-captain-backup.timer /etc/systemd/system/virtual-captain-backup.timer
$ sudo nano /etc/systemd/system/virtual-captain-backup.service   # sostituisci User/Group arossini con il tuo utente VPS (come virtual-captain.service)
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now virtual-captain-backup.timer
$ sudo systemctl start virtual-captain-backup.service            # prima copia subito
```
Se hai cambiato `DB_PATH` nell'env, aggiungi nel `.service` `Environment=DB_PATH=<stesso percorso>` (il backup non carica `/etc/virtual-captain/env`: non gli servono segreti).
**Verifica**:
```
$ systemctl status virtual-captain-backup.service --no-pager     # exited, status=0
$ journalctl -u virtual-captain-backup -e --no-pager             # "created virtual-captain-...db"
$ systemctl list-timers virtual-captain-backup.timer
$ ls -l /var/www/virtual-captain/shared/backup/                  # -rw------- , max 7 file
```
**Ritorno indietro**: `sudo systemctl disable --now virtual-captain-backup.timer && sudo rm /etc/systemd/system/virtual-captain-backup.{service,timer} /var/www/virtual-captain/scripts/backup-db.sh && sudo systemctl daemon-reload` (le copie restano finche' non le cancelli).

**Note**: con un `DB_PATH` diverso dal default servono anche `ReadWritePaths=<cartella del DB>` nel `.service` e la cartella scrivibile dall'utente del servizio (SQLite crea i file `-wal`/`-shm` accanto al DB). Con un `BACKUP_DIR` personalizzato lo script lo crea con umask 077 (modo 700) e anche li' serve `ReadWritePaths`. Le copie `.partial-*` rimaste da un'esecuzione interrotta vengono cancellate dopo 1 giorno.
**Controllo periodico** (ogni tanto, o dopo un riavvio del VPS): `systemctl --failed` (non deve elencare `virtual-captain-backup`) e `systemctl list-timers virtual-captain-backup.timer` (NEXT e LAST sensati). Niente `OnFailure=`: richiederebbe un'altra unit (e un canale di notifica) che per ora non c'e'; basta il controllo a mano.

**Ripristino di prova** (da fare una volta, senza toccare il DB vivo):
```
$ LAST=$(ls -1 /var/www/virtual-captain/shared/backup/virtual-captain-*.db | sort | tail -1)
$ sqlite3 "$LAST" 'PRAGMA integrity_check;'                      # ok
$ sqlite3 "$LAST" '.tables'                                      # le tabelle attese (users, ...)
$ sqlite3 "$LAST" 'SELECT count(*) FROM users;'                  # numero plausibile
```
**Ripristino vero** (solo se il DB e' perso/corrotto):
```
$ LAST=$(ls -1 /var/www/virtual-captain/shared/backup/virtual-captain-*.db | sort | tail -1)
$ sudo systemctl stop virtual-captain
$ mv /var/www/virtual-captain/shared/virtual-captain.db /var/www/virtual-captain/shared/virtual-captain.db.broken
$ rm -f /var/www/virtual-captain/shared/virtual-captain.db-wal /var/www/virtual-captain/shared/virtual-captain.db-shm
$ cp "$LAST" /var/www/virtual-captain/shared/virtual-captain.db && chmod 600 /var/www/virtual-captain/shared/virtual-captain.db
$ sudo systemctl start virtual-captain && curl -s https://virtualcaptain.rossinisolutions.com/healthz
```
Si perdono le scritture dopo l'ultima copia (al piu' 24 ore).

### 3. Log del sito in una sottocartella + logrotate a 14 giorni

Il file stock `/etc/logrotate.d/apache2` ruota `/var/log/apache2/*.log` (con la sua retention, non la nostra): se i log del sito restassero li', i due blocchi si contenderebbero gli stessi file e i 14 giorni non sarebbero garantiti. Per questo il vhost scrive in `/var/log/apache2/virtual-captain/` (il pattern `*.log` non scende nelle sottocartelle) e il nostro file ruota solo quella.

```
$ sudo install -d -o root -g adm -m 750 /var/log/apache2/virtual-captain
$ sudo cp /etc/apache2/sites-available/virtual-captain-le-ssl.conf{,.bak2}
$ sudo cp /etc/apache2/sites-available/virtual-captain.conf{,.bak2}                       # vhost porta 80, se ancora presente
$ sudo nano /etc/apache2/sites-available/virtual-captain-le-ssl.conf                      # ErrorLog/CustomLog -> .../virtual-captain/error.log e access.log (idem il file della porta 80)
$ sudo apachectl configtest && sudo systemctl reload apache2
$ sudo install -o root -g root -m 644 server/deploy/logrotate-virtual-captain /etc/logrotate.d/virtual-captain
$ sudo logrotate -d /etc/logrotate.d/virtual-captain                                      # prova a secco
```
I vecchi `/var/log/apache2/virtual-captain-*.log` restano dove sono: continuano a essere ruotati dal file stock finche' non li cancelli (`sudo rm` quando non ti servono; Apache non li riscrive piu').
**Verifica**: `ls -l /var/log/apache2/virtual-captain/` mostra `error.log` e `access.log` che crescono; `logrotate -d` elenca solo i due file della sottocartella, senza "duplicate log entry"; `grep -rn virtual-captain /etc/logrotate.d/` mostra solo il nostro file. La rotazione reale e' giornaliera (timer di sistema); per forzarla una volta: `sudo logrotate -f /etc/logrotate.d/virtual-captain`.
**Ritorno indietro**: rimetti i `.bak2` dei vhost, `sudo apachectl configtest && sudo systemctl reload apache2`, `sudo rm /etc/logrotate.d/virtual-captain`.

### Variabili d'ambiente

`server/env.example` elenca ora tutti i nomi letti da `config.py` (verificati uno a uno), compresi `RATE_ACCOUNT_USER_MINUTE` (default 10), `RATE_ACCOUNT_IP_MINUTE` (60), `HASH_QUEUE_MAX` (8) e `VC_REMOTE_COMMANDS` (`off` = interruttore della rotta dei comandi, risponde 503). Tutte facoltative.

### Rollback complessivo di C4

Apache: ripristina il `.bak` e ricarica. Backup: disabilita timer come sopra. Logrotate: rimuovi il file. Nessuna di queste cose tocca l'app o il DB vivo.
