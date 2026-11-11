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
:root{--bg:#0f1720;--card:#18232f;--fg:#e8eef4;--mut:#8da1b5;--acc:#3aa0ff;--ok:#38c172;--bad:#e3564a;--bd:#2b3a49;--warn:#d9a441}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.4 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}[hidden]{display:none!important}
h1{font-size:22px;margin:4px 0 12px}h2{font-size:16px;color:var(--mut);margin:20px 0 8px;text-transform:uppercase;letter-spacing:.06em}
.pill{display:inline-block;padding:4px 12px;border-radius:99px;background:var(--card);border:1px solid var(--bd);font-size:15px;margin-right:6px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:8px}.row>*{flex:1}
label{display:block;color:var(--mut);font-size:14px;margin-bottom:4px}
input,select{width:100%;padding:12px;font-size:20px;border-radius:10px;border:1px solid var(--bd);background:#0c131b;color:var(--fg)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
button{padding:14px 18px;font-size:19px;border:0;border-radius:12px;background:var(--acc);color:#fff;font-weight:600}
button.big{width:100%;padding:20px;font-size:23px;background:var(--ok)}button.big.fly{background:#2f7fd6}
button.ghost{background:transparent;border:1px solid var(--bd);color:var(--mut);font-weight:500}
button:disabled{opacity:.5}#info{color:var(--mut);font-size:15px;margin:10px 0 0;min-height:1.4em}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.chip{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:6px 12px;font-size:17px}
.chip b{color:var(--mut);font-weight:500;font-size:13px;margin-right:6px}
#now{min-height:38vh;display:flex;flex-direction:column;justify-content:center;text-align:center;padding:22px}
#nowtxt{font-size:34px;line-height:1.25;font-weight:600}#nowtime{color:var(--mut);font-size:15px;margin-top:10px}
#nowtxt.idle{color:var(--mut);font-weight:400;font-size:24px}
.plan{background:#0c131b;border:1px solid var(--bd);border-radius:12px;padding:12px;margin-top:12px;font-size:16px}
.plan .route{font:15px/1.5 ui-monospace,monospace;word-break:break-word;margin:6px 0}.plan .meta{color:var(--mut);font-size:14px}
.plan details{margin-top:8px;color:var(--mut)}.plan summary{cursor:pointer}.wp{display:grid;grid-template-columns:5.5em 5.5em 1fr;gap:4px;font:14px ui-monospace,monospace;padding:2px 0}
ul{list-style:none;margin:0;padding:0;font:15px/1.5 ui-monospace,monospace}li{padding:6px 0;border-bottom:1px solid var(--bd);color:var(--mut)}
</style></head><body><main>
<h1>Virtual Captain</h1>
<div><span class="pill" id="phase"></span><span class="pill" id="aao"></span></div>

<section id="cfg">
  <h2>Briefing</h2>
  <div class="card">
    <div class="row"><div style="flex:2"><label for="simbrief">SimBrief ID or username</label><input id="simbrief" enterkeyhint="next" autocapitalize="off" autocorrect="off"></div>
    <div style="flex:1"><label for="mode">Mode</label><select id="mode"></select></div></div>
    <p style="margin:12px 0 0"><button id="load" style="width:100%">Load latest plan</button></p>
    <p id="info"></p>
    <div class="grid" id="apt"></div>
    <div id="plancfg"></div>
    <div class="grid" id="grid"></div>
  </div>
  <button class="big" id="go">Briefing complete</button>
  <p style="margin-top:12px"><button class="ghost" id="end" hidden style="width:100%">End flight (stop reading)</button></p>
</section>

<section id="flt" hidden>
  <div class="chips" id="chips"></div>
  <div id="planflt"></div>
  <div class="card" id="now"><div id="nowtxt" class="idle">Listening…</div><div id="nowtime"></div></div>
  <button class="ghost" id="edit" style="width:100%">Edit briefing</button>
  <h2>Earlier</h2><div class="card"><ul id="log"></ul></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);let lastRev=-1,timer=null,phase="briefing",view="config",last=null;
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!r.ok){const t=await r.text();throw new Error(t||r.status)}return r.json()}
function build(s){const g=$("grid");g.innerHTML="";$("apt").innerHTML="";for(const [k,l] of s.labels){const d=document.createElement("div");
  d.innerHTML=`<label>${l}</label><input data-k="${k}" inputmode="${["origin","destination","dep_time","runway","flap"].includes(k)?"text":"numeric"}" autocomplete="off" enterkeyhint="next">`;(k==="origin"||k==="destination"||k==="dep_time"?$("apt"):g).appendChild(d);
  if(k==="dep_time"){const e=document.createElement("div");e.innerHTML='<label>Local time</label><input id="deploc" disabled>';$("apt").appendChild(e)}}
  $("mode").innerHTML=s.modes.map(m=>`<option value="${m}">${m[0].toUpperCase()+m.slice(1)}</option>`).join("");
  document.querySelectorAll("[data-k]").forEach(i=>i.oninput=()=>{save();localTime()});$("mode").onchange=save;document.querySelectorAll("#cfg input").forEach(i=>i.onkeydown=nextField);$("simbrief").oninput=save}
function localTime(){const el=$("deploc");if(!el)return;const v=(document.querySelector('[data-k="dep_time"]').value||"").trim();
  const m=v.match(/^(\d{1,2})[:.]?(\d{2})$/);if(!m||+m[1]>23||+m[2]>59){el.value="";return}
  const n=new Date(),d=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),+m[1],+m[2]));
  el.value=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",hour12:false})}
function nextField(e){if(e.key!=="Enter")return;e.preventDefault();
  const all=[...document.querySelectorAll("#cfg input:not([disabled])")].filter(i=>i.offsetParent);const n=all[all.indexOf(e.target)+1];
  if(n){n.focus();n.select&&n.select()}else{e.target.blur()}}
function fill(s){document.querySelectorAll("[data-k]").forEach(i=>i.value=s.fields[i.dataset.k]);$("simbrief").value=s.simbrief;$("mode").value=s.mode;localTime()}
function collect(){const o={simbrief:$("simbrief").value,mode:$("mode").value};document.querySelectorAll("[data-k]").forEach(i=>o[i.dataset.k]=i.value);return o}
function save(){clearTimeout(timer);timer=setTimeout(async()=>{try{const s=await api("/api/fields",collect());lastRev=s.rev}catch(e){}},400)}
function planHtml(p,open){if(!p||!(p.route||p.waypoints&&p.waypoints.length))return null;
  const box=document.createElement("div");box.className="plan";
  const head=[p.flight,p.aircraft,p.cruise_alt&&("FL"+Math.round(p.cruise_alt/100)),p.distance_nm&&(p.distance_nm+" nm"),p.ete,p.alternate&&("ALTN "+p.alternate)].filter(Boolean).join(" · ");
  const m=document.createElement("div");m.className="meta";m.textContent=head;box.appendChild(m);
  const sidstar=[p.sid&&("SID "+p.sid),p.star&&("STAR "+p.star)].filter(Boolean).join("   ");
  if(sidstar){const d=document.createElement("div");d.textContent=sidstar;d.style.fontWeight="600";box.appendChild(d)}
  if(p.route){const r=document.createElement("div");r.className="route";r.textContent=p.route;box.appendChild(r)}
  if(p.waypoints&&p.waypoints.length){const dt=document.createElement("details");dt.open=!!open;const sm=document.createElement("summary");sm.textContent="Waypoints ("+p.waypoints.length+")";dt.appendChild(sm);
    for(const w of p.waypoints){const row=document.createElement("div");row.className="wp";for(const v of [w.ident,w.via||"",w.alt!=null?w.alt+" ft":""]){const c=document.createElement("span");c.textContent=v;row.appendChild(c)}dt.appendChild(row)}box.appendChild(dt)}
  return box}
function plans(s){for(const [id,open] of [["plancfg",false],["planflt",false]]){const el=$(id);const keep=el.querySelector("details")?.open;const b=planHtml(s.plan,keep||open);el.innerHTML="";if(b)el.appendChild(b)}}
function chips(s){const f=s.fields,c=$("chips");c.innerHTML="";
  const add=(k,v)=>{if(v){const d=document.createElement("span");d.className="chip";d.innerHTML=`<b>${k}</b>`;d.append(v);c.appendChild(d)}};
  add("RWY",[f.origin,f.runway].filter(Boolean).join(" "));add("TA",f.ta&&f.ta+" ft");add("V1",f.v1);add("VR",f.vr);add("V2",f.v2);add("FLAP",f.flap);add("MODE",s.mode[0].toUpperCase()+s.mode.slice(1))}
function render(){const s=last;if(!s)return;const fly=s.phase==="flying";
  $("cfg").hidden=view!=="config";$("flt").hidden=view!=="flight";
  $("phase").textContent=fly?"✈ Flying — reading the simulator":"📝 Briefing";
  const a=$("aao");a.textContent=s.aao_ok===null?"AAO: waiting":s.aao_ok?"AAO: connected":"AAO: not responding";a.className="pill "+(s.aao_ok===null?"":s.aao_ok?"ok":"bad");
  $("go").textContent=fly?"Back to flight":"Briefing complete";$("go").className="big "+(fly?"fly":"");$("end").hidden=!fly;
  $("info").textContent=s.info;chips(s);plans(s);
  const l=s.log;if(l.length){const [t,...rest]=l[0].split("  ");$("nowtxt").textContent=rest.join("  ");$("nowtxt").className="";$("nowtime").textContent=t}
  else{$("nowtxt").textContent="Listening…";$("nowtxt").className="idle";$("nowtime").textContent=""}
  $("log").innerHTML="";for(const t of l.slice(1)){const li=document.createElement("li");li.textContent=t;$("log").appendChild(li)}}
function apply(s){const prev=last;last=s;
  if(!prev||prev.phase!==s.phase)view=s.phase==="flying"?"flight":"config";  // cambio di fase → vista di default
  render()}
async function poll(){try{const s=await api("/api/state");
  if(lastRev===-1){build(s);fill(s);lastRev=s.rev}else if(s.rev!==lastRev){fill(s);lastRev=s.rev}apply(s)}catch(e){$("phase").textContent="Connection lost…"}setTimeout(poll,1500)}
$("load").onclick=async()=>{$("load").disabled=true;$("info").textContent="Loading…";
  try{const s=await api("/api/load-plan",{simbrief:$("simbrief").value});fill(s);lastRev=s.rev;apply(s)}catch(e){$("info").textContent="SimBrief error: "+e.message}$("load").disabled=false};
$("go").onclick=async()=>{await api("/api/fields",collect());
  if(phase==="flying"){view="flight";render()}  // la lettura continua, si torna solo alla vista Volo
  else{const s=await api("/api/phase",{phase:"flying"});phase=s.phase;apply(s)}};
$("end").onclick=async()=>{const s=await api("/api/phase",{phase:"briefing"});phase=s.phase;apply(s)};
$("edit").onclick=()=>{view="config";render()};
const _apply=apply;apply=function(s){phase=s.phase;_apply(s)};
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
