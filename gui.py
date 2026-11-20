"""Status window (tkinter/ttk, standard library only). `import tkinter` only in this file, imported lazily by the launcher.

No business logic: it reads the registry (status.py) and the log queue (applog.py) from the after() loop and calls
the actions it is given (main.App methods); app threads never touch tkinter.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import status
from simbrief import SIMBRIEF_HOME

REFRESH_MS = 200
MAX_LOG_LINES = 500
COLORS = {status.PENDING: "#9e9e9e", status.OK: "#2e9e44", status.WARN: "#e0a800", status.ERROR: "#d32f2f"}
STATE_NAMES = {status.PENDING: "waiting", status.OK: "ok", status.WARN: "warning", status.ERROR: "error"}
DOT = 16
BASE_W, BASE_H = 680, 460
SHUTDOWN_MAX_S = 10.0


def enable_dpi_awareness() -> None:
    """Windows only: avoids a blurry window on high-resolution screens. Before creating Tk."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def window_size(screen_w: int, screen_h: int, scale: float = 1.0):
    """Initial geometry and minsize (pixels) for the given DPI scale, clamped to the screen."""
    scale = max(1.0, scale)
    w = min(int(BASE_W * scale), screen_w - 80)
    h = min(int(BASE_H * scale), screen_h - 120)
    return w, h, min(int(BASE_W * scale), w), min(int(BASE_H * scale), h)


def log_tag(level: int) -> str:
    return "error" if level >= logging.ERROR else "warn" if level >= logging.WARNING else "debug" if level < logging.INFO else ""


def create_root() -> tk.Tk:
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if sys.platform == "win32" and "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:  # noqa: BLE001
        pass
    return root


class _Row:
    def __init__(self, parent, row: int, item: status.StatusItem, wrap: int = 480, button=None) -> None:
        self.key = item.key
        self.canvas = tk.Canvas(parent, width=DOT + 2, height=DOT + 2, highlightthickness=0)
        self.dot = self.canvas.create_oval(2, 2, DOT, DOT, fill=COLORS[status.PENDING], outline="")
        self.label = ttk.Label(parent, text=item.label)
        self.detail = ttk.Label(parent, text="", foreground="#555555", wraplength=wrap, justify="left")
        self.when = ttk.Label(parent, text="", foreground="#888888")
        # columns: 0 dot, 1 label, 2 detail (stretches), 3 time (right), 4 optional action button
        self.canvas.grid(row=row, column=0, padx=(8, 4), pady=2, sticky="w")
        self.label.grid(row=row, column=1, sticky="w", padx=(0, 12))
        self.detail.grid(row=row, column=2, sticky="w")
        self.when.grid(row=row, column=3, sticky="e", padx=(8, 8))
        self.button = None
        if button is not None:  # (text, command): action on the row, column 4
            self.button = ttk.Button(parent, text=button[0], command=button[1])
            self.button.grid(row=row, column=4, sticky="e", padx=(0, 8), pady=1)
        self.sig = None

    def update(self, item: status.StatusItem) -> None:
        sig = (item.state, item.detail, item.ts)
        if sig == self.sig:
            return
        self.sig = sig
        self.canvas.itemconfigure(self.dot, fill=COLORS.get(item.state, COLORS[status.PENDING]))
        self.detail.configure(text=item.detail or STATE_NAMES.get(item.state, ""))
        self.when.configure(text=time.strftime("%H:%M:%S", time.localtime(item.ts)) if item.ts else "")


class Window:
    def __init__(self, root, registry: status.StatusRegistry, log_queue, actions: dict, title: str) -> None:
        self.root, self.registry, self.log_queue, self.actions = root, registry, log_queue, actions
        self.version_seen = -1
        self.keys: list = []
        self.rows: dict = {}
        self.log_visible = False
        self.closed = False
        self.link_win = None
        self.link_code = None
        self.synced = False  # Advanced variables copied from the app once it exists (it starts after the update)

        root.title(title)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        try:
            scale = max(1.0, root.winfo_fpixels("1i") / 96)
        except Exception:  # noqa: BLE001
            scale = 1.0
        self.wrap = int(480 * scale)
        self.min_wrap = int(200 * scale)
        self.detail_wrap = self.wrap
        try:
            w, h, mw, mh = window_size(root.winfo_screenwidth(), root.winfo_screenheight(), scale)
            root.geometry("%dx%d" % (w, h))
            root.minsize(mw, mh)
        except Exception:  # noqa: BLE001
            root.minsize(int(560 * scale), int(340 * scale))

        # the bar is packed BEFORE the body: it stays at the bottom even with a tall window
        bar = ttk.Frame(root)
        bar.pack(side="bottom", fill="x", padx=6, pady=6)
        self.bar = bar
        self.body = ttk.Frame(root)
        self.body.pack(fill="x", anchor="n", padx=6, pady=(6, 0))
        self.body.columnconfigure(2, weight=1)  # only the detail stretches
        self.body.bind("<Configure>", self._fit_wrap)

        top = ttk.Frame(bar)
        top.pack(fill="x")
        ttk.Button(top, text="Reconnect", command=self.reconnect).pack(side="left")
        self.log_button = ttk.Button(top, text="Show log", command=self.toggle_log)
        self.log_button.pack(side="left", padx=6)
        self.adv = ttk.Menubutton(top, text="Advanced")
        self.adv.pack(side="left", padx=6)
        self._build_advanced()
        ttk.Button(top, text="Exit", command=self.quit).pack(side="right")
        second = ttk.Frame(bar)
        second.pack(fill="x", pady=(6, 0))
        self.link_button = ttk.Button(second, text="Link to server", command=self.show_link)
        self.link_button.pack(side="left")
        self.dll_button = ttk.Button(second, text="Download SimConnect.dll", command=self.fetch_dll)
        self.dll_shown = False

        self.log_frame = ttk.Frame(root)
        self.text = tk.Text(self.log_frame, height=12, wrap="none", state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(self.log_frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("warn", foreground="#b07800")
        self.text.tag_configure("error", foreground="#d32f2f")
        self.text.tag_configure("debug", foreground="#888888")

        self.refresh()
        root.after(REFRESH_MS, self._tick)

    # --- Advanced menu: sim (restarts the program) and test links (immediate)
    def _build_advanced(self) -> None:
        menu = tk.Menu(self.adv, tearoff=False)
        self.sim_var = tk.BooleanVar(value=bool(self._call("sim_on", False)))
        self.links_var = tk.BooleanVar(value=bool(self._call("test_links_on", False)))
        menu.add_checkbutton(label="Simulated flight (restarts the program)", variable=self.sim_var, command=self.toggle_sim)
        menu.add_checkbutton(label="Test links", variable=self.links_var, command=self.toggle_links)
        self.adv["menu"] = menu

    def toggle_links(self) -> None:
        self._call("set_test_links", None, self.links_var.get())

    def toggle_sim(self) -> None:
        on = self.sim_var.get()
        if not messagebox.askyesno("Virtual Captain", "The program restarts %s the simulated flight.\n\nRestart now?"
                                   % ("in" if on else "out of"), parent=self.root):
            self.sim_var.set(not on)
            return
        self._call("restart_with", None, "--sim", on)
        self.quit()

    # --- actions
    def _call(self, name: str, default=None, *args):
        fn = self.actions.get(name)
        if fn is None:
            return default
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            logging.getLogger("captain.gui").exception("Action %s failed", name)
            return default

    def reconnect(self) -> None:
        self._call("reconnect")

    def open_page(self) -> None:
        url = self._call("page_url", "")
        if url:
            webbrowser.open(url)

    def row_buttons(self) -> dict:
        """Action buttons placed on a status row: key -> (text, command)."""
        return {"page": ("Open page", self.open_page), "plan": ("SimBrief", self.open_simbrief)}

    def open_simbrief(self) -> None:
        """Briefing of the loaded plan, else the SimBrief home. The URL is computed at click time (the plan can change)."""
        url = self._call("simbrief_url", "") or SIMBRIEF_HOME
        webbrowser.open(url)

    def fetch_dll(self) -> None:
        text = ("Download SimConnect.dll from PyPI?\n\n" + (self._call("dll_describe", "") or ""))
        if messagebox.askyesno("Download SimConnect.dll", text, parent=self.root):
            self._call("fetch_dll")

    def show_link(self) -> None:
        info = self._call("login_info")
        if not info:
            messagebox.showinfo("Link to server", "Nothing to approve right now: the program is already linked, "
                                "the server link is off, or the code has not arrived yet.", parent=self.root)
            return
        if self.link_win is not None:
            try:
                self.link_win.lift()
                return
            except Exception:  # noqa: BLE001
                self.link_win = None
        win = tk.Toplevel(self.root)
        win.title("Link to server")
        win.transient(self.root)
        self.link_win = win
        self.link_widgets = {}
        ttk.Label(win, text="1. Open the link and sign in to your Virtual Captain account.\n"
                            "2. Check that the code matches, then type its last 4 characters.",
                  justify="left", wraplength=self.wrap).pack(padx=16, pady=(14, 6))
        big = ("TkDefaultFont", 20, "bold")
        self.link_widgets["code"] = ttk.Label(win, text="", font=big)
        self.link_widgets["code"].pack(pady=4)
        self.link_widgets["last4"] = ttk.Label(win, text="")
        self.link_widgets["last4"].pack()
        buttons = ttk.Frame(win)
        buttons.pack(pady=12)
        ttk.Button(buttons, text="Open", command=self._link_open).pack(side="left", padx=4)
        ttk.Button(buttons, text="Copy", command=self._link_copy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Close", command=self._link_close).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", self._link_close)
        self._link_refresh()

    def _link_url(self) -> str:
        info = self._call("login_info") or {}
        return info.get("verification_uri_complete") or info.get("verification_uri") or ""

    def _link_open(self) -> None:
        url = self._link_url()
        if url:
            webbrowser.open(url)

    def _link_copy(self) -> None:
        url = self._link_url()
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)

    def _link_close(self) -> None:
        if self.link_win is not None:
            try:
                self.link_win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.link_win = None

    def _link_refresh(self) -> None:
        """Keeps the code window in sync: new code if it changes, closes when the link is done."""
        if self.link_win is None:
            return
        info = self._call("login_info")
        if not info:
            self._link_close()
            return
        code = info.get("user_code", "")
        if code != self.link_code:
            self.link_code = code
            self.link_widgets["code"].configure(text=code)
            self.link_widgets["last4"].configure(text="last 4 characters:  %s" % code[-4:])

    # --- log
    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
            self.log_button.configure(text="Hide log")
            self.text.see("end")
        else:
            self.log_frame.pack_forget()
            self.log_button.configure(text="Show log")

    # --- closing: the window disappears at once, the shutdown runs on a thread (the loop can take a few seconds)
    def quit(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._link_close()
        try:
            self.root.withdraw()
        except Exception:  # noqa: BLE001
            pass
        done = threading.Event()

        def shutdown() -> None:
            try:
                self._call("shutdown")
            finally:
                done.set()

        threading.Thread(target=shutdown, name="shutdown", daemon=True).start()
        started = time.monotonic()

        def wait_done() -> None:
            if done.is_set() or time.monotonic() - started > SHUTDOWN_MAX_S:
                try:
                    self.root.destroy()
                except Exception:  # noqa: BLE001
                    pass
                return
            self.root.after(100, wait_done)

        wait_done()

    # --- refresh
    def _fit_wrap(self, event=None) -> None:
        try:
            total = self.body.winfo_width()
            if total <= 1 or not self.rows:
                return
            used = sum(self.body.grid_bbox(c, 0)[2] for c in (0, 1, 3, 4))
            wrap = max(self.min_wrap, total - used - 16)
        except Exception:  # noqa: BLE001
            return
        if abs(wrap - self.detail_wrap) < 4:
            return
        self.detail_wrap = wrap
        for row in self.rows.values():
            row.detail.configure(wraplength=wrap)

    def _tick(self) -> None:
        if self.closed:
            return
        try:
            if not self.synced and "sim_on" in self.actions:
                self.synced = True
                self.sim_var.set(bool(self._call("sim_on", False)))
                self.links_var.set(bool(self._call("test_links_on", False)))
            self.refresh()
            self._update_dll_button()
            self._link_refresh()
        except Exception:  # noqa: BLE001
            logging.getLogger("captain.gui").exception("Window update")
        try:
            self.root.after(REFRESH_MS, self._tick)
        except Exception:  # noqa: BLE001
            pass

    def _update_dll_button(self) -> None:
        want = bool(self._call("dll_missing", False))
        if want != self.dll_shown:
            self.dll_shown = want
            if want:
                self.dll_button.pack(side="left", padx=6)
            else:
                self.dll_button.pack_forget()

    def refresh(self) -> None:
        if self.registry.version != self.version_seen:
            self.version_seen = self.registry.version
            snap = self.registry.snapshot()
            keys = [i.key for i in snap]
            keys_changed = keys != self.keys
            if keys_changed:
                self._rebuild(snap)
            changed = False
            for item in snap:
                row = self.rows[item.key]
                before = row.sig
                row.update(item)
                if row.sig != before:
                    changed = True
            if changed or keys_changed:
                self.root.after_idle(self._fit_wrap)
        self._drain_log()

    def _rebuild(self, snap: list) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.rows = {}
        self.keys = [i.key for i in snap]
        row = 0
        group = None
        for item in snap:
            if item.group != group:
                group = item.group
                ttk.Label(self.body, text=group, font=("TkDefaultFont", 10, "bold")).grid(
                    row=row, column=0, columnspan=5, sticky="w", padx=8, pady=(12 if row else 4, 0))
                row += 1
            self.rows[item.key] = _Row(self.body, row, item, self.detail_wrap, self.row_buttons().get(item.key))
            row += 1

    def _drain_log(self) -> None:
        lines = []
        while len(lines) < 200:
            try:
                lines.append(self.log_queue.get_nowait())
            except Exception:  # noqa: BLE001
                break
        if not lines:
            return
        self.text.configure(state="normal")
        for level, text in lines:
            tag = log_tag(level)
            prefix = "ERROR " if tag == "error" else "WARN " if tag == "warn" else ""
            self.text.insert("end", prefix + text + "\n", tag)
        extra = int(self.text.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if extra > 0:
            self.text.delete("1.0", "%d.0" % (extra + 1))
        self.text.configure(state="disabled")
        if self.log_visible:
            self.text.see("end")


def build_window(root, registry, log_queue, actions: dict, title: str = "Virtual Captain") -> Window:
    """Builds the window and starts the after() loop. Actions are all optional (see main.App)."""
    return Window(root, registry, log_queue, actions, title)
