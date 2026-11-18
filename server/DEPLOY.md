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
