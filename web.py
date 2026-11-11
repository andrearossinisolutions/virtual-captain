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
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
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
#flt{padding-bottom:calc(var(--sp-h,120px) + env(safe-area-inset-bottom) + 12px)}
#topbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:8px 0}#topbar .chips{margin:0;flex:1 1 auto}
#more{flex:0 0 auto;margin-left:auto}#more>summary{cursor:pointer;color:var(--mut);font-size:16px;padding:6px 12px;border:1px solid var(--bd);border-radius:10px}
#more[open]{flex:1 0 100%}#more[open]>summary{display:inline-block;margin-bottom:10px}
#more h2{margin-top:12px}#more .morebtns{display:flex;gap:8px;margin:12px 0}#more .morebtns button{flex:1}
#speech{position:fixed;bottom:0;left:0;right:0;z-index:10;background:#0b1118;border-top:1px solid var(--bd);padding:8px 16px calc(8px + env(safe-area-inset-bottom));cursor:pointer}
#speech.big{min-height:96px;display:flex;flex-direction:column;justify-content:center;text-align:center}
#sptxt{font-size:17px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#speech.big #sptxt{font-size:34px;font-weight:600;white-space:normal;line-height:1.25}
#speech.exp{max-height:40vh;overflow:auto}#speech.exp #sptxt{white-space:normal}
#sptxt.idle{color:var(--mut)}#sptime{color:var(--mut);font-size:13px}#speech:not(.big) #sptime{display:none}
@media (min-width:900px){
main.fv{max-width:none;padding:12px 16px}
.cl .cli{display:grid;grid-template-columns:1.8em minmax(0,1fr) auto;column-gap:10px;break-inside:avoid}
.cl .cli .tx{display:contents}.cl .cli .ic{grid-column:1}.cl .cli .e{grid-column:2}
.cl .cli .a{grid-column:3;text-align:right;max-width:16em}.cl .cli .d{grid-column:2/4;margin-top:0;font-size:14px}
#clbody{columns:420px 2;column-gap:24px}.cl h3{break-after:avoid;margin-top:12px}
}
.plan{background:#0c131b;border:1px solid var(--bd);border-radius:12px;padding:12px;margin-top:12px;font-size:16px}
.plan .route{font:15px/1.5 ui-monospace,monospace;word-break:break-word;margin:6px 0}.plan .meta{color:var(--mut);font-size:14px}
.plan details{margin-top:8px;color:var(--mut)}.plan summary{cursor:pointer}.wp{display:grid;grid-template-columns:5.5em 5.5em 1fr;gap:4px;font:14px ui-monospace,monospace;padding:2px 0}
.cs summary{cursor:pointer;color:var(--mut)}.cs h3{font-size:13px;color:var(--mut);margin:14px 0 6px;text-transform:uppercase;letter-spacing:.06em}
.cg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.ci{background:#0c131b;border:1px solid var(--bd);border-radius:10px;padding:8px 10px}
.ci .n{color:var(--mut);font-size:13px}.ci .v{font-size:19px;font-weight:600}.ci .v.na{color:#5d6d7d;font-weight:400}
.ci .x{color:#6b7c8d;font-size:12px}.ci .r{color:#6b7c8d;font:11px ui-monospace,monospace}
.clcard{padding:12px}.clbar{display:flex;gap:8px;align-items:stretch}
.clnav{flex:0 0 64px;font-size:34px;line-height:1;padding:10px 0}
.clstrip{flex:1;display:flex;gap:6px;overflow-x:auto;scroll-snap-type:x proximity;-webkit-overflow-scrolling:touch;padding:2px}
.clchip{flex:0 0 auto;scroll-snap-align:center;background:transparent;border:1px solid var(--bd);color:var(--mut);font-size:15px;font-weight:500;padding:10px 12px;white-space:nowrap}
.clchip.cur{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:700}
.clname{font-size:28px;font-weight:700;margin:12px 0 2px;line-height:1.2}.clsum{font-size:16px;color:var(--mut);margin-bottom:6px}.clsum.bad{color:var(--bad);font-weight:600}
.cl h3{font-size:13px;color:var(--mut);margin:16px 0 6px;text-transform:uppercase;letter-spacing:.06em}
.cli{display:flex;gap:10px;align-items:flex-start;padding:9px 8px;border-bottom:1px solid var(--bd);border-left:4px solid transparent}
.cli .ic{flex:0 0 1.6em;text-align:center;font-weight:700;color:var(--mut)}.cli .tx{flex:1;min-width:0}
.cli .e{font-size:18px;word-break:break-word}.cli .a{font-size:15px;color:var(--mut);word-break:break-word}.cli .d{font-size:15px;color:var(--bad);margin-top:2px}
.cli.st-ok{border-left-color:var(--ok)}.cli.st-ok .ic{color:var(--ok)}
.cli.st-bad{border-left-color:var(--bad);background:rgba(227,86,74,.08)}.cli.st-bad .ic{color:var(--bad)}
.cli.st-na{border-left-color:#5d6d7d}.cli.st-na .ic{color:#6b7c8d;font-size:12px;font-weight:500}
.cli.t-atc .e,.cli.t-announce .e,.cli.t-note .e{font-style:italic;color:var(--mut)}
.badge{display:inline-block;font-size:11px;font-style:normal;font-weight:700;letter-spacing:.05em;border:1px solid var(--bd);border-radius:6px;padding:1px 6px;margin-right:6px;color:var(--warn)}
#clnextbtn{margin-top:14px}
ul{list-style:none;margin:0;padding:0;font:15px/1.5 ui-monospace,monospace}li{padding:6px 0;border-bottom:1px solid var(--bd);color:var(--mut)}
</style></head><body><main id="main">
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
  <details class="card" id="tech"><summary>Technical settings</summary>
    <label for="aaourl">AAO address</label>
    <input id="aaourl" autocapitalize="off" autocorrect="off" placeholder="127.0.0.1:43380">
    <p style="margin:12px 0 0"><button class="ghost" id="aaotest" style="width:100%">Test connection</button></p>
    <p id="aaores" class="meta"></p>
  </details>
  <button class="big" id="go">Briefing complete</button>
  <p style="margin-top:12px"><button class="ghost" id="end" hidden style="width:100%">End flight (stop reading)</button></p>
</section>

<section id="flt" hidden>
  <div id="topbar"><div class="chips" id="chips"></div>
  <details id="more"><summary>More</summary>
    <details class="card cs" id="cs"><summary>Cockpit state</summary><div id="csbody"></div></details>
    <div id="planflt"></div>
    <div class="morebtns"><button class="ghost" id="edit">Edit briefing</button><button class="ghost" id="end2" hidden>End flight (stop reading)</button></div>
    <h2>Earlier</h2><div class="card"><ul id="log"></ul></div>
  </details></div>
  <div class="card clcard cl" id="clcard">
    <div class="clbar"><button class="clnav" id="clprev" aria-label="Previous phase">&lsaquo;</button><div class="clstrip" id="clstrip"></div><button class="clnav" id="clnext" aria-label="Next phase">&rsaquo;</button></div>
    <div class="clname" id="clname"></div><div class="clsum" id="clsum"></div>
    <div id="clbody"></div>
    <button class="big fly" id="clnextbtn">Next phase</button>
  </div>
  <div id="speech"><div id="sptxt" class="idle">Listening…</div><div id="sptime"></div></div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);let lastRev=-1,spN=null,spUntil=0,spExp=false,chipsKey="",csKey="",planKey="",clBodyKey="",timer=null,phase="briefing",view="config",last=null;
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!r.ok){const t=await r.text();throw new Error(t||r.status)}return r.json()}
function build(s){const g=$("grid");g.innerHTML="";$("apt").innerHTML="";for(const [k,l] of s.labels){const d=document.createElement("div");
  d.innerHTML=`<label>${l}</label><input data-k="${k}" inputmode="${["origin","destination","dep_time","runway","flap"].includes(k)?"text":"numeric"}" autocomplete="off" enterkeyhint="next">`;(k==="origin"||k==="destination"||k==="dep_time"?$("apt"):g).appendChild(d);
  if(k==="dep_time"){const e=document.createElement("div");e.innerHTML='<label>Local time</label><input id="deploc" disabled>';$("apt").appendChild(e)}}
  $("mode").innerHTML=s.modes.map(m=>`<option value="${m}">${m[0].toUpperCase()+m.slice(1)}</option>`).join("");
  document.querySelectorAll("[data-k]").forEach(i=>i.oninput=()=>{save();localTime()});$("mode").onchange=save;document.querySelectorAll("#cfg input").forEach(i=>i.onkeydown=nextField);$("simbrief").oninput=save;$("aaourl").oninput=save;$("aaotest").onclick=testAao}
function localTime(){const el=$("deploc");if(!el)return;const v=(document.querySelector('[data-k="dep_time"]').value||"").trim();
  const m=v.match(/^(\d{1,2})[:.]?(\d{2})$/);if(!m||+m[1]>23||+m[2]>59){el.value="";return}
  const n=new Date(),d=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),+m[1],+m[2]));
  el.value=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",hour12:false})}
function nextField(e){if(e.key!=="Enter")return;e.preventDefault();
  const all=[...document.querySelectorAll("#cfg input:not([disabled])")].filter(i=>i.offsetParent);const n=all[all.indexOf(e.target)+1];
  if(n){n.focus();n.select&&n.select()}else{e.target.blur()}}
async function testAao(){const r=$("aaores");r.textContent="Testing…";
  try{const x=await api("/api/aao-test",{url:$("aaourl").value});
    r.textContent=x.ok?`Connected in ${x.ms} ms (${x.url}). Read: `+Object.entries(x.values).map(([k,v])=>k+" "+Math.round(v)).join(", "):`Not responding after ${x.ms} ms (${x.url}): ${x.error}`;
    if(x.ok)$("aaourl").value=x.url}catch(e){r.textContent="Test failed: "+e.message}}
function fill(s){document.querySelectorAll("[data-k]").forEach(i=>i.value=s.fields[i.dataset.k]);$("simbrief").value=s.simbrief;if(document.activeElement!==$("aaourl"))$("aaourl").value=s.aao_url||"";$("mode").value=s.mode;localTime()}
function collect(){const o={simbrief:$("simbrief").value,aao_url:$("aaourl").value,mode:$("mode").value};document.querySelectorAll("[data-k]").forEach(i=>o[i.dataset.k]=i.value);return o}
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
function plans(s){const k=JSON.stringify(s.plan);if(k===planKey)return;planKey=k;for(const [id,open] of [["plancfg",false],["planflt",false]]){const el=$(id);const keep=el.querySelector("details")?.open;const b=planHtml(s.plan,keep||open);el.innerHTML="";if(b)el.appendChild(b)}}
function chips(s){const f=s.fields,c=$("chips"),k=JSON.stringify([f,s.mode]);if(k===chipsKey)return;chipsKey=k;c.innerHTML="";
  const add=(k,v)=>{if(v){const d=document.createElement("span");d.className="chip";d.innerHTML=`<b>${k}</b>`;d.append(v);c.appendChild(d)}};
  add("RWY",[f.origin,f.runway].filter(Boolean).join(" "));add("TA",f.ta&&f.ta+" ft");add("V1",f.v1);add("VR",f.vr);add("V2",f.v2);add("FLAP",f.flap);add("MODE",s.mode[0].toUpperCase()+s.mode.slice(1))}
function cockpit(s){const k=JSON.stringify(s.monitor);if(k===csKey)return;csKey=k;const b=$("csbody");b.textContent="";const m=s.monitor||[];
  if(!m.length){const p=document.createElement("div");p.className="meta";p.textContent="No data yet";b.appendChild(p);return}
  const mk=(c,t)=>{const e=document.createElement("div");e.className=c;e.textContent=t;return e};
  for(const [g,rows] of m){const h=document.createElement("h3");h.textContent=g;b.appendChild(h);
    const grid=document.createElement("div");grid.className="cg";
    for(const [n,v,x,r] of rows){const c=document.createElement("div");c.className="ci";
      c.appendChild(mk("n",n));c.appendChild(mk("v"+(v.startsWith("n/d")?" na":""),v));
      if(x)c.appendChild(mk("x",x));if(r!=="")c.appendChild(mk("r","raw "+r));grid.appendChild(c)}
    b.appendChild(grid)}}
let clKey="";
function clGo(body){api("/api/checklist-phase",body).then(apply).catch(()=>{})}
function checklist(s){const ph=s.cl_phases||[],cur=s.cl_phase,c=s.checklist;if(!c)return;
  const idx=ph.findIndex(p=>p.id===cur),strip=$("clstrip"),key=ph.length+"|"+cur;
  if(key!==clKey){clKey=key;strip.textContent="";
    for(const p of ph){const b=document.createElement("button");b.className="clchip"+(p.id===cur?" cur":"");b.textContent=p.name;b.onclick=()=>clGo({phase:p.id});strip.appendChild(b);
      if(p.id===cur)setTimeout(()=>b.scrollIntoView({inline:"center",block:"nearest"}),0)}}
  $("clprev").onclick=()=>clGo({step:-1});$("clnext").onclick=()=>clGo({step:1});
  $("clprev").disabled=idx<=0;$("clnext").disabled=idx>=ph.length-1;
  $("clname").textContent=c.phase.name;const sm=$("clsum");sm.textContent=c.bad+" out of spec";sm.className="clsum"+(c.bad?" bad":"");
  const body=$("clbody"),bk=JSON.stringify([cur,c.phase.name,c.bad,c.subphases.map(sp=>[sp.name,sp.items.map(i=>[i.text,i.status,i.kind,i.detail,i.response,i.type])])]);
  if(bk!==clBodyKey){clBodyKey=bk;const y=window.scrollY,x=window.scrollX;body.textContent="";
  const mk=(t,cls,txt)=>{const e=document.createElement(t);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e};
  for(const sp of c.subphases){if(sp.name)body.appendChild(mk("h3","",sp.name));
    for(const it of sp.items){const r=mk("div","cli st-"+it.status+" t-"+it.type);
      const ic=it.status==="ok"?"\u2713":it.status==="bad"?"\u2717":it.status==="na"?(it.kind==="unverified"?"unverified":"n/d"):"\u00b7";
      r.appendChild(mk("div","ic",ic));const tx=mk("div","tx");const e=mk("div","e");
      if(it.type==="atc")e.appendChild(mk("span","badge","ATC"));if(it.type==="announce")e.appendChild(mk("span","badge","ANNOUNCE"));
      e.append(it.text);tx.appendChild(e);
      if(it.response&&it.type!=="announce")tx.appendChild(mk("div","a",it.response));
      if(it.kind==="mismatch")tx.appendChild(mk("div","d",it.detail.replace(/, actual/g," \u00b7 actual")));
      r.appendChild(tx);body.appendChild(r)}}
  window.scrollTo(x,y)}
  const nb=$("clnextbtn"),nx=ph[idx+1];nb.hidden=!nx;if(nx){nb.textContent="Next phase: "+nx.name;nb.onclick=()=>{clGo({step:1});$("clcard").scrollIntoView({block:"start"})}}}
function speech(s){const sp=$("speech"),tx=$("sptxt"),l=s.log,ago=s.spoke_ago==null?1e9:s.spoke_ago;
  if(s.spoke_n!==spN){if(spN!==null)spExp=false;spN=s.spoke_n;spUntil=Date.now()+Math.max(0,10-ago)*1000}
  const big=Date.now()<spUntil&&l.length>0;sp.className=(big?"big":"")+(spExp&&!big?" exp":"");
  if(l.length){const [t,...rest]=l[0].split("  ");tx.textContent=big?rest.join("  "):t+"  "+rest.join("  ");tx.className="";$("sptime").textContent=t}
  else{tx.textContent="Listening…";tx.className="idle";$("sptime").textContent=""}}
function render(){const s=last;if(!s)return;const fly=s.phase==="flying";
  $("main").classList.toggle("fv",view==="flight");$("cfg").hidden=view!=="config";$("flt").hidden=view!=="flight";
  $("phase").textContent=fly?"✈ Flying — reading the simulator":"📝 Briefing";
  const a=$("aao");a.textContent=s.aao_ok===null?"AAO: waiting":s.aao_ok?"AAO: connected":"AAO: not responding";a.className="pill "+(s.aao_ok===null?"":s.aao_ok?"ok":"bad");
  $("go").textContent=fly?"Back to flight":"Briefing complete";$("go").className="big "+(fly?"fly":"");$("end").hidden=!fly;$("end2").hidden=!fly;
  $("info").textContent=s.info;chips(s);checklist(s);cockpit(s);plans(s);
  speech(s);
  const l=s.log;
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
$("end").onclick=$("end2").onclick=async()=>{const s=await api("/api/phase",{phase:"briefing"});phase=s.phase;apply(s)};
$("speech").onclick=()=>{spExp=!spExp;if(last)speech(last)};
if(window.ResizeObserver)new ResizeObserver(()=>document.documentElement.style.setProperty("--sp-h",$("speech").offsetHeight+"px")).observe($("speech"));

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
                elif self.path == "/api/aao-test":
                    return self._json(st.test_aao(str(data.get("url", "")).strip() or None))
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
