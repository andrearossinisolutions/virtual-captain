"""Pagina web (briefing e stato) per il tablet. Solo libreria standard.

Si lega all'IP di Tailscale (rete privata): mai a tutte le interfacce.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from state import State

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Virtual Captain</title>
<style>
:root{--bg:#0f1720;--card:#18232f;--fg:#e8eef4;--mut:#8da1b5;--acc:#3aa0ff;--ok:#38c172;--bad:#e3564a;--bd:#2b3a49}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.4 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 12px}h2{font-size:16px;color:var(--mut);margin:20px 0 8px;text-transform:uppercase;letter-spacing:.06em}
.pill{display:inline-block;padding:4px 12px;border-radius:99px;background:var(--card);border:1px solid var(--bd);font-size:15px;margin-right:6px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:8px}.row>*{flex:1}
label{display:block;color:var(--mut);font-size:14px;margin-bottom:4px}
input,select{width:100%;padding:12px;font-size:20px;border-radius:10px;border:1px solid var(--bd);background:#0c131b;color:var(--fg)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
button{padding:14px 18px;font-size:19px;border:0;border-radius:12px;background:var(--acc);color:#fff;font-weight:600}
button.big{width:100%;padding:20px;font-size:23px;background:var(--ok)}button.big.fly{background:#b8862b}
button:disabled{opacity:.5}#info{color:var(--mut);font-size:15px;margin:10px 0 0;min-height:1.4em}
ul{list-style:none;margin:0;padding:0;font:15px/1.5 ui-monospace,monospace}li{padding:6px 0;border-bottom:1px solid var(--bd)}
</style></head><body><main>
<h1>Virtual Captain</h1>
<div><span class="pill" id="phase"></span><span class="pill" id="aao"></span></div>
<h2>Briefing</h2>
<div class="card">
  <div class="row"><div><label for="simbrief">SimBrief ID or username</label><input id="simbrief" autocapitalize="off" autocorrect="off"></div>
  <div style="flex:0 0 auto;align-self:end"><button id="load">Load latest plan</button></div></div>
  <p id="info"></p>
  <div class="grid" id="grid"></div>
  <div class="grid"><div><label for="mode">Mode</label><select id="mode"></select></div></div>
</div>
<button class="big" id="go">Briefing complete</button>
<h2>Captain</h2><div class="card"><ul id="log"></ul></div>
</main>
<script>
const $=id=>document.getElementById(id);let lastRev=-1,timer=null,phase="briefing";
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!r.ok){const t=await r.text();throw new Error(t||r.status)}return r.json()}
function build(s){const g=$("grid");g.innerHTML="";for(const [k,l] of s.labels){const d=document.createElement("div");
  d.innerHTML=`<label>${l}</label><input data-k="${k}" inputmode="${["origin","destination","runway","flap"].includes(k)?"text":"numeric"}" autocomplete="off">`;g.appendChild(d)}
  $("mode").innerHTML=s.modes.map(m=>`<option>${m}</option>`).join("");
  document.querySelectorAll("[data-k]").forEach(i=>i.oninput=save);$("mode").onchange=save;$("simbrief").oninput=save}
function fill(s){document.querySelectorAll("[data-k]").forEach(i=>i.value=s.fields[i.dataset.k]);$("simbrief").value=s.simbrief;$("mode").value=s.mode}
function collect(){const o={simbrief:$("simbrief").value,mode:$("mode").value};document.querySelectorAll("[data-k]").forEach(i=>o[i.dataset.k]=i.value);return o}
function save(){clearTimeout(timer);timer=setTimeout(async()=>{try{const s=await api("/api/fields",collect());lastRev=s.rev}catch(e){}},400)}
function show(s){phase=s.phase;const fly=s.phase==="flying";
  $("phase").textContent=fly?"✈ Flying — reading the simulator":"📝 Briefing";
  const a=$("aao");a.textContent=s.aao_ok===null?"AAO: waiting":s.aao_ok?"AAO: connected":"AAO: not responding";a.className="pill "+(s.aao_ok===null?"":s.aao_ok?"ok":"bad");
  $("go").textContent=fly?"Back to briefing":"Briefing complete";$("go").className="big "+(fly?"fly":"");
  $("info").textContent=s.info;
  $("log").innerHTML="";for(const t of s.log){const li=document.createElement("li");li.textContent=t;$("log").appendChild(li)}}
async function poll(){try{const s=await api("/api/state");
  if(lastRev===-1){build(s);fill(s);lastRev=s.rev}else if(s.rev!==lastRev){fill(s);lastRev=s.rev}show(s)}catch(e){$("phase").textContent="Connection lost…"}setTimeout(poll,1500)}
$("load").onclick=async()=>{$("load").disabled=true;$("info").textContent="Loading…";
  try{const s=await api("/api/load-plan",{simbrief:$("simbrief").value});fill(s);lastRev=s.rev;show(s)}catch(e){$("info").textContent="SimBrief error: "+e.message}$("load").disabled=false};
$("go").onclick=async()=>{await api("/api/fields",collect());show(await api("/api/phase",{phase:phase==="flying"?"briefing":"flying"}))};
poll();
</script></body></html>"""


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

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html")
            elif self.path == "/api/state":
                self._json(st.snapshot())
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
