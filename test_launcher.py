"""Launcher tests (no pytest): update/pip functions with the subprocess calls faked. Run: python3 test_launcher.py"""
import hashlib
import os
import subprocess
import tempfile
from types import SimpleNamespace

import launcher

DIGEST = hashlib.sha256(b"websockets\n").hexdigest()


class Env:
    """Temporary requirements.txt and hash file; restores the launcher globals on exit."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.saved = (launcher.REQ, launcher.REQ_HASH, launcher.HERE, launcher.subprocess.run)
        launcher.REQ, launcher.REQ_HASH, launcher.HERE = (os.path.join(d, "requirements.txt"),
                                                          os.path.join(d, ".requirements.sha256"), d)
        with open(launcher.REQ, "w") as f:
            f.write("websockets\n")
        self.calls = []
        return self

    def fake_run(self, rc=0, exc=None, out=""):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            if exc:
                raise exc
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        launcher.subprocess.run = run

    def __exit__(self, *a):
        launcher.REQ, launcher.REQ_HASH, launcher.HERE, launcher.subprocess.run = self.saved
        self.tmp.cleanup()


def reports():
    got = []
    return got, lambda key, state, detail="": got.append((key, state, detail))


def test_same_hash_skips_pip():
    with Env() as e:
        with open(launcher.REQ_HASH, "w") as f:
            f.write(DIGEST)
        e.fake_run()
        got, rep = reports()
        assert launcher.install_requirements(rep) is True
        assert e.calls == [] and got[-1] == ("deps", "OK", "already installed")


def test_new_or_missing_hash_runs_pip_and_saves():
    for old in (None, "deadbeef"):
        with Env() as e:
            if old:
                with open(launcher.REQ_HASH, "w") as f:
                    f.write(old)
            e.fake_run()
            assert launcher.install_requirements(reports()[1]) is True
            cmd, kw = e.calls[0]
            assert cmd[1:4] == ["-m", "pip", "install"] and kw["timeout"] == 300
            assert open(launcher.REQ_HASH).read() == DIGEST


def test_pip_failure_does_not_save_and_reports():
    with Env() as e:
        e.fake_run(rc=1, out="a\nERROR: boom")
        got, rep = reports()
        assert launcher.install_requirements(rep) is False
        assert not os.path.exists(launcher.REQ_HASH)
        assert got[-1] == ("deps", "ERROR", "pip failed: ERROR: boom")


def test_errors_are_contained():
    for exc in (subprocess.TimeoutExpired("pip", 300), OSError("no pip")):
        with Env() as e:
            e.fake_run(exc=exc)
            assert launcher.install_requirements(reports()[1]) is False
            assert not os.path.exists(launcher.REQ_HASH)


def test_no_requirements_no_pip():
    with Env() as e:
        os.remove(launcher.REQ)
        e.fake_run()
        assert launcher.install_requirements(reports()[1]) is True
        assert e.calls == []


def test_update_code_reports_changed_unchanged_and_failed():
    saved = launcher.run_cmd
    try:
        heads = iter(["aaa1111", "bbb2222"])

        def run_changed(cmd, timeout):
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout=next(heads) + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="Updating aaa..bbb\n", stderr="")
        launcher.run_cmd = run_changed
        got, rep = reports()
        assert launcher.update_code(rep) is True and got[-1] == ("code", "OK", "updated aaa1111 -> bbb2222")

        def run_same(cmd, timeout):
            return SimpleNamespace(returncode=0, stdout="ccc3333\n" if cmd[1] == "rev-parse" else "Already up to date.\n", stderr="")
        launcher.run_cmd = run_same
        got, rep = reports()
        assert launcher.update_code(rep) is False and got[-1] == ("code", "OK", "up to date (ccc3333)")

        def run_fail(cmd, timeout):
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="ccc3333\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="fatal: not possible to fast-forward")
        launcher.run_cmd = run_fail
        got, rep = reports()
        assert launcher.update_code(rep) is False and got[-1][1] == "ERROR" and "fast-forward" in got[-1][2]

        def run_boom(cmd, timeout):
            raise OSError("git missing")
        launcher.run_cmd = run_boom
        got, rep = reports()
        assert launcher.update_code(rep) is False and got[-1][1] == "ERROR"
    finally:
        launcher.run_cmd = saved


def test_headless_unlink_skips_the_update_and_passes_arguments_to_main():
    saved = (launcher.update_code, launcher.install_requirements, launcher.load_app)
    seen = {}
    try:
        launcher.update_code = lambda *a: seen.setdefault("pull", True)
        launcher.install_requirements = lambda *a: seen.setdefault("pip", True)
        launcher.load_app = lambda: SimpleNamespace(main=lambda argv: seen.setdefault("argv", argv) and 7)
        assert launcher.run_headless(["--unlink"], False) == 7
        assert "pull" not in seen and seen["argv"] == ["--unlink"]
        seen.clear()
        assert launcher.run_headless(["--no-gui", "--sim"], False) == 7
        assert seen["pull"] and seen["pip"] and seen["argv"] == ["--sim"]
    finally:
        launcher.update_code, launcher.install_requirements, launcher.load_app = saved


def run_all():
    names = sorted(n for n, f in globals().items() if n.startswith("test_") and callable(f))
    for n in names:
        globals()[n]()
        print("ok", n)


if __name__ == "__main__":
    run_all()
