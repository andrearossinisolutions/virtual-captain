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
:root{--bg:#0f1720;--card:#18232f;--fg:#e8eef4;--mut:#8da1b5;--acc:#3aa0ff;--ok:#38c172;--bad:#e3564a;--bd:#2b3a49;--warn:#d9a441;--opt:#5bc0eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.4 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}[hidden]{display:none!important}
h1{font-size:20px;margin:0;white-space:nowrap}h2{font-size:16px;color:var(--mut);margin:20px 0 8px;text-transform:uppercase;letter-spacing:.06em}
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
.chip.sqk{cursor:pointer}.chip input{width:5.5em;font-size:16px;padding:2px 6px;border-radius:6px;border:1px solid var(--bd);background:var(--card);color:var(--fg)}
.chip b{color:var(--mut);font-weight:500;font-size:13px;margin-right:6px}
#flt{padding-bottom:calc(var(--sp-h,120px) + env(safe-area-inset-bottom) + 12px)}
#hdr{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 8px}#hdr .pill{margin:0}#hdr .chips{margin:0}
#more{flex:0 0 auto;margin-left:auto}#more>summary{cursor:pointer;color:var(--mut);font-size:16px;padding:6px 12px;border:1px solid var(--bd);border-radius:10px}
#more[open]{flex:1 0 100%}#more[open]>summary{display:inline-block;margin-bottom:10px}
.livesw{display:block;margin:8px 0;color:var(--fg)}.livesw input{width:auto;margin-right:8px}.lv{font-size:14px;color:var(--acc)}
#more h2{margin-top:12px}#more .morebtns{display:flex;gap:8px;margin:12px 0}#more .morebtns button{flex:1}
#speech{position:fixed;bottom:0;left:0;right:0;z-index:10;background:#0b1118;border-top:1px solid var(--bd);padding:8px 16px calc(8px + env(safe-area-inset-bottom));cursor:pointer}
#speech.big{min-height:96px;display:flex;flex-direction:column;justify-content:center;text-align:center}
#sptxt{font-size:17px;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#speech.big #sptxt{font-size:34px;font-weight:600;white-space:normal;line-height:1.25}
#speech.exp{max-height:40vh;overflow:auto}#speech.exp #sptxt{white-space:normal}
#sptxt.idle{color:var(--mut)}#sptime{color:var(--mut);font-size:13px}#speech:not(.big) #sptime{display:none}
@media (min-width:900px){
main.fv{max-width:none;padding:12px 16px}
.cl .cli{display:grid;grid-template-columns:1.8em minmax(0,1fr) auto;column-gap:10px;break-inside:avoid}.cl .cli.wl{grid-template-columns:5.2em minmax(0,1fr) auto}.cl .cli .rw{grid-column:2}.cl .cli .now.nv{color:var(--mut)}.cl .cli .now{grid-column:3;text-align:right;font-size:14px;color:var(--bad);white-space:nowrap}
.cl .cli .exp{grid-column:2/4}
.cl .cli .tx{display:contents}.cl .cli .ic{grid-column:1}.cl .cli .e{grid-column:2}
.cl .cli .a{grid-column:3;text-align:right;max-width:16em}.chip.bad{border-color:var(--bad);color:var(--bad)}.rb{color:var(--bad)!important}.cl .cli .ah .aw{color:var(--warn);font-weight:600;margin-left:8px}.cl .cli .a.ah{font-weight:600}.cl .cli .a.wh{font-weight:400}.cl .cli .wh .wr{font:12px ui-monospace,monospace;color:var(--mut);word-break:break-word}.cl .cli .wh.nd{color:var(--mut)}.cl .cli .a.lg{grid-column:2/4;text-align:left;max-width:none}.cl .cli .d{grid-column:2/4;margin-top:0;font-size:14px}
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
.cli .ic{flex:0 0 1.6em;min-width:1.6em;text-align:center;font-weight:700;color:var(--mut)}.cli .tx{flex:1;min-width:0}
.cli .e{font-size:18px;word-break:break-word}.cli .a{font-size:15px;color:var(--mut);word-break:break-word}.cli .d{font-size:15px;color:var(--bad);margin-top:2px}
.cli.st-ok{border-left-color:var(--ok)}.cli.st-ok .ic{color:var(--ok)}
.cli.st-bad{border-left-color:var(--bad);background:rgba(227,86,74,.08)}.cli.st-bad .ic{color:var(--bad)}
.cli.st-opt{border-left-color:var(--opt)}.cli.st-opt .ic{color:var(--opt)}
.cli.st-wait{border-left-color:#5d6d7d}.cli.st-wait .ic{color:var(--mut);opacity:.7}.cli.st-na{border-left-color:#5d6d7d}.cli.st-na .ic{color:#6b7c8d;font-size:12px;font-weight:500}.cli .ic.w{flex:0 0 5.2em;min-width:5.2em;white-space:nowrap;text-align:left}.cli .rw{font:12px ui-monospace,monospace;color:var(--mut);word-break:break-all}
.cli.t-atc .e,.cli.t-announce .e,.cli.t-note .e{font-style:italic;color:var(--mut)}
.badge{display:inline-block;font-size:11px;font-style:normal;font-weight:700;letter-spacing:.05em;border:1px solid var(--bd);border-radius:6px;padding:1px 6px;margin-right:6px;color:var(--warn)}
.cli .a.set{color:var(--fg);font-weight:600}
.craft{background:#0c131b;border:1px solid var(--bd);border-radius:10px;padding:8px;margin:8px 0;break-inside:avoid}
.craft .ch{font-size:12px;font-weight:700;letter-spacing:.06em;color:var(--warn);margin-bottom:4px}
.craft .cf{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px 6px}.craft .cf .w{grid-column:1/-1}.craft .cf .c2{grid-column:span 2}@media(max-width:420px){.craft input{padding:5px 6px}}  /* CRAFT: Cleared to e Route a tutta larghezza, poi A, F, T su tre colonne */
.craft.tbl .cf{grid-template-columns:.5fr 1.7fr 1.2fr 1.7fr;gap:4px}.craft.tbl .cf.c5 .tc:nth-child(5){grid-column:1/3}@media(max-width:420px){.craft.tbl .tc .fld{padding:5px 4px;font-size:15px}.craft.tbl .tr{gap:2px}.craft.tbl .plus{min-width:30px}.craft.tbl .plus.un{min-width:34px;font-size:11px}}.craft.tbl .tr{display:flex;align-items:center;gap:4px}.craft.tbl .tr .fld{flex:1 1 0;min-width:0}.craft.tbl .plus{flex:0 0 auto;min-width:34px;height:28px;margin:0;padding:0;font-size:20px;line-height:1;font-weight:600;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg);cursor:pointer;touch-action:manipulation}.craft.tbl .plus.un{font-size:12px;min-width:40px}.craft.tbl .tc .fld{display:block;cursor:pointer;min-height:1.6em;padding:5px 8px;font-size:16px;border-radius:8px;border:1px solid var(--bd);background:var(--card);overflow:hidden;white-space:nowrap}.craft.tbl .tc .fld.nv{color:var(--mut)}.craft.tbl .tc .fld.set{color:var(--fg);font-weight:600}.craft.tbl .tc .fld input{padding:0;border:0;background:transparent}
.craft label{display:block;font-size:11px;color:var(--mut);line-height:1.2}
.craft input,.craft textarea{width:100%;padding:5px 8px;font-size:16px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);scroll-margin-bottom:calc(var(--sp-h,120px) + 24px)}
.craft textarea{font:16px/1.3 ui-monospace,monospace;text-transform:uppercase;min-height:3.6em;resize:vertical}
.a.inpr .fld{cursor:pointer;border-bottom:1px dotted var(--mut);white-space:nowrap}.a.inpr .fld.set{color:var(--fg);font-weight:600}.a.inpr .sep{color:var(--mut)}
.a.inpr input{width:4.5em;font-size:15px;padding:1px 6px;border-radius:6px;border:1px solid var(--bd);background:var(--card);color:var(--fg);scroll-margin-bottom:calc(var(--sp-h,120px) + 24px)}.a.inpr .fld.w input{width:6em}
.a.inpr .rs{background:none;border:0;color:var(--mut);padding:0 4px;font-size:16px;font-weight:400;cursor:pointer}
.craft .cb{margin-top:6px}.craft .cb button{padding:4px 10px;font-size:13px}
#clnextbtn{margin-top:14px}
#thrtab{border-collapse:collapse;font:13px ui-monospace,monospace;width:100%}#thrtab th,#thrtab td{border-bottom:1px solid var(--bd);padding:3px 6px;text-align:right;white-space:nowrap}#thrtab th:first-child,#thrtab td:first-child{text-align:left}#thrtab .lv{font-weight:700}#thrtab .gd td:first-child{color:var(--fg)}
ul{list-style:none;margin:0;padding:0;font:15px/1.5 ui-monospace,monospace}li{padding:6px 0;border-bottom:1px solid var(--bd);color:var(--mut)}
</style></head><body><main id="main">
<header id="hdr"><h1>Virtual Captain</h1><span class="pill" id="aao"></span>
  <div class="chips" id="chips" hidden></div><span class="chip" id="sqk" hidden></span><div class="chips" id="fpills" hidden></div>
  <details id="more" hidden><summary>More</summary>
    <label class="livesw"><input type="checkbox" id="livesw"> Show real values on checklist</label>
    <details class="card cs" id="fd"><summary>Flight data</summary><div id="fdbody"></div></details>
    <details class="card cs" id="cs"><summary>Cockpit state</summary><div id="csbody"></div></details>
    <div id="planflt"></div>
    <div class="morebtns"><button class="ghost" id="edit">Edit briefing</button><button class="ghost" id="end2" hidden>End flight (stop reading)</button></div>
    <h2>Earlier</h2><div class="card"><ul id="log"></ul></div>
  </details>
</header>

<section id="cfg">
  <h2>Briefing</h2>
  <div class="card">
    <div class="row"><div style="flex:2"><label for="simbrief">SimBrief ID or username</label><input id="simbrief" enterkeyhint="next" autocapitalize="off" autocorrect="off"></div>
    <div style="flex:1"><label for="mode">Mode</label><select id="mode"></select></div>
    <div style="flex:1"><label for="clmode">Checklists</label><select id="clmode"></select></div></div>
    <p style="margin:12px 0 0"><button id="load" style="width:100%">Load latest plan</button></p>
    <p id="info"></p>
    <div class="grid" id="apt"></div>
    <div id="plancfg"></div>
    <div class="grid" id="grid"></div>
  </div>
  <details class="card" id="tech"><summary>Technical settings</summary>
    <div id="thrcal">
      <label>Thrust levers calibration</label>
      <p id="thrst" class="meta" style="margin:0 0 8px"></p>
      <div class="row"><button class="ghost" id="thrstart">Calibrate</button><button class="ghost" id="thrclear" hidden>Clear calibration</button></div>
      <div class="card" id="thrcard" hidden>
        <div class="clname" id="thrtitle" style="font-size:22px;margin-top:0"></div>
        <p id="thrtext" style="margin:6px 0"></p>
        <p class="meta" id="thrwarn" style="margin:0 0 8px">Hold the levers still for a moment before pressing Next.</p>
        <div style="overflow-x:auto"><table id="thrtab"></table></div>
        <p id="threrr" class="bad" style="margin:8px 0 0"></p>
        <div class="row" style="margin-top:10px" id="thrbtns"><button class="ghost" id="thrback">Back</button><button id="thrnext">Next</button><button class="ghost" id="thrskip">Skip</button><button class="ghost" id="thrcancel">Cancel</button></div>
        <div id="thrsum" hidden><p id="thrsumtxt"></p><button id="thrclose" style="width:100%">Close</button></div>
      </div>
      <p id="thrmsg" class="bad" style="margin:8px 0"></p>
    </div>
    <label for="aaourl">AAO address</label>
    <input id="aaourl" autocapitalize="off" autocorrect="off" placeholder="127.0.0.1:43380">
    <p style="margin:12px 0 0"><button class="ghost" id="aaotest" style="width:100%">Test connection</button></p>
    <p id="aaores" class="meta"></p>
  </details>
  <button class="big" id="go">Briefing complete</button>
  <p style="margin-top:12px"><button class="ghost" id="end" hidden style="width:100%">End flight (stop reading)</button></p>
</section>

<section id="flt" hidden>
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
const $=id=>document.getElementById(id);let lastRev=-1,spN=null,spUntil=0,spExp=false,chipsKey="",csKey="",fdKey="",planKey="",clBodyKey="",timer=null,phase="briefing",view="config",shownView="config",last=null;
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!r.ok){const t=await r.text();throw new Error(t||r.status)}return r.json()}
function build(s){const g=$("grid");g.innerHTML="";$("apt").innerHTML="";for(const [k,l] of s.labels){const d=document.createElement("div");
  d.innerHTML=`<label>${l}</label><input data-k="${k}" inputmode="${["origin","destination","dep_time"].includes(k)?"text":"numeric"}" autocomplete="off" enterkeyhint="next"${k==="origin"?' placeholder="LIMC/17R" autocapitalize="characters"':""}>`;(k==="origin"||k==="destination"||k==="dep_time"?$("apt"):g).appendChild(d);
  if(k==="dep_time"){const e=document.createElement("div");e.innerHTML='<label>Local time</label><input id="deploc" disabled>';$("apt").appendChild(e)}}
  $("mode").innerHTML=s.modes.map(m=>`<option value="${m}">${m[0].toUpperCase()+m.slice(1)}</option>`).join("");
  $("clmode").innerHTML=s.cl_modes.map(m=>`<option value="${m}">${m[0].toUpperCase()+m.slice(1)}</option>`).join("");
  document.querySelectorAll("[data-k]").forEach(i=>i.oninput=()=>{save();localTime()});$("mode").onchange=save;$("clmode").onchange=save;document.querySelectorAll("#cfg input").forEach(i=>i.onkeydown=nextField);$("simbrief").oninput=save;$("aaourl").oninput=save;$("aaotest").onclick=testAao;
  for(const a of ["start","next","back","skip","cancel"])$("thr"+a).onclick=()=>thrAct(a);$("thrclose").onclick=()=>thrAct("cancel");
  $("thrclear").onclick=()=>{if(confirm("Clear the thrust levers calibration?"))thrAct("clear")}}
let calKey="";
async function thrAct(action){try{await api("/api/thrust-cal",{action});$("thrmsg").textContent="";await refresh()}catch(e){$("thrmsg").textContent=e.message;try{await refresh()}catch(x){}}}
async function refresh(){const s=await api("/api/state");apply(s)}
function calib(s){const c=s.calib,t=s.thr_cal,k=JSON.stringify([c,t]);if(k===calKey)return;calKey=k;
  $("thrst").textContent=t?`Calibrated ${t.when}, guide L: ${t.guides.l}, R: ${t.guides.r}`:"Not calibrated";
  $("thrstart").hidden=!!(c&&c.active);$("thrclear").hidden=!t;$("thrcard").hidden=!(c&&c.active);
  if(!(c&&c.active))return;
  $("thrtitle").textContent=c.done?"Calibration complete":`Step ${c.step+1}/${c.total}: ${c.name}`;
  $("thrtext").textContent=c.done?"":c.text;$("thrwarn").hidden=c.done;$("threrr").textContent=c.error||(c.has_live||c.done?"":"No live data from AAO (is the simulator connected?)");
  $("thrbtns").hidden=c.done;$("thrsum").hidden=!c.done;
  $("thrback").disabled=c.step===0;$("thrskip").hidden=!c.can_skip;
  if(c.done&&t)$("thrsumtxt").textContent=`Saved ${t.when}. Guide L: ${t.guides.l}; R: ${t.guides.r}.`;
  const tb=$("thrtab");tb.textContent="";const mk=(tag,txt,cls)=>{const e=document.createElement(tag);e.textContent=txt;if(cls)e.className=cls;return e};
  const h=document.createElement("tr");h.appendChild(mk("th","Variable"));h.appendChild(mk("th","Live"));for(const x of c.heads)h.appendChild(mk("th",x));tb.appendChild(h);
  for(const r of c.rows){const tr=document.createElement("tr");if(r.guide)tr.className="gd";tr.appendChild(mk("td",r.name));tr.appendChild(mk("td",r.live===""?"n/d":r.live,"lv"));for(const v of r.vals)tr.appendChild(mk("td",v));tb.appendChild(tr)}}
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
function fill(s){document.querySelectorAll("[data-k]").forEach(i=>i.value=s.fields[i.dataset.k]);$("simbrief").value=s.simbrief;if(document.activeElement!==$("aaourl"))$("aaourl").value=s.aao_url||"";$("mode").value=s.mode;$("clmode").value=s.checklist_mode;localTime()}
function collect(){const o={simbrief:$("simbrief").value,aao_url:$("aaourl").value,mode:$("mode").value,checklist_mode:$("clmode").value};document.querySelectorAll("[data-k]").forEach(i=>o[i.dataset.k]=i.value);return o}
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
function chips(s){const f=s.fields,c=$("chips"),rb=s.rwy_bad||{},k=JSON.stringify([f,rb]);if(k===chipsKey)return;chipsKey=k;c.innerHTML="";
  const add=(k,v,w)=>{if(v){const d=document.createElement("span");d.className="chip"+(w?" bad":"");d.innerHTML=`<b>${k}</b>`;d.append(v);c.appendChild(d)}};
  add("DEP",f.origin);const dr=(f.datis_rwy||"").replace(/\//g,"-");add("DEP ATIS",(f.datis_letter||"")+(f.datis_letter&&dr?" ":"")+(dr?"RWY "+dr:""),rb.datis);  // pista ATIS diversa da quella del piano: pill rossa (solo avviso)
  const rw=(f.atis_rwy||"").replace(/\//g,"-");add("ARR",f.destination?f.destination+(rw?"/"+rw:""):rw,rb.atis);  // ARR EDDF/17R-17L; senza destinazione solo le piste
  add("ARR ATIS",f.atis_letter)}
function cockpit(s){const k=JSON.stringify(s.monitor);if(k===csKey)return;csKey=k;const b=$("csbody");b.textContent="";const m=s.monitor||[];
  if(!m.length){const p=document.createElement("div");p.className="meta";p.textContent="No data yet";b.appendChild(p);return}
  const mk=(c,t)=>{const e=document.createElement("div");e.className=c;e.textContent=t;return e};
  for(const [g,rows] of m){const h=document.createElement("h3");h.textContent=g;b.appendChild(h);
    const grid=document.createElement("div");grid.className="cg";
    for(const [n,v,x,r] of rows){const c=document.createElement("div");c.className="ci";
      c.appendChild(mk("n",n));c.appendChild(mk("v"+(v.startsWith("n/d")?" na":""),v));
      if(x)c.appendChild(mk("x",x));if(r!=="")c.appendChild(mk("r","raw "+r));grid.appendChild(c)}
    b.appendChild(grid)}}
function fdata(s){const k=JSON.stringify([s.flight,s.aao_ok]);if(k===fdKey)return;fdKey=k;const b=$("fdbody");b.textContent="";const f=s.flight||[];
  const mk=(c,t)=>{const e=document.createElement("div");e.className=c;e.textContent=t;return e};
  if(!f.length||s.aao_ok===false){b.appendChild(mk("meta",s.aao_ok===false?"AAO unavailable":"No data yet"));return}
  const grid=document.createElement("div");grid.className="cg";
  for(const [n,v] of f){const c=document.createElement("div");c.className="ci";c.appendChild(mk("n",n));c.appendChild(mk("v"+(v.startsWith("\u2014")?" na":""),v));grid.appendChild(c)}
  b.appendChild(grid)}
function fpills(s){const e=$("fpills"),defs=s.pill_defs||[],p=s.pills||{},k=JSON.stringify(defs);
  if(k!==e.dataset.k){e.dataset.k=k;e.textContent="";for(const [key,l] of defs){const c=document.createElement("span");c.className="chip";c.id="pill-"+key;const b=document.createElement("b");b.textContent=l;c.appendChild(b);const v=document.createElement("span");c.appendChild(v);e.appendChild(c)}}
  for(const [key] of defs){const v=$("pill-"+key).lastChild;const t=p[key]||"\u2014";if(v.textContent!==t)v.textContent=t}}
let clKey="",showLive=localStorage.getItem("showLive")==="1";
$("livesw").checked=showLive;$("livesw").onchange=()=>{showLive=$("livesw").checked;localStorage.setItem("showLive",showLive?"1":"0");if(last)checklist(last)};
let craftEl=null,clrTimer=null,sqkEdit=false,sqkVal="";
function sqkShow(){const e=$("sqk");if(sqkEdit)return;e.className="chip sqk";e.innerHTML="<b>SQK</b>";e.append(sqkVal||"\u2014")}
function sqkStart(){if(sqkEdit)return;sqkEdit=true;const e=$("sqk"),i=document.createElement("input");i.inputMode="numeric";i.maxLength=4;i.pattern="[0-7]*";i.value=sqkVal;
  i.setAttribute("autocomplete","off");i.spellcheck=false;let done=false;
  const end=save=>{if(done)return;done=true;sqkEdit=false;const v=i.value;sqkShow();
    if(save&&v!==sqkVal){sqkVal=v;sqkShow();clearTimeout(clrTimer);clrTimer=null;api("/api/clearance",{t:v}).then(s=>{if(s&&s.clearance)syncCraft(s.clearance,true)}).catch(()=>{})}};
  i.oninput=()=>{const v=i.value.replace(/[^0-7]/g,"");if(v!==i.value)i.value=v};
  i.onkeydown=ev=>{if(ev.key==="Enter"){ev.preventDefault();end(true)}else if(ev.key==="Escape"){ev.preventDefault();end(false)}};
  i.onblur=()=>end(true);e.textContent="";const b=document.createElement("b");b.textContent="SQK";e.append(b,i);i.focus();i.select()}
$("sqk").onclick=sqkStart;

function clrCollect(){const o={};craftEl.querySelectorAll("[data-c]").forEach(i=>o[i.dataset.c]=i.value);return o}
function clrSend(){clearTimeout(clrTimer);clrTimer=null;if(craftEl)api("/api/clearance",clrCollect()).catch(()=>{})}
function craftNode(it){if(craftEl)return craftEl;
  const el=document.createElement("div");el.className="craft";const h=document.createElement("div");h.className="ch";h.textContent=it.text;el.appendChild(h);
  const g=document.createElement("div");g.className="cf";
  for(const f of it.fields){const d=document.createElement("div");if(f.k==="r")d.className="w";else if(f.k==="c")d.className="c2";
    const lb=document.createElement("label");lb.textContent=f.label;const i=document.createElement(f.k==="r"?"textarea":"input");i.dataset.c=f.k;
    i.setAttribute("autocomplete","off");i.setAttribute("autocapitalize",f.k==="t"?"off":"characters");i.setAttribute("autocorrect","off");i.spellcheck=false;
    if(f.k==="t"){i.inputMode="numeric";i.maxLength=4;i.pattern="[0-7]*"}
    i.oninput=()=>{if(f.k==="t"){const v=i.value.replace(/[^0-7]/g,"");if(v!==i.value)i.value=v}if(f.k==="f"&&i.value.length===7){const n=craftEl.querySelector('[data-c="t"]');if(n)n.focus()}clearTimeout(clrTimer);clrTimer=setTimeout(clrSend,400)};
    i.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();i.blur()}};
    d.appendChild(lb);d.appendChild(i);g.appendChild(d)}
  el.appendChild(g);const b=document.createElement("div");b.className="cb";const rb=document.createElement("button");rb.className="ghost";rb.textContent="Reset to plan";
  rb.onclick=async()=>{clearTimeout(clrTimer);clrTimer=null;try{const s=await api("/api/clearance",{reset:true});syncCraft(s.clearance,true)}catch(e){}};
  b.appendChild(rb);el.appendChild(b);craftEl=el;return el}
function syncCraft(c,force){if(c&&!sqkEdit){sqkVal=c.t||"";sqkShow()}if(!craftEl||!c||(clrTimer&&!force))return;
  craftEl.querySelectorAll("[data-c]").forEach(i=>{const v=c[i.dataset.c]||"";if(i.value!==v&&(force||document.activeElement!==i))i.value=v})}
let inpDirty={},inpTimer=null;
function inpSend(){clearTimeout(inpTimer);inpTimer=null;const o=inpDirty;inpDirty={};if(last)Object.assign(last.fields,o);if(Object.keys(o).length)api("/api/fields",o).then(s=>{lastRev=s.rev}).catch(()=>{})}
function fval(k){return k in inpDirty?inpDirty[k]:((last&&last.fields[k])||"")}
function fShow(el){if(el.querySelector("input"))return;const v=fval(el.dataset.f);el.textContent="";el.className="fld"+(v?" set":" nv")+(el.dataset.w?" w":"")+(/^d?atis_rwy$/.test(el.dataset.f)&&last&&last.rwy_bad&&last.rwy_bad[el.dataset.f.replace("_rwy","")]?" rb":"");
  el.textContent=el.dataset.pf?el.dataset.pf+" "+(v||"---"):v||(el.dataset.r&&!el.dataset.pre?el.dataset.r:(el.dataset.pre?el.dataset.pre+" ":"")+"---")}
// ATIS-PURE-BEGIN (regole pure: provate anche con node nei test)
const ATIS_FULL={letter:/^[A-Z]$/,rwy:/(^|\/)\d{2}[LRC]$/,wind:/^(\d{3}|VRB)\/\d{2}$/,qnh:/^(\d{4}|\d{2}\.\d{2})$/,tl:/^\d{3}$/};  // valore completo: passa al campo successivo (arrivo: TL ultimo, chiude; partenza: QNH ultimo)
// sanifica un campo ATIS mentre si scrive; typed: digitazione vera (non incolla); inMode: QNH in inHg (3 cifre -> "29.9")
function atisClean(sf,v,typed,inMode){let u;
  if(sf==="letter")return v.toUpperCase().replace(/[^A-Z]/g,"").slice(0,1);
  if(sf==="rwy")return v.toUpperCase().replace(/[\s,]+/g,"/").replace(/[^0-9LRC\/]/g,"").replace(/\/+/g,"/").replace(/^\//,"").slice(0,12);
  if(sf==="tl"){const fl=/^\s*FL/i.test(v),d=v.toUpperCase().replace(/^\s*FL/,"").replace(/\D/g,"").slice(0,3);return fl&&!typed&&d?d.padStart(3,"0"):d}  // "FL70" incollato -> "070"; digitando niente riempimento (si completa alla chiusura del campo)  // transition level: "FL70" -> "70"; solo cifre, max 3
  if(sf==="wind"){u=v.toUpperCase().replace(/[^0-9\/GVRBKT]/g,"").replace(/\/+/g,"/").slice(0,12);u=u.replace(/^(\d{3}|VRB)(\d)/,"$1/$2");  // direzione + cifre senza "/": lo rimette
    if(typed&&/^(\d{3}|VRB)$/.test(u))u+="/";return u}  // direzione completa digitata: "/" automatico (non su incolla)
  u=v.replace(/[^\d.]/g,"").replace(/^([^.]*\.?)(.*)$/,(m,a,b)=>a+b.replace(/\./g,""));
  if(inMode&&/^\d{3,}$/.test(u))u=u.slice(0,2)+"."+u.slice(2);
  return u.slice(0,5)}
// QNH hPa <-> inHg: esatta, 33.8639 hPa per inHg in entrambi i versi (1013 hPa -> 29.91 inHg, come state.atis_qnh_hpa); valore non numerico: invariato
function qnhConvert(v){const x=parseFloat(v);if(!isFinite(x))return v;return String(v).includes(".")?String(Math.round(x*33.8639)):(x/33.8639).toFixed(2)}
const qu={};function qUnit(k,v){return v?(v.includes(".")?"In":"hPa"):(qu[k]||"hPa")}  // unita' del QNH: dal valore ("." = In); campo vuoto: ultima scelta (in memoria)
function qnhInMode(k,v){return v.includes(".")||qu[k]==="In"}  // in digitazione: inHg se c'e' il punto o se l'unita' scelta e' In (campo vuoto: il "." scatta dopo 2 cifre)
function qnhFull(v){return /^\d{4}$/.test(v)||/^\d{2}\.\d{2}$/.test(v)}  // solo valori completi si convertono
// ATIS-PURE-END
function fStart(el,sel){if(el.querySelector("input"))return;const k=el.dataset.f,i=document.createElement("input");i.value=fval(k);i.dataset.f=k;
  const sf=k.replace(/^d?atis_/,""),isA=sf!==k,txt=["runway","flap","taxiway"].includes(k)||isA&&["letter","rwy","wind"].includes(sf);i.inputMode=txt?"text":k==="zfwcg"||isA&&sf==="qnh"?"decimal":"numeric";i.setAttribute("autocapitalize",txt?"characters":"off");i.setAttribute("autocomplete","off");i.setAttribute("autocorrect","off");i.spellcheck=false;
  let done=false;const end=()=>{if(done)return;done=true;if(isA&&sf==="tl"&&/^\d{1,2}$/.test(i.value)){i.value=i.value.padStart(3,"0");inpDirty[k]=i.value}if(isA&&sf==="rwy"&&/\/$/.test(i.value)){i.value=i.value.replace(/\/+$/,"");inpDirty[k]=i.value}inpSend();i.remove();fShow(el)};
  const nextF=()=>{const tc=el.closest(".tc");if(tc){const c=tc.nextElementSibling;return c&&c.querySelector(".fld")}let n=el.nextElementSibling;while(n&&!n.dataset.f)n=n.nextElementSibling;return n};  // tabella ATIS: il campo successivo e' nella cella accanto
  const adv=()=>{const n=nextF();end();if(n)fStart(n)};
  i.oninput=ev=>{if(k==="runway")i.value=i.value.toUpperCase();if(isA){const nv=atisClean(sf,i.value,/^(insertText|insertCompositionText)$/.test(ev&&ev.inputType||""),sf==="qnh"&&qnhInMode(k,i.value));if(nv!==i.value){i.value=nv;if(sf==="wind"){const n=nv.length;try{i.setSelectionRange(n,n)}catch(e){}}}}  /* riassegna (e cursore in fondo dopo il "/" rimesso) solo se il valore pulito differisce: niente salti a meta' stringa ne' interferenza con l'IME */if(k==="taxiway")i.value=i.value.toUpperCase().replace(/[^A-Z0-9]/g,"").slice(0,4);
    if(k==="fob"||k==="zfw")i.value=i.value.replace(/\D/g,"");if(k==="zfwcg")i.value=i.value.replace(/[^\d.]/g,"").replace(/^(\d*\.?\d?).*$/,"$1");  // fob/zfw cifre, zfwcg un decimale (28.4): nessun avanzamento automatico, solo Invio o tocco fuori
    inpDirty[k]=i.value;clearTimeout(inpTimer);inpTimer=setTimeout(inpSend,400);
    const full={v1:/^\d{3}$/,vr:/^\d{3}$/,v2:/^\d{3}$/,runway:/^\d{2}[LRC]$/,ta:/^\d{4}$/,thr_red_agl:/^\d{4}$/,accel_agl:/^\d{4}$/,flap:/^[1-3]$/}[k]||(isA?ATIS_FULL[sf]:null);  // ATIS: regole in ATIS_FULL (lettera, pista, vento, QNH); l'ultimo campo chiude (blur)
    if(full&&full.test(i.value))adv()};  // valore completo: passa al campo successivo, o chiude la modifica se è l'ultimo
  i.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();adv()}};i.onblur=end;
  el.textContent="";i.placeholder=el.dataset.l;el.append(i);i.focus({preventScroll:true});
  if(sel){try{i.setSelectionRange(sel[0],sel[1])}catch(e){}}else if(isA&&(sf==="rwy"||sf==="wind")){const n=i.value.length;i.setSelectionRange(n,n)}else i.select()}  // piste e vento: cursore in fondo (per aggiungere "/35R" o "G25")
// risposta lunga (piu' di 40 caratteri o con "Eg."): sotto il titolo a tutta larghezza invece che nella colonna destra
function longAns(t){return t.length>40||t.includes("Eg.")}
// card a tabella per l'ATIS (stile CRAFT): etichetta sopra, valore sotto; stessi campi inline (.fld) di prima, quindi stessi sanificatori, persistenza e focus
const TL={letter:"INFO",rwy:"RWY",wind:"WIND",qnh:"QNH",tl:"TL"};
// "+" accanto a RWY: torna nel campo (anche se il focus e' passato oltre), aggiunge "/" in coda se c'e' una pista e c'e' posto per altre 3 cifre (max 12 caratteri), cursore in fondo; la pulizia del "/" finale resta alla chiusura
function addRwy(c){let i=c.querySelector("input");if(!i){fStart(c);i=c.querySelector("input")}if(!i)return;const k=c.dataset.f,v=i.value;
  if(v&&!v.endsWith("/")&&v.length+4<=12){i.value=v+"/";inpDirty[k]=i.value;clearTimeout(inpTimer);inpTimer=setTimeout(inpSend,400)}
  i.focus({preventScroll:true});const n=i.value.length;try{i.setSelectionRange(n,n)}catch(e){}}
function toggleQnh(c){const k=c.dataset.f,i=c.querySelector("input"),v=(i?i.value:fval(k)).trim(),to=qUnit(k,v)==="In"?"hPa":"In";qu[k]=to;
  if(qnhFull(v)){const nv=qnhConvert(v);inpDirty[k]=nv;clearTimeout(inpTimer);inpTimer=setTimeout(inpSend,400);if(i)i.value=nv;else fShow(c)}
  qnhUnits();if(i){i.focus({preventScroll:true});const n=i.value.length;try{i.setSelectionRange(n,n)}catch(e){}}}
function qnhUnits(){document.querySelectorAll("#clbody .un").forEach(b=>{b.textContent=qUnit(b.dataset.f,fval(b.dataset.f).trim())})}
function tableNode(it){const el=document.createElement("div");el.className="a lg craft tbl";const h=document.createElement("div");h.className="ch";h.textContent=(/^datis_/.test(it.inputs[0].k)?"DEP":"ARR")+" ATIS";el.appendChild(h);
  const g=document.createElement("div");g.className="cf"+(it.inputs.length>4?" c5":"");
  for(const f of it.inputs){const d=document.createElement("div");d.className="tc";const lb=document.createElement("label");lb.textContent=TL[f.k.replace(/^d?atis_/,"")]||f.label;
    const c=document.createElement("span");c.dataset.f=f.k;c.dataset.l=f.label;c.title=f.label;c.onclick=()=>fStart(c);fShow(c);
    const tr=document.createElement("div");tr.className="tr";tr.appendChild(c);  // riga del valore: campo flessibile + controllo piccolo a destra (le etichette restano tutte alla stessa altezza)
    if(/_rwy$/.test(f.k)){const b=document.createElement("button");b.type="button";b.className="plus";b.textContent="+";b.title="Add runway";b.setAttribute("aria-label","Add runway");
      b.onpointerdown=ev=>{ev.preventDefault();addRwy(c)};b.onclick=ev=>ev.preventDefault();tr.appendChild(b)}  // pointerdown: il campo non perde il focus (niente chiusura e invio di dati sporchi)
    if(/_qnh$/.test(f.k)){const u=document.createElement("button");u.type="button";u.className="plus un";u.dataset.f=f.k;u.title="hPa / inHg";u.setAttribute("aria-label","QNH unit");u.textContent=qUnit(f.k,fval(f.k).trim());
      u.onpointerdown=ev=>{ev.preventDefault();toggleQnh(c)};u.onclick=ev=>ev.preventDefault();tr.appendChild(u)}  // "hPa"/"In": converte il valore e cambia unita' senza chiudere il campo
    d.appendChild(lb);d.appendChild(tr);g.appendChild(d)}
  el.appendChild(g);return el}
function inputsNode(it){const el=document.createElement("div");el.className="a inpr";const one=it.inputs.length===1;
  it.inputs.forEach((f,n)=>{if(n){const sp=document.createElement("span");sp.className="sep";sp.textContent=" / ";el.appendChild(sp)}
    const c=document.createElement("span");c.dataset.f=f.k;c.dataset.l=f.label;c.title=f.label;if(["runway","flap","fob","zfw","zfwcg","taxiway"].includes(f.k)||/^d?atis_/.test(f.k))c.dataset.w="1";if(f.prefix)c.dataset.pf=f.prefix;
    if(one)c.dataset.r=it.response||"";else if(!f.plan&&!/^d?atis_wind$/.test(f.k))c.dataset.pre=f.label.split(" (")[0].toLowerCase();
    c.onclick=()=>fStart(c);fShow(c);el.appendChild(c)});
  const ks=it.inputs.filter(f=>f.plan).map(f=>f.k);
  if(ks.length){const b=document.createElement("button");b.className="rs";b.textContent="\u21ba";b.title="Reset to plan";b.setAttribute("aria-label","Reset to plan");
    b.onclick=async ev=>{ev.stopPropagation();clearTimeout(inpTimer);inpTimer=null;ks.forEach(k=>delete inpDirty[k]);try{const s=await api("/api/fields",{reset:ks});lastRev=s.rev;last=s;syncInputs(s)}catch(e){}};el.appendChild(b)}
  return el}
// iPhone: un tocco su un'area non interattiva non toglie il focus (e la tastiera numerica può non avere Invio): lo togliamo noi
document.addEventListener("pointerdown",e=>{const a=document.activeElement;if(a&&(a.tagName==="INPUT"||a.tagName==="TEXTAREA")&&e.target!==a&&!(e.target.closest&&e.target.closest("input,textarea,select"))&&!(e.target.closest&&e.target.closest(".plus")&&a.closest(".tc")&&a.closest(".tc")===e.target.closest(".tc")))a.blur()},true);
const NATO={A:"Alpha",B:"Bravo",C:"Charlie",D:"Delta",E:"Echo",F:"Foxtrot",G:"Golf",H:"Hotel",I:"India",J:"Juliett",K:"Kilo",L:"Lima",M:"Mike",N:"November",O:"Oscar",P:"Papa",Q:"Quebec",R:"Romeo",S:"Sierra",T:"Tango",U:"Uniform",V:"Victor",W:"Whiskey",X:"X-ray",Y:"Yankee",Z:"Zulu"};
// riga "ATIS: Information KILO (K) · RWY 35L · QNH 1013 · Wind 270/12" dai campi annotati (solo quelli presenti, ordine fisso); vuota se nessun dato
function atisLine(keys,pre){const v=k=>fval(pre+"_"+k).trim(),p=[],l=v("letter");
  if(keys.includes("letter")&&l)p.push("Information "+(NATO[l]||l)+" ("+l+")");
  if(keys.includes("rwy")&&v("rwy"))p.push("RWY "+v("rwy"));if(keys.includes("qnh")&&v("qnh"))p.push("QNH "+v("qnh"));if(keys.includes("wind")&&v("wind"))p.push("Wind "+v("wind"));if(keys.includes("tl")&&v("tl"))p.push("TL "+v("tl"));
  return p}  // parti della riga ("Information Kilo (K)", "RWY 35L", ...); la pagina le unisce con " \u00b7 " dopo "ATIS: "
// METAR della destinazione sotto la voce (snapshot wx_dest, testo gia' formattato dal server): riga dati + grezzo piccolo; senza dati "METAR not available" attenuato
function wxFill(s){document.querySelectorAll("#clbody .wh").forEach(e=>{const w=s["wx_"+e.dataset.w];e.textContent="";e.classList.toggle("nd",!w);
  if(!w){e.textContent="METAR not available";return}
  const a=document.createElement("div");a.textContent=w.icao+" METAR"+(w.line?": "+w.line:"");e.appendChild(a);
  if(w.raw){const r=document.createElement("div");r.className="wr";r.textContent=w.raw;e.appendChild(r)}})}
function syncInputs(s){wxFill(s);qnhUnits();document.querySelectorAll("#clbody .fld").forEach(fShow);document.querySelectorAll("#clbody .ah").forEach(e=>{const p=e.dataset.p,parts=atisLine(e.dataset.h.split(","),p),t=parts.length,wn=(s.atis_warn||{})[p]&&e.dataset.h.includes("qnh")?s.atis_warn[p]:"";e.textContent="";e.hidden=!t;
    if(t){e.append("ATIS: ");parts.forEach((x,n)=>{if(n)e.append(" \u00b7 ");const sp=document.createElement("span");sp.textContent=x;if(x.startsWith("RWY ")&&(s.rwy_bad||{})[p])sp.className="rb";e.appendChild(sp)})}  // pista diversa dal piano: RWY in rosso
    if(t&&wn){const a=document.createElement("span");a.className="aw";a.textContent="\u26a0 QNH differs: "+wn;e.appendChild(a)}})}  // ATIS/METAR oltre 3 hPa: avviso giallo, la voce non diventa rossa
function clGo(body){api("/api/checklist-phase",body).then(apply).catch(()=>{})}
function checklist(s){const ph=s.cl_phases||[],cur=s.cl_phase,c=s.checklist;if(!c)return;
  const idx=ph.findIndex(p=>p.id===cur),strip=$("clstrip"),key=ph.length+"|"+cur;
  if(key!==clKey){clKey=key;strip.textContent="";
    for(const p of ph){const b=document.createElement("button");b.className="clchip"+(p.id===cur?" cur":"");b.textContent=p.name;b.onclick=()=>clGo({phase:p.id});strip.appendChild(b);
      if(p.id===cur)setTimeout(()=>b.scrollIntoView({inline:"center",block:"nearest"}),0)}}
  $("clprev").onclick=()=>clGo({step:-1});$("clnext").onclick=()=>clGo({step:1});
  $("clprev").disabled=idx<=0;$("clnext").disabled=idx>=ph.length-1;
  $("clname").textContent=c.phase.name;const sm=$("clsum");sm.textContent=c.bad+" out of spec";sm.className="clsum"+(c.bad?" bad":"");
  const body=$("clbody"),bk=JSON.stringify([cur,c.phase.name,c.bad,c.subphases.map(sp=>[sp.name,sp.items.map(i=>[i.text,i.status,i.kind,i.detail,i.response,i.type,i.live,i.raw,i.show,i.expected,i.inputs,i.layout,i.atis_hint,i.datis_hint,i.wx_hint])]),showLive,s.test_links]);
  if(bk!==clBodyKey){clBodyKey=bk;const y=window.scrollY,x=window.scrollX;
  const ae=document.activeElement,keep=craftEl&&ae&&craftEl.contains(ae)?[ae.dataset.c,ae.selectionStart,ae.selectionEnd]:null,keepF=ae&&ae.dataset&&ae.dataset.f?[ae.dataset.f,ae.selectionStart,ae.selectionEnd]:null;body.textContent="";
  const mk=(t,cls,txt)=>{const e=document.createElement(t);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e};
  for(const sp of c.subphases){if(sp.name)body.appendChild(mk("h3","",sp.name));
    for(const it of sp.items){if(it.type==="craft"){body.appendChild(craftNode(it));continue}
      const r=mk("div","cli st-"+it.status+" t-"+it.type);
      const ic=it.status==="ok"?"\u2713":it.status==="bad"?"\u2717":it.status==="opt"?"\u25cb":it.status==="wait"?"\u23f3":it.status==="na"?(it.kind==="unverified"?"unverified":"n/d"):"\u00b7";
      if(ic.length>2)r.classList.add("wl");
      r.appendChild(mk("div","ic"+(ic.length>2?" w":""),ic));const tx=mk("div","tx");const e=mk("div","e");
      if(it.type==="atc")e.appendChild(mk("span","badge","ATC"));if(it.type==="announce")e.appendChild(mk("span","badge","ANNOUNCE"));
      e.append(it.text);tx.appendChild(e);
      const av=(it.expected||it.response);if(it.inputs)tx.appendChild(it.layout==="table"?tableNode(it):inputsNode(it));else if(av&&it.type!=="announce")tx.appendChild(mk("div","a"+(it.expected?" set":"")+(longAns(av)?" lg":""),av));
      for(const [hk,pre] of [["datis_hint","datis"],["atis_hint","atis"]])if(it[hk]){const h=mk("div","a lg set ah");h.dataset.h=it[hk].join(",");h.dataset.p=pre;h.hidden=true;tx.appendChild(h)}
      if(it.wx_hint){const h=mk("div","a lg set wh");h.dataset.w=it.wx_hint;tx.appendChild(h)}
      if(showLive&&it.live&&it.live.length)tx.appendChild(mk("div","lv","Actual: "+it.live.map(l=>(it.live.length>1?l[0]+" ":"")+l[1]).join(" \u00b7 ")));
      if(s.test_links&&it.raw&&it.raw.length)tx.appendChild(mk("div","rw",it.raw.map(l=>l[0]+"="+l[1]).join(" \u00b7 ")));
      if(it.show&&it.live&&it.live.length)tx.appendChild(mk("div","now nv","NOW: "+it.live.map(l=>l[1]).join(", ")));
      if(it.kind==="wait")tx.appendChild(mk("div","now nv","After TL"+(it.detail?" ("+it.detail+")":"")));
      if(it.kind==="mismatch"){const mm=[...it.detail.matchAll(/actual ([^;,]+)/g)].map(m=>m[1].trim());
        tx.appendChild(mm.length?mk("div","now","NOW: "+mm.join(", ")):mk("div","d",it.detail))}
      r.appendChild(tx);body.appendChild(r)}}
  if(keep){const i=craftEl.querySelector('[data-c="'+keep[0]+'"]');if(i){i.focus({preventScroll:true});try{i.setSelectionRange(keep[1],keep[2])}catch(e){}}}
  if(keepF){const c=body.querySelector('.fld[data-f="'+keepF[0]+'"]');if(c)fStart(c,[keepF[1],keepF[2]])}
  window.scrollTo(x,y)}
  syncCraft(s.clearance);syncInputs(s);
  const nb=$("clnextbtn"),nx=ph[idx+1];nb.hidden=!nx;if(nx){nb.textContent="Next phase: "+nx.name;nb.onclick=()=>{clGo({step:1});$("clcard").scrollIntoView({block:"start"})}}}
function speech(s){const sp=$("speech"),tx=$("sptxt"),l=s.log,ago=s.spoke_ago==null?1e9:s.spoke_ago;
  if(s.spoke_n!==spN){if(spN!==null)spExp=false;spN=s.spoke_n;spUntil=Date.now()+Math.max(0,10-ago)*1000}
  const big=Date.now()<spUntil&&l.length>0;sp.className=(big?"big":"")+(spExp&&!big?" exp":"");
  if(l.length){const [t,...rest]=l[0].split("  ");tx.textContent=big?rest.join("  "):t+"  "+rest.join("  ");tx.className="";$("sptime").textContent=t}
  else{tx.textContent="Listening…";tx.className="idle";$("sptime").textContent=""}}
function render(){const s=last;if(!s)return;const fly=s.phase==="flying";
  if(view==="flight"&&shownView!=="flight")window.scrollTo(0,0);shownView=view;  // solo al cambio vista: la vista Volo parte dall'alto (pills visibili)
  $("main").classList.toggle("fv",view==="flight");$("cfg").hidden=view!=="config";$("flt").hidden=view!=="flight";$("chips").hidden=$("more").hidden=$("sqk").hidden=$("fpills").hidden=view!=="flight";
  const a=$("aao");a.textContent=s.aao_ok===null?"AAO: waiting":s.aao_ok?"AAO: connected":"AAO: not responding";a.className="pill "+(s.aao_ok===null?"":s.aao_ok?"ok":"bad");
  $("go").textContent=fly?"Back to flight":"Briefing complete";$("go").className="big "+(fly?"fly":"");$("end").hidden=!fly;$("end2").hidden=!fly;
  $("info").textContent=s.info;chips(s);checklist(s);cockpit(s);fdata(s);fpills(s);plans(s);calib(s);
  speech(s);
  const l=s.log;
  $("log").innerHTML="";for(const t of l.slice(1)){const li=document.createElement("li");li.textContent=t;$("log").appendChild(li)}}
function apply(s){const prev=last;last=s;
  if(!prev||prev.phase!==s.phase)view=s.phase==="flying"?"flight":"config";  // cambio di fase → vista di default
  render()}
async function poll(){try{const s=await api("/api/state");
  if(lastRev===-1){build(s);fill(s);lastRev=s.rev}else if(s.rev!==lastRev){fill(s);lastRev=s.rev}apply(s)}catch(e){const a=$("aao");a.textContent="Connection lost…";a.className="pill bad"}setTimeout(poll,1500)}
$("load").onclick=async()=>{$("load").disabled=true;$("info").textContent="Loading…";
  try{const s=await api("/api/load-plan",{simbrief:$("simbrief").value});fill(s);lastRev=s.rev;apply(s)}catch(e){$("info").textContent="SimBrief error: "+e.message}$("load").disabled=false};
$("go").onclick=async()=>{await api("/api/fields",collect());
  if(phase==="flying"){view="flight";render()}  // la lettura continua, si torna solo alla vista Volo
  else{const s=await api("/api/phase",{phase:"flying"});phase=s.phase;apply(s)}};
$("end").onclick=$("end2").onclick=async()=>{const s=await api("/api/phase",{phase:"briefing"});phase=s.phase;apply(s)};
$("speech").onclick=()=>{spExp=!spExp;if(last)speech(last)};
if(window.ResizeObserver)new ResizeObserver(()=>document.documentElement.style.setProperty("--sp-h",$("speech").offsetHeight+"px")).observe($("speech"));

$("edit").onclick=()=>{view="config";if(last)fill(last);render()};  // fill: Departure puo' essere cambiato dal T/O runway della checklist
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
