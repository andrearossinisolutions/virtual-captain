"""Pagina web (briefing e stato) per il tablet. Solo libreria standard.

Di default si lega a 127.0.0.1 (`--host` per cambiarlo).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import commands
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
        raise RuntimeError(f"web/ folder incomplete: {name} is missing; run git pull") from None


def build_page() -> str:
    """Pagina unica con CSS e JS inline, assemblata da web/index.html, app.css e app.js."""
    html = _read("index.html")
    html = html.replace(_LINK, "<style>\n" + _read("app.css") + "</style>")
    return html.replace(_SCRIPT, "<script>\n" + _read("app.js") + "</script>")


PAGE = build_page()


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
            name = self.path[len("/api/"):] if self.path.startswith("/api/") else ""
            try:
                if name == "simdll-fetch":
                    if not (isinstance(data, dict) and data.get("confirm") is True):
                        return self._send(400, b"confirmation required", "text/plain")
                    try:
                        st.fetch_dll()
                    except ValueError as e:
                        return self._send(400, str(e).encode(), "text/plain")
                elif name in commands.COMMANDS:
                    try:
                        commands.run_command(st, name, data)
                    except commands.BadRequest as e:
                        return self._send(400, str(e).encode(), "text/plain")
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
