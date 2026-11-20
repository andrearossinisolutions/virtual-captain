"""Token store tests: file backend, DPAPI path with a fake (no Windows needed), corruption, atomicity."""
from __future__ import annotations

import ctypes
import json
import os
import stat
from pathlib import Path

import pytest

import secure_store
from secure_store import DpapiProtector, PlainProtector, TokenStore

TOK = {"refresh": "R" * 40, "access": "A" * 40, "access_exp": 1234.5, "base_url": "https://vc.example.test"}


def store(tmp_path, protector=None, warn=None):
    return TokenStore(tmp_path / "vc", protector or PlainProtector(), warn=warn or (lambda m: None))


class FakeDpapi:
    """Stands for crypt32/kernel32: reads the DATA_BLOBs through ctypes like the real calls would."""

    def __init__(self):
        self.keep, self.freed, self.calls, self.fail = [], [], [], False

    def _xform(self, kind, pin, descr, pent, res, prompt, flags, pout):
        blob, ent = pin._obj, pent._obj
        data = ctypes.string_at(blob.pbData, blob.cbData)
        self.calls.append((kind, ctypes.string_at(ent.pbData, ent.cbData), flags, descr, res, prompt))
        if self.fail:
            return 0
        out = bytes(b ^ 0x5A for b in data)
        buf = ctypes.create_string_buffer(out, len(out))
        self.keep.append(buf)
        pout._obj.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        pout._obj.cbData = len(out)
        return 1

    def protect(self, *a):
        return self._xform("protect", *a)

    def unprotect(self, *a):
        return self._xform("unprotect", *a)

    def free(self, ptr):
        self.freed.append(ptr)

    def apis(self):
        return self.protect, self.unprotect, self.free


def test_roundtrip_and_clear(tmp_path):
    s = store(tmp_path)
    assert s.load() is None
    s.save(TOK)
    assert s.load() == TOK
    s.save(dict(TOK, access="B" * 40))
    assert s.load()["access"] == "B" * 40
    s.clear()
    assert s.load() is None and not s.path.exists()
    s.clear()  # idempotent


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_file_is_0600_and_dir_0700(tmp_path):
    s = store(tmp_path)
    s.save(TOK)
    assert stat.S_IMODE(s.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(s.dir.stat().st_mode) == 0o700
    s.save(TOK)  # the replacement keeps the mode
    assert stat.S_IMODE(s.path.stat().st_mode) == 0o600


def test_unencrypted_warning_once_and_never_with_tokens(tmp_path):
    msgs = []
    s = store(tmp_path, warn=msgs.append)
    s.save(TOK)
    s.save(TOK)
    assert len(msgs) == 1 and "UNENCRYPTED" in msgs[0]
    assert TOK["refresh"] not in msgs[0] and TOK["access"] not in msgs[0]
    msgs2 = []
    d = store(tmp_path / "d", DpapiProtector(FakeDpapi().apis()), warn=msgs2.append)
    d.save(TOK)
    assert msgs2 == []  # encrypted: nothing to warn about


@pytest.mark.parametrize("content", [b"", b"\x00\x01garbage", b"{not json", b"[]", b'"text"', b"null",
                                     json.dumps({"refresh": "r"}).encode(),
                                     json.dumps(dict(TOK, access_exp="soon")).encode(),
                                     json.dumps(dict(TOK, refresh="")).encode(),
                                     json.dumps(dict(TOK, access=5)).encode(),
                                     json.dumps(dict(TOK, access_exp=True)).encode(),
                                     b"\xff\xfe\xfa"])
def test_corrupt_blob_means_not_linked(tmp_path, content):
    s = store(tmp_path)
    s.dir.mkdir(parents=True)
    s.path.write_bytes(content)
    assert s.load() is None


def test_atomic_write_keeps_the_old_file_on_failure(tmp_path, monkeypatch):
    s = store(tmp_path)
    s.save(TOK)
    before = s.path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        s.save(dict(TOK, access="C" * 40))
    monkeypatch.undo()
    assert s.path.read_bytes() == before and s.load() == TOK
    assert not s.path.with_name(s.path.name + ".tmp").exists()  # no leftovers


def test_write_goes_through_a_temp_file_and_replace(tmp_path, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append((Path(a).name, Path(b).name)), real(a, b))[1])
    store(tmp_path).save(TOK)
    assert calls == [("link.dat.tmp", "link.dat")]


def test_dpapi_path_with_fake_api(tmp_path):
    fake = FakeDpapi()
    s = store(tmp_path, DpapiProtector(fake.apis()))
    s.save(TOK)
    raw = s.path.read_bytes()
    assert TOK["refresh"].encode() not in raw and b"refresh" not in raw  # what lands on disk is the "encrypted" blob
    assert s.load() == TOK
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["protect", "unprotect"]
    for _, entropy, flags, descr, res, prompt in fake.calls:
        assert entropy == secure_store.ENTROPY and flags == secure_store.CRYPTPROTECT_UI_FORBIDDEN == 1
        assert descr is None and res is None and prompt is None
    assert len(fake.freed) == 2  # LocalFree on every output buffer


def test_dpapi_failure_is_not_linked_and_save_raises(tmp_path):
    fake = FakeDpapi()
    s = store(tmp_path, DpapiProtector(fake.apis()))
    s.save(TOK)
    fake.freed.clear()
    fake.fail = True  # e.g. blob made by another Windows user
    assert s.load() is None
    with pytest.raises(OSError):
        s.save(TOK)
    assert fake.freed == []  # nothing to free on failure


def test_default_dir_per_platform(tmp_path):
    both = {"LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming")}
    assert secure_store.default_dir("win32", both, tmp_path) == tmp_path / "Local" / "VirtualCaptain"
    only_roaming = {"APPDATA": str(tmp_path / "Roaming")}
    assert secure_store.default_dir("win32", only_roaming, tmp_path) == tmp_path / "Roaming" / "VirtualCaptain"
    assert secure_store.default_dir("win32", {}, tmp_path) == tmp_path / "AppData" / "Local" / "VirtualCaptain"
    assert secure_store.default_dir("darwin", {}, tmp_path) == tmp_path / ".config" / "virtual-captain"
    assert secure_store.BLOB_NAME == "link.dat"
