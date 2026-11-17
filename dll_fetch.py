# Download della SimConnect.dll (solo libreria standard, copiato da RoxPanel), solo dopo il consenso dell'utente.
# Scarica dal PyPI il wheel del pacchetto "SimConnect" fissato per versione e SHA-256, ne estrae UN solo
# membro (la DLL) senza importare nulla e la salva in modo atomico. Il pacchetto (AGPL-3.0) non viene importato
# ne' ridistribuito. Mai eccezioni verso il chiamante; nessuna rete se non in default_download.
import hashlib
import io
import logging
import os
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Callable, NamedTuple, Optional

log = logging.getLogger("captain.sim")

DLL_NAME = "SimConnect.dll"
WHEEL_URL = ("https://files.pythonhosted.org/packages/76/bf/3baa88079144b6e5ab224b76725fa36f502554e95b9c27c42bac9c6de48f/"
             "SimConnect-0.4.26-py2.py3-none-any.whl")
ALLOWED_HOST = "files.pythonhosted.org"
WHEEL_SHA256 = "b77ca43f52df236a34f1f6b1b7c7cfdb0ff5677f86f8a6fd2c1819078fe07d76"
WHEEL_SIZE = 96098
MEMBER = "SimConnect/SimConnect.dll"
DLL_SIZE = 58368
DLL_SHA256 = "677fd3849d54e2a6831dc1aa348d974ead2255ec609fbf5830ef32bf0804b8bc"
MAX_WHEEL = 1024 * 1024
TIMEOUT_S = 15.0

OK, NETWORK, HASH, BAD_WHEEL, NO_MEMBER, TOO_BIG, NOT_WRITABLE = (
    "OK", "NETWORK", "HASH", "BAD_WHEEL", "NO_MEMBER", "TOO_BIG", "NOT_WRITABLE")

MANUAL = "In alternativa copia SimConnect.dll accanto a main.py o in vendor\\ (vedi README)."


class FetchResult(NamedTuple):
    ok: bool
    path: str
    code: str
    message: str


class DownloadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError("redirect non https rifiutato")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _HostRedirect(_HttpsOnlyRedirect):
    def __init__(self, host: str) -> None:
        self.host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if (parts.hostname or "").lower() != self.host or parts.port not in (None, 443):
            raise urllib.error.URLError("redirect verso un host o una porta diversi rifiutato")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_url(url: str, host: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https" or (parts.hostname or "").lower() != host \
            or parts.port not in (None, 443):
        raise urllib.error.URLError("indirizzo non consentito")


def default_download(url: str, max_bytes: int, timeout: float = TIMEOUT_S, host: str = ALLOWED_HOST) -> bytes:
    """Scarica a blocchi con tetto; solo https e solo `host` (anche nei redirect). TOO_BIG oltre il tetto."""
    _check_url(url, host)
    req = urllib.request.Request(url, headers={"User-Agent": "VirtualCaptain"})
    opener = urllib.request.build_opener(_HostRedirect(host))
    chunks, total = [], 0
    with opener.open(req, timeout=timeout) as resp:
        while True:
            block = resp.read(65536)
            if not block:
                break
            total += len(block)
            if total > max_bytes:
                raise DownloadError(TOO_BIG, "file troppo grande")
            chunks.append(block)
    return b"".join(chunks)


def is_pe64(data: bytes) -> bool:
    if len(data) < 0x40 or data[:2] != b"MZ":
        return False
    off = struct.unpack_from("<I", data, 0x3C)[0]
    if off + 6 > len(data) or data[off:off + 4] != b"PE\x00\x00":
        return False
    return struct.unpack_from("<H", data, off + 4)[0] == 0x8664


def extract_dll(wheel: bytes, wheel_sha256: str = WHEEL_SHA256, wheel_size: int = WHEEL_SIZE,
                member: str = MEMBER, dll_size: int = DLL_SIZE, dll_sha256: str = DLL_SHA256) -> bytes:
    """Verifica il wheel e ritorna i byte della DLL; DownloadError altrimenti. Niente extract: un solo read."""
    if len(wheel) != wheel_size:
        raise DownloadError(HASH, "il file scaricato non e' quello atteso (dimensione)")
    if hashlib.sha256(wheel).hexdigest() != wheel_sha256:
        raise DownloadError(HASH, "il file scaricato non e' quello atteso (SHA-256)")
    if not zipfile.is_zipfile(io.BytesIO(wheel)):
        raise DownloadError(BAD_WHEEL, "il file scaricato non e' un archivio valido")
    try:
        with zipfile.ZipFile(io.BytesIO(wheel)) as z:
            try:
                info = z.getinfo(member)
            except KeyError:
                raise DownloadError(NO_MEMBER, "la DLL non e' nel pacchetto scaricato") from None
            if info.file_size != dll_size:
                raise DownloadError(TOO_BIG, "dimensione della DLL inattesa")
            with z.open(info) as f:
                data = f.read(dll_size + 1)
    except DownloadError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
        raise DownloadError(BAD_WHEEL, "il file scaricato non e' un archivio valido") from None
    if len(data) != dll_size:
        raise DownloadError(TOO_BIG, "dimensione della DLL inattesa")
    if hashlib.sha256(data).hexdigest() != dll_sha256:
        raise DownloadError(HASH, "la DLL estratta non e' quella attesa (SHA-256)")
    if not is_pe64(data):
        raise DownloadError(BAD_WHEEL, "la DLL estratta non e' a 64 bit")
    return data


def write_atomic(dest_dir: str, name: str, data: bytes) -> str:
    """Scrive in <name>.part nella stessa cartella e poi os.replace. Nessun residuo in caso di errore."""
    os.makedirs(dest_dir, exist_ok=True)
    final = os.path.join(dest_dir, name)
    part = final + ".part"
    try:
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return final


def fetch_dll(dest_dir: str, download: Callable = default_download, url: str = WHEEL_URL, max_wheel: int = MAX_WHEEL,
              **limits) -> FetchResult:
    """Scarica, verifica e salva la DLL in dest_dir. Non solleva mai. `limits`: costanti di extract_dll."""
    try:
        try:
            wheel = download(url, max_wheel)
        except DownloadError:
            raise
        except Exception as e:  # URLError, TimeoutError, OSError, ValueError...
            raise DownloadError(NETWORK, "download non riuscito (%s)" % (str(e) or type(e).__name__)[:80]) from None
        if not isinstance(wheel, (bytes, bytearray)):
            raise DownloadError(NETWORK, "download non riuscito")
        if len(wheel) > max_wheel:
            raise DownloadError(TOO_BIG, "file troppo grande")
        data = extract_dll(bytes(wheel), **limits)
        try:
            path = write_atomic(dest_dir, DLL_NAME, data)
        except Exception as e:
            raise DownloadError(NOT_WRITABLE, "impossibile salvare in %s (%s)" % (dest_dir, type(e).__name__)) from None
        try:
            present = os.path.isfile(path)
        except Exception:
            present = False
        if not present:
            raise DownloadError(NOT_WRITABLE, "la DLL salvata e' sparita: controlla l'antivirus")
        return FetchResult(True, path, OK, "SimConnect.dll salvata in %s" % dest_dir)
    except DownloadError as e:
        extra = " Controlla l'antivirus." if e.code == NOT_WRITABLE else ""
        return FetchResult(False, "", e.code, "%s.%s %s" % (e.message, extra, MANUAL))
    except Exception as e:  # rete di sicurezza
        return FetchResult(False, "", NETWORK, "errore (%s). %s" % (type(e).__name__, MANUAL))


_lock = threading.Lock()
_running = False


def is_running() -> bool:
    return _running


def describe() -> str:
    """Testo per la conferma dell'utente: origine, file attesi con SHA-256 e dimensione (mostrato prima del download)."""
    return ("Download SimConnect.dll from %s (PyPI package SimConnect 0.4.26, %d bytes, SHA-256 %s). "
            "Only the DLL (%d bytes, SHA-256 %s) is extracted and saved in vendor/. Continue?"
            % (ALLOWED_HOST, WHEEL_SIZE, WHEEL_SHA256, DLL_SIZE, DLL_SHA256))


def start_fetch(dest_dir: str, on_done: Optional[Callable[[FetchResult], None]] = None,
                download: Callable = default_download, **kw) -> Optional[threading.Thread]:
    """Thread daemon: scarica e chiama on_done(result) una volta. None se gia' in corso."""
    global _running
    with _lock:
        if _running:
            return None
        _running = True

    def run() -> None:
        global _running
        try:
            result = fetch_dll(dest_dir, download, **kw)
            if not result.ok:
                log.warning("Download SimConnect.dll: %s", result.message)
            try:
                if on_done is not None:
                    on_done(result)
            except Exception:
                log.exception("Download SimConnect.dll: errore in on_done")
        finally:
            with _lock:
                _running = False

    try:
        t = threading.Thread(target=run, daemon=True, name="dll_fetch")
        t.start()
        return t
    except Exception:
        with _lock:
            _running = False
        return None
