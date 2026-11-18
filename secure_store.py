"""Archivio dei token del collegamento al server pubblico (refresh, access, scadenza, indirizzo).

Windows: blob cifrato con DPAPI (ctypes, nessuna dipendenza) in %LOCALAPPDATA%\\VirtualCaptain\\link.dat,
legato all'utente di Windows. Altri sistemi: file con permessi 0600 in ~/.config/virtual-captain/,
NON cifrato (avviso in console). Scrittura atomica (file temporaneo + os.replace); un blob
illeggibile vale "non collegato". I token non vanno mai nei log ne' nella repo.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import POINTER, Structure, c_ubyte, c_void_p, c_ulong
from pathlib import Path
from typing import Callable, Optional

APP_DIR_WIN = "VirtualCaptain"
APP_DIR_POSIX = "virtual-captain"
BLOB_NAME = "link.dat"
ENTROPY = b"VirtualCaptain.link.v1"  # lega il blob all'app: non e' un segreto
CRYPTPROTECT_UI_FORBIDDEN = 0x1
FIELDS = {"refresh": str, "access": str, "base_url": str}


class DATA_BLOB(Structure):
    _fields_ = [("cbData", c_ulong), ("pbData", POINTER(c_ubyte))]


def _blob(data: bytes):
    buf = (c_ubyte * max(len(data), 1)).from_buffer_copy(data or b"\0")
    return DATA_BLOB(len(data), ctypes.cast(buf, POINTER(c_ubyte))), buf


class PlainProtector:
    """Nessuna cifratura (solo file 0600): per i sistemi senza DPAPI."""
    encrypted = False

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


def windows_apis():
    """(CryptProtectData, CryptUnprotectData, LocalFree) reali; solo su Windows."""
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        fn.argtypes = [POINTER(DATA_BLOB), c_void_p, POINTER(DATA_BLOB), c_void_p, c_void_p,
                       c_ulong, POINTER(DATA_BLOB)]
        fn.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [c_void_p]
    kernel32.LocalFree.restype = c_void_p
    return crypt32.CryptProtectData, crypt32.CryptUnprotectData, kernel32.LocalFree


class DpapiProtector:
    """DPAPI via ctypes. `apis` e' iniettabile (un finto) per provare il percorso senza Windows."""
    encrypted = True

    def __init__(self, apis=None):
        self._protect, self._unprotect, self._free = apis or windows_apis()

    def _call(self, fn, data: bytes) -> bytes:
        src, _keep = _blob(data)
        ent, _keep2 = _blob(ENTROPY)
        out = DATA_BLOB()
        if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                  CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._free(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._protect, data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self._unprotect, data)


def default_dir(platform: str = sys.platform, env=os.environ, home: Optional[Path] = None) -> Path:
    if platform == "win32":
        # LOCALAPPDATA: the blob is bound to this machine and must not roam with the profile
        base = env.get("LOCALAPPDATA") or env.get("APPDATA") or str((home or Path.home()) / "AppData" / "Local")
        return Path(base) / APP_DIR_WIN
    return (home or Path.home()) / ".config" / APP_DIR_POSIX


class TokenStore:
    """save / load / clear dei token; `protector` e `directory` iniettabili per i test."""

    def __init__(self, directory: Optional[Path] = None, protector=None,
                 warn: Callable[[str], None] = print):
        self.dir = Path(directory) if directory else default_dir()
        if protector is None:
            protector = DpapiProtector() if sys.platform == "win32" else PlainProtector()
        self.protector, self._warn, self._warned = protector, warn, False
        self.path = self.dir / BLOB_NAME

    def save(self, data: dict) -> None:
        payload = {"refresh": data["refresh"], "access": data["access"],
                   "access_exp": float(data["access_exp"]), "base_url": data["base_url"]}
        raw = self.protector.protect(json.dumps(payload).encode("utf-8"))
        if not self.protector.encrypted and not self._warned:
            self._warned = True
            self._warn(f"Avviso: su questo sistema i token del collegamento sono in un file NON cifrato "
                       f"(permessi 0600): {self.path}")
        self.dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.dir, 0o700)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self) -> Optional[dict]:
        """I token salvati, oppure None (assenti, corrotti o di un altro utente: non collegato)."""
        try:
            data = json.loads(self.protector.unprotect(self.path.read_bytes()).decode("utf-8"))
        except Exception:  # noqa: BLE001  (file assente, blob corrotto, JSON non valido, DPAPI rifiuta)
            return None
        if not isinstance(data, dict):
            return None
        for name, kind in FIELDS.items():
            if not isinstance(data.get(name), kind) or not data[name]:
                return None
        exp = data.get("access_exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return {"refresh": data["refresh"], "access": data["access"],
                "access_exp": float(exp), "base_url": data["base_url"]}

    def clear(self) -> None:
        for p in (self.path, self.path.with_name(self.path.name + ".tmp")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
