"""Pagina web (briefing e stato) per il tablet. Solo libreria standard.

Si lega all'IP di Tailscale (rete privata): mai a tutte le interfacce.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from state import State

WEB_DIR = Path(__file__).resolve().parent / "web"
_LINK = '<link rel="stylesheet" href="/app.css">'
_SCRIPT = '<script src="/app.js"></script>'
# solo questi file sono serviti come statici (niente path traversal: nome esatto)
STATIC = {"/app.css": ("app.css", "text/css"), "/app.js": ("app.js", "text/javascript")}


def _read(name: str) -> str:
    try:
        with open(WEB_DIR / name, encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        raise RuntimeError(f"cartella web/ incompleta: manca {name}; fai git pull") from None


def build_page() -> str:
    """Pagina unica con CSS e JS inline, assemblata da web/index.html, app.css e app.js."""
    html = _read("index.html")
    html = html.replace(_LINK, "<style>\n" + _read("app.css") + "</style>")
    return html.replace(_SCRIPT, "<script>\n" + _read("app.js") + "</script>")


PAGE = build_page()


def tailscale_ip() -> str | None:
    """IPv4 di Tailscale: dal comando `tailscale ip -4`, altrimenti cerca un indirizzo 100.64.0.0/10."""
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
        if out:
            return out[0]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            a, b = (int(x) for x in ip.split(".")[:2])
            if a == 100 and 64 <= b <= 127:
                return ip
    except (OSError, ValueError):
        pass
    return None


def make_handler(st: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # niente rumore nel terminale
            pass

        def _send(self, code, body: bytes, ctype="application/json", extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html")
            elif self.path == "/api/state":
                self._json(st.snapshot())
            elif self.path in STATIC:
                name, ctype = STATIC[self.path]
                self._send(200, _read(name).encode(), ctype, (("X-Content-Type-Options", "nosniff"),))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, OSError):
                return self._send(400, b"bad request", "text/plain")
            try:
                if self.path == "/api/fields":
                    st.update(data)
                elif self.path == "/api/load-plan":
                    st.load_plan(str(data.get("simbrief", "")))
                elif self.path == "/api/simdll-fetch":
                    if not (isinstance(data, dict) and data.get("confirm") is True):
                        return self._send(400, b"confirmation required", "text/plain")
                    try:
                        st.fetch_dll()
                    except ValueError as e:
                        return self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/sim-retry":
                    st.sim_retry()
                elif self.path == "/api/checklist-phase":
                    try:
                        if not isinstance(data, dict):
                            raise ValueError("body must be an object")
                        if "phase" in data:
                            st.set_cl_phase(data["phase"] if isinstance(data["phase"], str) else None)
                        else:
                            st.set_cl_phase(data.get("step"))
                    except ValueError as e:
                        return self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/clearance":
                    try:
                        if data.get("reset") is True:
                            st.reset_clearance()
                        else:
                            st.set_clearance(data)
                    except (ValueError, AttributeError) as e:
                        return self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/thrust-cal":
                    try:
                        if not isinstance(data, dict):
                            raise ValueError("body must be an object")
                        st.calib_action(data.get("action"))
                    except ValueError as e:
                        return self._send(400, str(e).encode(), "text/plain")
                elif self.path == "/api/phase":
                    st.set_phase(data.get("phase", ""))
                else:
                    return self._send(404, b"not found", "text/plain")
            except Exception as e:  # noqa: BLE001
                return self._send(502, str(e).encode(), "text/plain")
            self._json(st.snapshot())

    return H


def serve(st: State, host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(st))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
