"""Collegamento del programma al server pubblico (U1): login a codice, WebSocket, riconnessione.

Logica pura con I/O iniettato (http, connect, orologio, attesa) cosi' si prova senza rete.
Il thread dell'uplink NON tocca mai SimConnect ne' la voce, e non blocca mai il programma:
gli stati e gli errori vanno solo in console (print), mai token, mai codici nei file di log.
"""
from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import protocol
from secure_store import TokenStore, default_dir

APP_NAME = "VirtualCaptain"
APP_VERSION = "0.2.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"
DEFAULT_SERVER_URL = "https://virtualcaptain.rossinisolutions.com"  # sovrascrivibile con --server-url
CONFIG_NAME = "uplink.json"
LOCK_NAME = "uplink.lock"
REFRESH_MARGIN = 120.0      # secondi: sotto questa soglia l'access token si rinnova prima di usarlo
STABLE_AFTER = 30.0         # secondi di collegamento stabile dopo i quali il backoff riparte da 1 s
MAX_LOGIN_RESTARTS = 3      # nuovi codici dopo la scadenza del primo
RECV_TICK = 0.2
SEND_TIMEOUT = 5.0         # secondi massimi per un invio sul WebSocket
MAX_FRESH_REJECTS = 3       # 401 consecutivi su token appena rinnovati: serve un nuovo collegamento
REVOKE_OLD_TIMEOUT = 3.0
LOOPBACK = ("localhost", "127.0.0.1", "::1")

UNLINKED, LINKING, CONNECTING, CONNECTED = "unlinked", "linking", "connecting", "connected"
RELINK_NEEDED, STOPPED = "relink_needed", "stopped"


class NetworkError(Exception):
    """Server irraggiungibile o risposta inattesa (transitorio: si riprova con backoff)."""


class InvalidGrant(Exception):
    """Il refresh token non vale piu': serve un nuovo collegamento."""


class ConnClosed(Exception):
    def __init__(self, code: int = 1006):
        super().__init__(str(code))
        self.code = code


class ConnRejected(Exception):
    def __init__(self, status: int):
        super().__init__(str(status))
        self.status = status


class ConnError(Exception):
    """Errore di rete o di handshake senza status HTTP."""


# ---------------------------------------------------------------- indirizzo del server

def check_base_url(url: str) -> str:
    """Origine normalizzata (schema://host[:porta]); http solo su loopback (mai token in chiaro)."""
    text = (url or "").strip().rstrip("/")
    try:
        parts = urlparse(text)
        host = parts.hostname
    except ValueError:
        raise ValueError("indirizzo del server non valido") from None
    if (parts.scheme not in ("http", "https") or not host or parts.path or parts.query
            or parts.fragment or parts.username or parts.password):
        raise ValueError("l'indirizzo del server deve essere solo schema://host[:porta]")
    if parts.scheme == "http" and host not in LOOPBACK:
        raise ValueError("indirizzo http rifiutato: serve https (http solo su localhost)")
    return text


def ws_url(base_url: str) -> str:
    return ("wss" if base_url.startswith("https://") else "ws") + base_url[base_url.index("://"):] + protocol.WS_PATH


# ---------------------------------------------------------------- backoff

class Backoff:
    """1, 2, 4 ... 60 s con jitter +-20%; riparte da 1 s dopo un collegamento stabile (>= 30 s)."""

    def __init__(self, rng: Callable[[], float] = random.random, base: float = 1.0, cap: float = 60.0,
                 jitter: float = 0.2, stable: float = STABLE_AFTER):
        self.rng, self.base, self.cap, self.jitter, self.stable = rng, base, cap, jitter, stable
        self.n = 0

    def failure(self) -> float:
        delay = min(self.cap, self.base * (2 ** min(self.n, 12)))
        self.n += 1
        return delay * (1 + self.jitter * (2 * self.rng() - 1))

    def session_ended(self, duration: float) -> None:
        if duration >= self.stable:
            self.n = 0

    def reset(self) -> None:
        self.n = 0


# ---------------------------------------------------------------- HTTP (stdlib)

class UrllibHttp:
    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 15.0):
        self.user_agent, self.timeout = user_agent, timeout

    def post_json(self, url: str, body: dict, timeout: Optional[float] = None):
        """(status, dict). NetworkError se il server non risponde."""
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": self.user_agent})
        try:
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    status, raw = r.status, r.read(65536)
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read(65536)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NetworkError(type(e).__name__) from None
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- login a codice e rinnovo

def tokens_from(data: dict, base_url: str, now: float) -> Optional[dict]:
    access, refresh = data.get("access_token"), data.get("refresh_token")
    ttl = data.get("expires_in")
    if not (isinstance(access, str) and isinstance(refresh, str) and access and refresh):
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        ttl = 600
    return {"refresh": refresh, "access": access, "access_exp": now + float(ttl), "base_url": base_url}


def _interval(value, default: int = 5) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(1, min(int(value), 60))


def device_login(http, base_url: str, show: Callable[[dict], None], wait: Callable[[float], bool],
                 wall: Callable[[], float] = time.time, max_restarts: int = MAX_LOGIN_RESTARTS):
    """Device-code flow. Ritorna (esito, tokens): "ok" | "denied" | "expired" | "network" | "stopped".
    Rispetta `interval` e `slow_down`; a expired_token chiede un nuovo codice (al piu' max_restarts volte)."""
    for _attempt in range(1 + max_restarts):
        try:
            status, d = http.post_json(base_url + "/api/device/start", {})
        except NetworkError:
            return "network", None
        if status != 200 or not isinstance(d.get("device_code"), str) or not isinstance(d.get("user_code"), str):
            return "network", None
        show({"user_code": d["user_code"], "verification_uri": d.get("verification_uri", base_url + "/link"),
              "verification_uri_complete": d.get("verification_uri_complete", ""),
              "expires_in": d.get("expires_in", 600)})
        interval = _interval(d.get("interval"))
        deadline = wall() + (d.get("expires_in") if isinstance(d.get("expires_in"), (int, float)) else 600)
        while True:
            if wait(interval):
                return "stopped", None
            if wall() >= deadline:
                break
            try:
                status, r = http.post_json(base_url + "/api/device/poll", {"device_code": d["device_code"]})
            except NetworkError:
                continue
            if status == 200:
                tokens = tokens_from(r, base_url, wall())
                if tokens:
                    return "ok", tokens
                return "network", None
            error = r.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = _interval(r.get("interval"), min(interval + 5, 60))
            elif error == "access_denied":
                return "denied", None
            elif error == "expired_token":
                break
            else:  # troppi tentativi o risposta inattesa: rallenta e continua
                interval = min(interval + 5, 60)
    return "expired", None


def refresh_tokens(http, base_url: str, tokens: dict, wall: Callable[[], float] = time.time) -> dict:
    """Rinnovo con rotazione. InvalidGrant se il refresh non vale piu'; NetworkError altrimenti."""
    status, d = http.post_json(base_url + "/api/device/token",
                               {"grant_type": "refresh_token", "refresh_token": tokens["refresh"]})
    if status == 200:
        new = tokens_from(d, base_url, wall())
        if new:
            return new
    elif status == 400 and d.get("error") in ("invalid_grant", "unsupported_grant_type"):
        raise InvalidGrant()
    raise NetworkError(f"http {status}")


def revoke(http, base_url: str, tokens: dict, timeout: Optional[float] = None) -> bool:
    """Revoca la famiglia di token sul server. True se il server ha risposto ok."""
    try:
        status, d = http.post_json(base_url + "/api/device/revoke", {"token": tokens["refresh"]}, timeout)
    except NetworkError:
        return False
    return status == 200 and d.get("ok") is True


# ---------------------------------------------------------------- WebSocket (adattatore websockets)

class _WsAdapter:
    def __init__(self, ws):
        self._ws = ws

    def recv(self, timeout: float):
        from websockets.exceptions import ConnectionClosed
        try:
            msg = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        except ConnectionClosed as e:
            raise ConnClosed(e.rcvd.code if e.rcvd else 1006) from None
        except OSError:
            raise ConnError("os") from None
        return msg if isinstance(msg, str) else ""

    def _abort(self) -> None:
        try:
            self._ws.socket.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001
            pass

    def send(self, text: str) -> None:
        """Invio con tempo massimo: se il server non legge, un timer chiude il socket e l'invio fallisce."""
        from websockets.exceptions import ConnectionClosed
        timer = threading.Timer(SEND_TIMEOUT, self._abort)
        timer.daemon = True
        timer.start()
        try:
            self._ws.send(text)
        except ConnectionClosed as e:
            raise ConnClosed(e.rcvd.code if e.rcvd else 1006) from None
        except OSError:
            raise ConnError("os") from None
        finally:
            timer.cancel()

    def close(self, code: int = 1000) -> None:
        try:
            self._ws.close(code)
        except Exception:  # noqa: BLE001
            pass


def websockets_connect(url: str, token: str, timeout: float = 10.0) -> _WsAdapter:
    """Apre il WebSocket con `Authorization: Bearer` (mai nella query string)."""
    from websockets.exceptions import InvalidStatus, WebSocketException
    from websockets.sync.client import connect
    try:
        raw = connect(url, additional_headers={"Authorization": "Bearer " + token},
                      user_agent_header=USER_AGENT, open_timeout=timeout, close_timeout=2,
                      max_size=protocol.MAX_MSG, ping_interval=None)
    except InvalidStatus as e:
        raise ConnRejected(e.response.status_code) from None
    except (OSError, TimeoutError, WebSocketException) as e:
        raise ConnError(type(e).__name__) from None
    return _WsAdapter(raw)


# ---------------------------------------------------------------- lock di istanza

class InstanceLock:
    """Una sola istanza alla volta ruota il refresh token (msvcrt su Windows, fcntl altrove)."""

    def __init__(self, path: Path):
        self.path, self._f = Path(path), None

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.path, "a+")
        except OSError:
            return False
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        f, self._f = self._f, None
        if f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


# ---------------------------------------------------------------- l'uplink

class Uplink:
    def __init__(self, base_url: str, store: TokenStore, http=None, connect=None,
                 say: Callable[[str], None] = print, show_login: Optional[Callable[[dict], None]] = None,
                 wait: Optional[Callable[[float], bool]] = None, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, rng: Callable[[], float] = random.random,
                 force_link: bool = False, lock: Optional[InstanceLock] = None):
        self.base_url = check_base_url(base_url)
        self.store, self.http = store, http or UrllibHttp()
        self.connect = connect or websockets_connect
        self.say, self.show_login = say, show_login or (lambda info: None)
        self.wall, self.mono, self.force_link, self.lock = wall, mono, force_link, lock
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self.backoff = Backoff(rng)
        self.state = UNLINKED
        self._thread: Optional[threading.Thread] = None
        self._last_msg = ""
        self._dirty: Optional[dict] = None  # token rinnovati non ancora salvati su disco
        self._unproven = False   # token rinnovati e mai usati con successo
        self._rejects = 0        # 401 consecutivi su token rinnovati

    # ---- ciclo di vita
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="uplink", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()

    def stopping(self) -> bool:
        return self._stop.is_set()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self.lock is not None:
            self.lock.release()

    # ---- messaggi (solo console)
    def _set(self, state: str, message: str = "") -> None:
        self.state = state
        if message:
            self._tell(message)

    def _tell(self, message: str) -> None:
        if message != self._last_msg:  # niente ripetizioni identiche durante il backoff
            self._last_msg = message
            try:
                self.say("[uplink] " + message)
            except Exception:  # noqa: BLE001  (console chiusa)
                pass

    # ---- token su disco
    def _persist(self, tokens: dict) -> None:
        try:
            self.store.save(tokens)
            self._dirty = None
        except OSError:
            self._dirty = tokens
            self._tell("impossibile salvare i token su disco: il collegamento funziona ma non sopravvive al riavvio")

    def _load(self) -> Optional[dict]:
        tokens = self.store.load()
        if tokens is not None and tokens["base_url"] != self.base_url:
            return None  # collegamento fatto con un altro server: i suoi token non si mandano qui
        return tokens

    # ---- thread
    def run(self) -> None:
        try:
            self._loop()
        except ImportError:
            self._set(STOPPED, "libreria 'websockets' mancante: esegui  pip install -r requirements.txt  (il programma resta in locale)")
        except Exception as e:  # noqa: BLE001  (il thread non deve mai portare giu' il programma)
            self._set(STOPPED, f"uplink interrotto da un errore inatteso ({type(e).__name__}): il programma resta in locale")

    def _loop(self) -> None:
        old = self._load() if self.force_link else None  # --link: i vecchi token restano finche' il nuovo collegamento non riesce
        tokens = None if self.force_link else self._load()
        while not self.stopping():
            if self._dirty is not None:
                self._persist(self._dirty)
            if tokens is None:
                tokens = self._link()
                if tokens is None:
                    if self.state in (STOPPED, RELINK_NEEDED) or self.stopping():
                        return
                    if self._wait(self.backoff.failure()):
                        return
                    continue
                self._persist(tokens)
                if old is not None:  # il server ha gia' revocato la famiglia vecchia dello stesso account: qui solo un tentativo extra
                    revoke(self.http, self.base_url, old, REVOKE_OLD_TIMEOUT)
                    old = None
                self._unproven, self._rejects = False, 0
            try:
                if tokens["access_exp"] - self.wall() < REFRESH_MARGIN:
                    tokens = refresh_tokens(self.http, self.base_url, tokens, self.wall)
                    self._persist(tokens)  # PRIMA di usarli: il vecchio refresh e' gia' ruotato
                    self._unproven = True
            except InvalidGrant:
                return self._revoked("autorizzazione scaduta o revocata")
            except NetworkError:
                self._set(CONNECTING, "server non raggiungibile, riprovo (il programma resta utilizzabile in locale)")
                if self._wait(self.backoff.failure()):
                    return
                continue
            self._set(CONNECTING)
            try:
                ws = self.connect(ws_url(self.base_url), tokens["access"])
            except ConnRejected as e:
                if e.status == 401 and self._unproven:
                    self._rejects += 1
                    if self._rejects >= MAX_FRESH_REJECTS:
                        return self._revoked("il server rifiuta anche i token appena rinnovati")
                elif e.status == 401:
                    tokens = dict(tokens, access_exp=0.0)  # token non valido: prossimo giro rinnova
                self._tell(f"collegamento rifiutato dal server (HTTP {e.status}), riprovo")
                if self._wait(self.backoff.failure()):
                    return
                continue
            except ConnError:
                self._tell("server non raggiungibile, riprovo (il programma resta utilizzabile in locale)")
                if self._wait(self.backoff.failure()):
                    return
                continue
            started = self.mono()
            self._unproven, self._rejects = False, 0
            outcome, code = self._session(ws)
            self.backoff.session_ended(self.mono() - started)
            if outcome == "stop":
                return
            if outcome == "closed" and code == protocol.CLOSE_REPLACED:
                return self._set(STOPPED, "un'altra istanza ha preso il collegamento: questa resta in locale")
            if outcome == "closed" and code == protocol.CLOSE_REVOKED:
                return self._revoked("autorizzazione revocata dal server")
            if outcome == "closed" and code == protocol.CLOSE_BAD_VERSION:
                return self._set(STOPPED, "il server non supporta questa versione del protocollo: aggiorna il programma")
            self._set(CONNECTING, "collegamento perso, riprovo")
            if self._wait(self.backoff.failure()):
                return

    def _link(self) -> Optional[dict]:
        self._set(LINKING)
        kind, tokens = device_login(self.http, self.base_url, self.show_login, self._wait, self.wall)
        if kind == "ok":
            return tokens
        if kind == "denied":
            self._set(STOPPED, "collegamento rifiutato dal sito: riavvia il programma o usa --link per riprovare")
        elif kind == "expired":
            self._set(RELINK_NEEDED, "codice scaduto: riavvia il programma o usa --link per un nuovo collegamento")
        elif kind == "network":
            self._set(UNLINKED, "server non raggiungibile, riprovo (il programma resta utilizzabile in locale)")
        return None

    def _revoked(self, why: str) -> None:
        self.store.clear()
        self._dirty = None
        self._set(RELINK_NEEDED, f"{why}: riavvia il programma (o usa --link) per collegarlo di nuovo")

    def _session(self, ws):
        """Una connessione: ("stop"|"closed"|"error"|"bad", codice). Chiude sempre il socket."""
        try:
            deadline = self.mono() + protocol.HELLO_TIMEOUT + 2
            msg = None
            while msg is None:
                if self.stopping():
                    return "stop", None
                if self.mono() > deadline:
                    return "error", None
                msg = ws.recv(RECV_TICK)
            data = _parse(msg)
            version = data.get("v")
            if data.get("t") != protocol.T_WELCOME or isinstance(version, bool) or version not in protocol.SUPPORTED_VERSIONS:
                return "bad", None
            beat = data.get("heartbeat")
            beat = float(beat) if isinstance(beat, (int, float)) and not isinstance(beat, bool) and 1 <= beat <= 30 else float(protocol.HEARTBEAT)
            ws.send(json.dumps({"t": protocol.T_HELLO, "v": protocol.PROTOCOL_VERSION,
                                "app": f"virtual-captain/{APP_VERSION}", "platform": sys.platform}))
            self._set(CONNECTED, "collegato al server")
            now = self.mono()
            next_ping, last_rx = now + beat, now
            while True:
                if self.stopping():
                    try:
                        ws.send(json.dumps({"t": protocol.T_BYE}))
                    except (ConnClosed, ConnError):
                        pass
                    return "stop", None
                if ws.recv(RECV_TICK) is not None:
                    last_rx = self.mono()
                now = self.mono()
                if now - last_rx > protocol.STALE_AFTER:
                    return "error", None
                if now >= next_ping:
                    ws.send(json.dumps({"t": protocol.T_PING}))
                    next_ping = now + beat
        except ConnClosed as e:
            return "closed", e.code
        except ConnError:
            return "error", None
        finally:
            ws.close()


def _parse(text) -> dict:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- configurazione e avvio da riga di comando

def load_config(directory: Path) -> dict:
    cfg = {"enabled": True, "base_url": DEFAULT_SERVER_URL}
    try:
        data = json.loads((Path(directory) / CONFIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        if isinstance(data.get("enabled"), bool):
            cfg["enabled"] = data["enabled"]
        if isinstance(data.get("base_url"), str) and data["base_url"]:
            cfg["base_url"] = data["base_url"]
    return cfg


def save_config(directory: Path, cfg: dict) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_NAME
    tmp = path.with_name(CONFIG_NAME + ".tmp")
    tmp.write_text(json.dumps({"enabled": bool(cfg["enabled"]), "base_url": cfg["base_url"]}), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class Options:
    enabled: bool
    base_url: str
    link: bool = False
    unlink: bool = False
    error: str = ""


def parse_options(argv: list, config: dict, save: Callable[[dict], None] = lambda cfg: None) -> Options:
    """--server (abilita e salva), --no-server / --local (solo questa esecuzione), --server-url URL
    (salvato), --link, --unlink. Senza opzioni vale la configurazione salvata: di default ACCESO."""
    cfg = dict(config)
    changed = False
    if "--server" in argv:
        cfg["enabled"], changed = True, True
    enabled = cfg["enabled"] and "--no-server" not in argv and "--local" not in argv
    if "--server-url" in argv:
        i = argv.index("--server-url")
        try:
            cfg["base_url"] = check_base_url(argv[i + 1] if i + 1 < len(argv) else "")
            changed = True
        except ValueError as e:
            return Options(False, config["base_url"], error=str(e))
    try:
        base = check_base_url(cfg["base_url"])
    except ValueError as e:
        return Options(False, DEFAULT_SERVER_URL, error=str(e))
    if changed:
        try:
            save({"enabled": cfg["enabled"], "base_url": base})
        except OSError:
            pass
    return Options(enabled, base, link="--link" in argv, unlink="--unlink" in argv)


def unlink_cli(argv: list, say: Callable[[str], None] = print, store: Optional[TokenStore] = None,
               http=None) -> int:
    """--unlink: revoca sul server e cancella i token locali (se la revoca fallisce restano, per riprovare)."""
    store = store or TokenStore()
    tokens = store.load()
    if tokens is None:
        say("[uplink] nessun collegamento salvato su questo PC.")
        return 0
    if not revoke(http or UrllibHttp(), tokens["base_url"], tokens):
        say("[uplink] revoca sul server non riuscita (server irraggiungibile?): i token restano su questo PC, riprova piu' tardi.")
        return 1
    store.clear()
    say("[uplink] collegamento revocato sul server e cancellato da questo PC.")
    return 0


def launch(argv: list, show_login: Callable[[dict], None], say: Callable[[str], None] = print,
           store: Optional[TokenStore] = None, directory: Optional[Path] = None,
           http=None, connect=None) -> Optional[Uplink]:
    """Avvia l'uplink in un thread daemon (None se disattivato o se un'altra istanza lo ha gia')."""
    directory = Path(directory) if directory else default_dir()
    opts = parse_options(argv, load_config(directory), lambda cfg: save_config(directory, cfg))
    if opts.error:
        say(f"[uplink] {opts.error}: avvio senza collegamento al server.")
        return None
    if not opts.enabled:
        say("[uplink] collegamento al server disattivato (--no-server / --local): solo pagina locale.")
        return None
    lock = InstanceLock(directory / LOCK_NAME)
    if not lock.acquire():
        say("[uplink] un'altra istanza di Virtual Captain ha gia' il collegamento al server: questa parte senza.")
        return None
    up = Uplink(opts.base_url, store or TokenStore(directory), http=http, connect=connect, say=say,
                show_login=show_login, force_link=opts.link, lock=lock)
    up.start()
    return up
