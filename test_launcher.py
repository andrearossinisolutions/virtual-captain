import hashlib
import subprocess
from types import SimpleNamespace

import pytest

import launcher


@pytest.fixture
def env(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("qrcode\n")
    monkeypatch.setattr(launcher, "REQ", str(req))
    monkeypatch.setattr(launcher, "REQ_HASH", str(tmp_path / ".requirements.sha256"))
    monkeypatch.setattr(launcher, "HERE", str(tmp_path))
    return tmp_path


def digest():
    return hashlib.sha256(b"qrcode\n").hexdigest()


def fake_run(monkeypatch, rc=0, exc=None, out=""):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if exc:
            raise exc
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")
    monkeypatch.setattr(launcher.subprocess, "run", run)
    return calls


def test_same_hash_skips_pip(env, monkeypatch):
    (env / ".requirements.sha256").write_text(digest())
    calls = fake_run(monkeypatch)
    assert launcher.install_requirements() is True
    assert calls == []


@pytest.mark.parametrize("old", [None, "deadbeef"])
def test_new_or_missing_hash_runs_pip_and_saves(env, monkeypatch, old):
    if old:
        (env / ".requirements.sha256").write_text(old)
    calls = fake_run(monkeypatch)
    assert launcher.install_requirements() is True
    cmd, kw = calls[0]
    assert cmd[1:4] == ["-m", "pip", "install"] and kw["timeout"] == 300
    assert (env / ".requirements.sha256").read_text() == digest()


def test_pip_failure_does_not_save(env, monkeypatch, capsys):
    fake_run(monkeypatch, rc=1, out="a\nERROR: boom")
    assert launcher.install_requirements() is False
    assert not (env / ".requirements.sha256").exists()
    assert "pip non riuscito: ERROR: boom" in capsys.readouterr().out


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("pip", 300), OSError("no pip")])
def test_errors_are_contained(env, monkeypatch, exc):
    fake_run(monkeypatch, exc=exc)
    assert launcher.install_requirements() is False
    assert not (env / ".requirements.sha256").exists()


def test_no_requirements_no_pip(env, monkeypatch):
    (env / "requirements.txt").unlink()
    calls = fake_run(monkeypatch)
    assert launcher.install_requirements() is True
    assert calls == []


def test_main_returns_main_code_even_if_pip_fails(env, monkeypatch):
    monkeypatch.setattr(launcher, "update", lambda: True)
    fake_run(monkeypatch, exc=OSError("x"))
    monkeypatch.setattr(launcher.subprocess, "call", lambda *a, **k: 7)
    assert launcher.main() == 7
