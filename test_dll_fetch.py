# Test di dll_fetch.py: wheel finto in memoria, downloader iniettato, NESSUNA rete. Uso: python3 test_dll_fetch.py
import hashlib
import io
import os
import struct
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

import dll_fetch as D


def fake_dll(machine=0x8664, size=2048):
    b = bytearray(size)
    b[:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x80)
    b[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", b, 0x84, machine)
    return bytes(b)


def make_wheel(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def limits(wheel, dll, member=D.MEMBER):
    return dict(wheel_sha256=hashlib.sha256(wheel).hexdigest(), wheel_size=len(wheel), member=member,
                dll_size=len(dll), dll_sha256=hashlib.sha256(dll).hexdigest())


def run(wheel, dest, dll=None, member=D.MEMBER, lim=None, **kw):
    dll = fake_dll() if dll is None else dll
    lim = limits(wheel, dll, member) if lim is None else lim
    return D.fetch_dll(dest, lambda url, mx: wheel, **lim, **kw)


def test_constants():
    assert D.WHEEL_URL.startswith("https://files.pythonhosted.org/") and len(D.WHEEL_SHA256) == 64
    assert len(D.DLL_SHA256) == 64 and D.MEMBER == "SimConnect/SimConnect.dll"


def test_ok_writes_dll():
    dll = fake_dll()
    wheel = make_wheel({D.MEMBER: dll, "SimConnect/__init__.py": b"x"})
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "vendor")
        r = run(wheel, dest, dll)
        assert r.ok and r.code == D.OK and r.path == os.path.join(dest, "SimConnect.dll"), r
        assert open(r.path, "rb").read() == dll and os.listdir(dest) == ["SimConnect.dll"]


def test_wrong_hash():
    dll = fake_dll()
    wheel = make_wheel({D.MEMBER: dll})
    with tempfile.TemporaryDirectory() as d:
        lim = limits(wheel, dll)
        lim["wheel_sha256"] = "0" * 64
        r = run(wheel, d, dll, lim=lim)
        assert not r.ok and r.code == D.HASH and "SimConnect.dll next to" in r.message, r
        lim = limits(wheel, dll)
        lim["dll_sha256"] = "0" * 64
        assert run(wheel, d, dll, lim=lim).code == D.HASH
        lim = limits(wheel, dll)
        lim["wheel_size"] += 1
        assert run(wheel, d, dll, lim=lim).code == D.HASH
        assert os.listdir(d) == []


def test_not_zip():
    junk = b"questo non e' uno zip" * 10
    with tempfile.TemporaryDirectory() as d:
        r = D.fetch_dll(d, lambda u, m: junk, wheel_sha256=hashlib.sha256(junk).hexdigest(), wheel_size=len(junk))
        assert r.code == D.BAD_WHEEL and os.listdir(d) == [], r


def test_member_missing():
    dll = fake_dll()
    wheel = make_wheel({"SimConnect/altro.dll": dll})
    with tempfile.TemporaryDirectory() as d:
        r = run(wheel, d, dll)
        assert r.code == D.NO_MEMBER and os.listdir(d) == [], r


def test_path_traversal_never_used():
    dll = fake_dll()
    with tempfile.TemporaryDirectory() as d:
        dest = os.path.join(d, "a", "b")
        for evil in ("../x.dll", "SimConnect/../../x", "/abs/x.dll"):
            wheel = make_wheel({evil: dll})
            r = run(wheel, dest, dll)
            assert r.code == D.NO_MEMBER, (evil, r)
        assert not os.path.exists(os.path.join(d, "a", "x.dll")) and not os.path.exists(os.path.join(d, "x"))
        assert not os.path.exists(os.path.join(d, "a", "b", "SimConnect.dll"))


def test_dll_too_big_or_false_size():
    dll = fake_dll(size=4096)
    wheel = make_wheel({D.MEMBER: dll})
    with tempfile.TemporaryDirectory() as d:
        lim = limits(wheel, dll)
        lim["dll_size"] = 100  # la size dichiarata nel wheel e' diversa da quella attesa
        r = run(wheel, d, dll, lim=lim)
        assert r.code == D.TOO_BIG and os.listdir(d) == [], r


def test_download_over_limit():
    def big(url, mx):
        raise D.DownloadError(D.TOO_BIG, "file too large")
    with tempfile.TemporaryDirectory() as d:
        assert D.fetch_dll(d, big).code == D.TOO_BIG
        r = D.fetch_dll(d, lambda u, m: b"x" * (m + 1))
        assert r.code == D.TOO_BIG, r
        assert D.fetch_dll(d, lambda u, m: b"x" * 10, max_wheel=5).code == D.TOO_BIG
        assert os.listdir(d) == []


def test_network_errors():
    def err(e):
        def dl(url, mx):
            raise e
        return dl
    with tempfile.TemporaryDirectory() as d:
        for e in (urllib.error.URLError("no rete"), TimeoutError("timeout"), OSError("x")):
            r = D.fetch_dll(d, err(e))
            assert not r.ok and r.code == D.NETWORK and "SimConnect.dll next to" in r.message, r
        assert os.listdir(d) == []


def test_not_writable_and_no_part():
    dll = fake_dll()
    wheel = make_wheel({D.MEMBER: dll})
    with tempfile.TemporaryDirectory() as d:
        blocker = os.path.join(d, "file")
        open(blocker, "w").close()
        r = run(wheel, os.path.join(blocker, "sub"), dll)  # la cartella non si puo' creare
        assert r.code == D.NOT_WRITABLE and "antivirus" in r.message, r
        # errore a meta' scrittura: nessun .part residuo
        saved = os.replace
        def boom(a, b):
            raise PermissionError("negato")
        D.os.replace = boom
        try:
            r = run(wheel, d, dll)
        finally:
            D.os.replace = saved
        assert r.code == D.NOT_WRITABLE and not [n for n in os.listdir(d) if n.endswith(".part")], os.listdir(d)
        assert not os.path.exists(os.path.join(d, "SimConnect.dll"))


def test_dll_not_pe64():
    with tempfile.TemporaryDirectory() as d:
        for bad in (fake_dll(machine=0x014C), b"MZ" + bytes(100), b"ELF" + bytes(100)):
            wheel = make_wheel({D.MEMBER: bad})
            r = run(wheel, d, bad)
            assert r.code == D.BAD_WHEEL and os.listdir(d) == [], r
    assert D.is_pe64(fake_dll()) and not D.is_pe64(fake_dll(0x014C))


def test_start_fetch_and_double_start():
    dll = fake_dll()
    wheel = make_wheel({D.MEMBER: dll})
    lim = limits(wheel, dll)
    gate = [False]
    calls = []

    def dl(url, mx):
        end = time.monotonic() + 3
        while not gate[0] and time.monotonic() < end:
            time.sleep(0.01)
        return wheel

    with tempfile.TemporaryDirectory() as d:
        t = D.start_fetch(d, calls.append, dl, **lim)
        time.sleep(0.1)
        assert D.is_running() and D.start_fetch(d, calls.append, dl, **lim) is None
        gate[0] = True
        t.join(5)
        assert len(calls) == 1 and calls[0].ok and not D.is_running()
        calls.clear()
        D.start_fetch(d, calls.append, lambda u, m: (_ for _ in ()).throw(TimeoutError()), **lim).join(5)
        assert len(calls) == 1 and calls[0].code == D.NETWORK
        D.start_fetch(d, lambda r: 1 / 0, lambda u, m: wheel, **lim).join(5)  # on_done che solleva: nessuna propagazione
        assert not D.is_running()


def test_describe_has_origin_hash_size():
    t = D.describe()
    assert D.ALLOWED_HOST in t and D.WHEEL_SHA256 in t and D.DLL_SHA256 in t and str(D.WHEEL_SIZE) in t and str(D.DLL_SIZE) in t


def test_redirect_rules():
    h = D._HostRedirect(D.ALLOWED_HOST)
    req = urllib.request.Request(D.WHEEL_URL)
    for bad in ("http://files.pythonhosted.org/x.whl", "https://evil.example.com/x.whl",
                "https://files.pythonhosted.org.evil.com/x.whl", "https://files.pythonhosted.org:8443/x.whl"):
        try:
            h.redirect_request(req, None, 302, "Found", {}, bad)
        except urllib.error.URLError:
            continue
        raise AssertionError(bad)
    ok = h.redirect_request(req, None, 302, "Found", {}, "https://files.pythonhosted.org/y.whl")
    assert ok is not None
    assert h.redirect_request(req, None, 302, "Found", {}, "https://files.pythonhosted.org:443/y.whl") is not None
    D._check_url("https://files.pythonhosted.org:443/x", D.ALLOWED_HOST)
    for bad in ("http://files.pythonhosted.org/x", "https://evil.example.com/x", "ftp://files.pythonhosted.org/x",
                "https://files.pythonhosted.org:8443/x"):
        try:
            D.default_download(bad, 10)  # rifiutato prima di aprire qualunque connessione
        except urllib.error.URLError:
            continue
        raise AssertionError(bad)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
