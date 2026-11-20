"""Launcher: opens the status window at once, updates the repo (git pull --ff-only), installs the dependencies when
requirements.txt changed, then runs the app in the same process. Started by start.bat with pythonw (no console).

Usage: pythonw launcher.py [main.py arguments]   (--no-gui for the console, --unlink to revoke the server link)

Prudence rules: no print (under pythonw stdout/stderr are None), every exception goes to logs/captain.log and a
message box; a failed git pull never discards local changes and the app starts with the present version. If the update
changes the code the launcher restarts itself once (VC_RELAUNCHED) so gui.py/status.py/applog.py already loaded do not
stay at the old version. If the GUI cannot be imported (tkinter missing or gui.py broken) it updates anyway and falls
back to --no-gui. --unlink and --probe (python simconnect_backend.py --probe) stay on the command line.
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import os
import queue
import subprocess
import sys
import threading

import applog
import paths

HERE = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(HERE, "requirements.txt")
REQ_HASH = os.path.join(HERE, ".requirements.sha256")
RELAUNCH_ENV = "VC_RELAUNCHED"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PENDING, OK, WARN, ERROR = "PENDING", "OK", "WARN", "ERROR"  # same values as status.py
TITLE = "Virtual Captain"
log = logging.getLogger("captain.launcher")


def error_box(message: str, root=None) -> None:
    try:
        import tkinter
        from tkinter import messagebox
        own = None
        if root is None:
            own = tkinter.Tk()
            own.withdraw()
        messagebox.showerror(TITLE, message, parent=root or own)
        if own is not None:
            own.destroy()
    except Exception:  # noqa: BLE001
        pass


def install_hooks() -> None:
    def hook(exc_type, exc, tb):
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            error_box("Unhandled error: %s\nDetails in logs/captain.log" % exc)

    def thread_hook(args):  # logged only: tkinter must never be touched outside the main thread
        log.error("Unhandled error in thread %s", getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_hook


# --- update (no GUI access: writes only through report(key, state, detail))
def run_cmd(cmd, timeout):
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def git_head() -> str:
    try:
        r = run_cmd(["git", "rev-parse", "--short", "HEAD"], 15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def last_line(text: str, default: str = "") -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:160] if lines else default


def log_only(key, state, detail=""):
    log.info("%s: %s %s", key, state, detail)


def update_code(report=log_only) -> bool:
    """git pull --ff-only. True if HEAD changed. Never raises."""
    report("code", PENDING, "git pull in progress...")
    before = git_head()
    try:
        r = run_cmd(["git", "pull", "--ff-only"], 60)
    except Exception as e:  # noqa: BLE001  (git missing, timeout, ...)
        log.error("git pull failed: %s", e)
        report("code", ERROR, "git pull failed: %s (using the present version)" % e)
        return False
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        log.error("git pull failed (code %s): %s", r.returncode, out)
        report("code", ERROR, "git pull failed, using the present version: %s" % last_line(out, "error"))
        return False
    after = git_head()
    log.info("git pull: %s", last_line(out, "ok"))
    if before and after and before != after:
        report("code", OK, "updated %s -> %s" % (before, after))
        return True
    report("code", OK, "up to date (%s)" % (after or "?"))
    return False


def install_requirements(report=log_only) -> bool:
    """Installs requirements.txt if it changed since the last successful install. Never blocks the start."""
    if not os.path.isfile(REQ):
        report("deps", OK, "no requirements.txt")
        return True
    try:
        with open(REQ, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        try:
            with open(REQ_HASH, "r", encoding="utf-8") as f:
                if f.read().strip() == digest:
                    report("deps", OK, "already installed")
                    return True
        except OSError:
            pass
        report("deps", PENDING, "pip install in progress...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", REQ], cwd=HERE, capture_output=True,
                           text=True, timeout=300, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        if r.returncode != 0:
            detail = last_line(r.stdout + r.stderr, "code %s" % r.returncode)
            log.error("pip install failed: %s", detail)
            report("deps", ERROR, "pip failed: %s" % detail)
            return False
        with open(REQ_HASH, "w", encoding="utf-8") as f:
            f.write(digest)
        log.info("Dependencies updated")
        report("deps", OK, "updated")
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.error("pip install failed: %s", e)
        report("deps", ERROR, "pip failed: %s (starting anyway)" % e)
        return False


def relaunch(argv, mark: bool = True) -> bool:
    try:
        env = dict(os.environ)
        if mark:
            env[RELAUNCH_ENV] = "1"
        else:
            env.pop(RELAUNCH_ENV, None)
        subprocess.Popen([sys.executable, os.path.join(HERE, "launcher.py"), *argv], cwd=HERE, env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
        log.info("Restarting the launcher.")
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Launcher restart failed: %s (continuing with the version already loaded)", e)
        return False


def load_app():
    """Imports main.py in-process (after the update, so it is the new code)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    return importlib.import_module("main")


def run_headless(argv, relaunched: bool, gui_failed: bool = False) -> int:
    """No window: updates (not for --unlink), then runs the app in-process with the console."""
    changed = False
    if not relaunched and "--unlink" not in argv:
        changed = update_code(log_only)
        install_requirements(log_only)
    if gui_failed and changed and relaunch(argv):
        return 0
    app_argv = [a for a in argv if a != "--no-gui"]
    return int(load_app().main(app_argv) or 0)


class GuiUnavailable(Exception):
    pass


def run_window(argv, relaunched: bool) -> int:
    root = None
    try:
        import tkinter  # noqa: F401
        import gui
        import status

        log_queue: "queue.Queue" = queue.Queue(applog.GUI_QUEUE_SIZE)
        applog.setup_logging(paths.log_dir(), console=False, gui_queue=log_queue)
        registry = status.make_registry()
        root = gui.create_root()
        actions: dict = {}
        win = gui.build_window(root, registry, log_queue, actions, TITLE)
    except Exception as e:  # noqa: BLE001  (tkinter missing, GUI modules broken, no display)
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
        raise GuiUnavailable(str(e))
    applog.redirect_stdio(applog.get_logger())  # print()/tracebacks end up in the log (pythonw has no stdout)
    root.report_callback_exception = lambda et, ev, tb: (
        log.error("Error in the GUI", exc_info=(et, ev, tb)), error_box("Error in the GUI: %s" % ev, root))
    state = {"done": False, "changed": False, "app": None}

    def worker() -> None:
        try:
            if relaunched:
                registry.report("code", OK, "updated (%s)" % (git_head() or "?"))
                registry.report("deps", OK, "already installed")
            else:
                state["changed"] = update_code(registry.report)
                install_requirements(registry.report)
        except Exception:  # noqa: BLE001
            log.exception("Update error")
            registry.report("code", ERROR, "update error (see the log)")
        finally:
            state["done"] = True

    def finish() -> None:
        if state["changed"] and not relaunched and relaunch(argv):
            win.quit()
            return
        startup_error = ()
        try:
            module = load_app()
            startup_error = module.StartupError
            app = module.App(argv, registry)
            app.start()
            app.start_loop()
        except Exception as e:  # noqa: BLE001
            if startup_error and isinstance(e, startup_error):  # port in use, ...: the message is for the user
                log.error("%s", e)
                error_box(str(e), root)
            else:
                log.exception("App start failed")
                error_box("Start failed: %s\nDetails in logs/captain.log" % e, root)
            win.quit()
            return
        state["app"] = app
        import dll_fetch
        from simbrief import briefing_url
        actions.update({
            "shutdown": app.shutdown, "reconnect": app.reconnect, "fetch_dll": app.fetch_dll,
            "page_url": lambda: app.url, "simbrief_url": lambda: briefing_url(app.st.plan), "login_info": lambda: app.login_info,
            "dll_missing": lambda: bool(app.st.dll_missing and not app.sim),
            "dll_describe": dll_fetch.describe, "sim_on": lambda: app.sim,
            "test_links_on": lambda: app.st.cl_trust_links, "set_test_links": app.set_test_links,
            "restart_with": app.request_restart_with})
        head = git_head()
        root.title("%s %s%s" % (TITLE, module.uplink.APP_VERSION, " (%s)" % head if head else ""))

    def poll() -> None:
        if win.closed:
            return
        if state["done"]:
            finish()
        else:
            root.after(150, poll)

    threading.Thread(target=worker, name="update", daemon=True).start()
    root.after(150, poll)
    root.mainloop()
    app = state["app"]
    if app is not None and app.restart_argv is not None:  # Advanced > Simulated flight: restart with the new options
        relaunch(app.restart_argv, mark=False)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    headless = "--no-gui" in argv or "--unlink" in argv
    applog.setup_logging(paths.log_dir(), console=headless)
    install_hooks()
    relaunched = os.environ.get(RELAUNCH_ENV) == "1"
    log.info("Launcher started (python %s, arguments: %s)", sys.version.split()[0], " ".join(argv))
    try:
        if headless:
            return run_headless(argv, relaunched)
        try:
            return run_window(argv, relaunched)
        except GuiUnavailable as e:  # tkinter missing or GUI modules broken: update and fall back to --no-gui
            log.exception("GUI not available: %s", e)
            error_box("GUI not available (%s): the app starts WITHOUT a window (log only) and must be closed from "
                      "Task Manager (pythonw). Details in logs/captain.log" % e)
            return run_headless(argv, relaunched, True)
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        log.exception("Launcher error")
        error_box("Launcher error: %s\nDetails in logs/captain.log" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
