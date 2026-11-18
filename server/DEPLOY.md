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

certbot ha creato `/etc/apache2/sites-available/virtual-captain-le-ssl.conf`. Confrontalo con `server/deploy/apache-ssl.conf.example` e porta dentro: redirect di `www`, HSTS `max-age=86400` (poi lo alziamo), `ProxyPass` di `/` e `/ws/` verso `127.0.0.1:8100`, mantenendo le righe `SSLCertificate*` generate da certbot. Moduli: `sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers ssl`.
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

## Account (passo 2b-1: API, senza pagine)

Nuove variabili in `/etc/virtual-captain/env` (NOME=valore, una per riga; i valori non vanno in repo ne' in chat):

- `SECRET_KEY` (**obbligatoria se imposti `PUBLIC_BASE_URL`**, min 32 caratteri): sale degli hash di IP e indirizzi nei limiti di frequenza. Generala a mano sul VPS: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` e incolla il risultato con `sudo nano /etc/virtual-captain/env`. Se la cambi, i contatori dei limiti si azzerano. **Impostala PRIMA di `PUBLIC_BASE_URL`**: con `PUBLIC_BASE_URL` e senza `SECRET_KEY` il servizio rifiuta di partire (messaggio chiaro nel journal).
- `PUBLIC_BASE_URL`: origine pubblica https senza slash finale e senza percorso (`http` ammesso solo per localhost/127.0.0.1/::1). Serve per i link nelle mail e per il controllo `Origin`. Se manca, l'API `/api/auth/*` **non viene montata** e nel journal compare un warning ("NOT mounted").
- `DB_PATH` (facoltativa): file SQLite. Default `/var/www/virtual-captain/shared/virtual-captain.db`. Viene creato col primo uso, permessi 600 (anche `-wal`/`-shm`); la cartella `shared` deve essere scrivibile dall'utente del servizio.
- Limiti (facoltative, default tra parentesi): `RATE_IP_HOURLY` (5), `RATE_IP_DAILY` (20), `RATE_MAIL_ADDR_HOURLY` (3), `RATE_MAIL_DAILY` (200, **per tipo di mail**: verifica, reset, altre), `LOGIN_MAX_FAILURES` (5), `LOCKOUT_MINUTES` (15), `RATE_LOGIN_IP_15MIN` (30). I limiti per IP valgono separatamente per registrazione/forgot, reset e verifica; gli IPv6 sono raggruppati per /64.

### X-Forwarded-For

L'IP del client si legge da `X-Forwarded-For` **solo** se la richiesta arriva da 127.0.0.1 (Apache), e si usa l'**ultimo** valore, se e' un indirizzo IP valido (i precedenti li puo' falsificare il client; un valore non valido si ignora e vale l'IP della connessione). Il vhost deve quindi passare `X-Forwarded-For` (lo fa `ProxyPass`) e il servizio deve ascoltare solo su 127.0.0.1.

### Token e query string

Nessun token viaggia in query string: i link nelle mail usano il frammento (`/verify#<token>`, `/reset#<token>`, che non arriva al server) e la pagina li manda in POST nel corpo JSON (`/api/auth/verify/preview` per la sola anteprima, che non consuma; `/api/auth/verify` consuma).

### Dimensione delle richieste

L'app legge il corpo a blocchi con tetto di 8 KB (413 oltre; Content-Length non fidato). **Da applicare da Andrea** nel vhost SSL, dentro il blocco che fa il proxy: `LimitRequestBody 16384`, cosi' Apache scarta i corpi enormi prima dell'app.

### Compromessi noti (decisioni consapevoli)

- **Pre-hijack**: chi registra per primo l'indirizzo di un'altra persona fissa la password dell'account non verificato. Se la vittima poi si registra, l'ultima registrazione vince (la password viene sostituita e parte un nuovo link), ma prima di allora vale quella dell'attaccante. Mitigazione prevista per 2b-2: impostare la password al momento della verifica, non della registrazione.
- **Lockout dell'account**: dopo N tentativi falliti l'indirizzo e' bloccato per 15 minuti anche per il proprietario (chi conosce un indirizzo puo' quindi bloccarlo a ripetizione); il controllo del blocco e la registrazione del fallimento non sono un'unica operazione atomica, quindi tentativi paralleli possono superare di poco la soglia. Reset password e lockout: il reset azzera i fallimenti.
- **Quota mail per indirizzo** (3/ora): un attaccante puo' esaurirla per ritardare una mail legittima verso quell'indirizzo fino a un'ora. Quando una mail viene soppressa dai limiti, il link gia' emesso resta valido (non viene sostituito).
- Cambio password e cambio email confermato invalidano i token di reset gia' emessi.
- Pulizia automatica (sessioni scadute, token usati/scaduti, eventi vecchi) al primo uso e al massimo ogni 10 minuti; massimo 10 sessioni per utente (le piu' vecchie vengono revocate).

I backup del DB non ci sono ancora (previsto `sqlite3 .backup` con timer piu' avanti).
