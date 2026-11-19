const REMOTE=document.body.dataset.mode==="remote";  // pagina /flight del sito: dati dallo snapshot del programma, comandi via server (adattatore in api())
let rCmd=true;const RO=()=>REMOTE&&!rCmd;  // RO: il programma sul PC non accetta comandi dal sito (sola lettura)
const ghosts={};const $=id=>document.getElementById(id)||(ghosts[id]||(ghosts[id]=document.createElement("div")));  // id assente (pagina remota): elemento fantasma, mai nel DOM
let lastRev=-1,spN=null,spUntil=0,spExp=false,chipsKey="",csKey="",fdKey="",planKey="",clBodyKey="",timer=null,phase="briefing",view="config",shownView="config",last=null;
async function api(path,body){if(REMOTE&&body)return remoteApi(path,body);const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});
  if(!r.ok){const t=await r.text();throw new Error(t||r.status)}return r.json()}
function build(s){const g=$("grid"),apt=$("apt");g.textContent="";apt.textContent="";const mk=(t,txt)=>{const e=document.createElement(t);if(txt!==undefined)e.textContent=txt;return e};
  for(const [k,l] of s.labels){const d=mk("div"),lb=mk("label",l),i=mk("input");i.dataset.k=k;i.setAttribute("inputmode",["origin","destination","dep_time"].includes(k)?"text":"numeric");i.setAttribute("autocomplete","off");i.setAttribute("enterkeyhint","next");
    if(k==="origin"){i.placeholder="LIMC/17R";i.setAttribute("autocapitalize","characters")}
    d.append(lb,i);(k==="origin"||k==="destination"||k==="dep_time"?apt:g).appendChild(d);
    if(k==="dep_time"){const e=mk("div"),l2=mk("label","Local time"),dl=mk("input");dl.id="deploc";dl.disabled=true;e.append(l2,dl);apt.appendChild(e)}}
  const opts=(sel,list)=>{sel.textContent="";for(const m of list){const o=mk("option",m[0].toUpperCase()+m.slice(1));o.value=m;sel.appendChild(o)}};
  opts($("mode"),s.modes);opts($("clmode"),s.cl_modes);
  document.querySelectorAll("[data-k]").forEach(i=>{i.oninput=()=>{mark(i.dataset.k,i);save();localTime()};i.onchange=()=>{if(REMOTE)save(true)}});$("mode").onchange=()=>{mark("mode",$("mode"));save(true)};$("clmode").onchange=()=>{mark("checklist_mode",$("clmode"));save(true)};document.querySelectorAll("#cfg input").forEach(i=>i.onkeydown=nextField);$("simbrief").oninput=()=>{if(REMOTE)mark("simbrief",$("simbrief"));else save()};$("simdll").onclick=simDll;$("simretry").onclick=simRetry;
}
async function refresh(){const s=await api("/api/state");apply(s)}
function localTime(){const el=$("deploc"),dt=document.querySelector('[data-k="dep_time"]');if(!el||!dt)return;const v=(dt.value||"").trim();
  const m=v.match(/^(\d{1,2})[:.]?(\d{2})$/);if(!m||+m[1]>23||+m[2]>59){el.value="";return}
  const n=new Date(),d=new Date(Date.UTC(n.getUTCFullYear(),n.getUTCMonth(),n.getUTCDate(),+m[1],+m[2]));
  el.value=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",hour12:false})}
function nextField(e){if(e.key!=="Enter")return;e.preventDefault();
  const all=[...document.querySelectorAll("#cfg input:not([disabled])")].filter(i=>i.offsetParent);const n=all[all.indexOf(e.target)+1];
  if(n){n.focus();n.select&&n.select()}else{e.target.blur()}}
async function simDll(){const r=$("simmsg");if(!confirm(last&&last.dll_confirm||"Download SimConnect.dll?"))return;
  r.textContent="";try{await api("/api/simdll-fetch",{confirm:true})}catch(e){r.textContent="Download failed: "+e.message}}
async function simRetry(){const r=$("simmsg");r.textContent="";try{await api("/api/sim-retry",{})}catch(e){r.textContent="Retry failed: "+e.message}}
const rDirty=new Map();let rPre="",rBuilt=false;  // remoto: campi modificati e non ancora confermati dal PC (fill non li sovrascrive); ID SimBrief dell'account
const keep=(k,el)=>REMOTE&&(rDirty.has(k)||document.activeElement===el);  // fill() lascia stare campi con focus o modifiche non inviate
function fill(s){document.querySelectorAll("[data-k]").forEach(i=>{if(!keep(i.dataset.k,i))i.value=(s.fields||{})[i.dataset.k]??""});
  const sb=$("simbrief");if(!keep("simbrief",sb))sb.value=s.simbrief||(REMOTE?rPre:"");
  if(!keep("mode",$("mode")))$("mode").value=s.mode;if(!keep("checklist_mode",$("clmode")))$("clmode").value=s.checklist_mode;localTime()}
function collect(){const o={simbrief:$("simbrief").value,mode:$("mode").value,checklist_mode:$("clmode").value};document.querySelectorAll("[data-k]").forEach(i=>o[i.dataset.k]=i.value);return o}
function rBody(){const o={};for(const [k,el] of rDirty)if(k!=="simbrief")o[k]=el.value;return o}  // remoto: solo i campi modificati (mai simbrief: viaggia con load-plan)
function mark(k,el){if(REMOTE)rDirty.set(k,el)}
async function rSave(){clearTimeout(timer);timer=null;const b=rBody();if(!Object.keys(b).length)return;
  const done=()=>{for(const k in b){const el=rDirty.get(k);if(el&&el.value===b[k])rDirty.delete(k)}};
  try{const s=await api("/api/fields",b);done();fill(s)}catch(e){if(last)fill(last)}}  // errore: i campi restano in rDirty (messaggio in rerr), non si perdono
function save(now){clearTimeout(timer);if(REMOTE){timer=setTimeout(rSave,now?0:1200);return}timer=setTimeout(async()=>{try{const s=await api("/api/fields",collect());lastRev=s.rev}catch(e){}},400)}
function planHtml(p,open){if(!p||!(p.route||p.waypoints&&p.waypoints.length))return null;
  const box=document.createElement("div");box.className="plan";
  const head=[p.flight,p.aircraft,p.cruise_alt&&("FL"+Math.round(p.cruise_alt/100)),p.distance_nm&&(p.distance_nm+" nm"),p.ete,p.alternate&&("ALTN "+p.alternate)].filter(Boolean).join(" · ");
  const m=document.createElement("div");m.className="meta";m.textContent=head;box.appendChild(m);
  const sidstar=[p.sid&&("SID "+p.sid),p.star&&("STAR "+p.star)].filter(Boolean).join("   ");
  if(sidstar){const d=document.createElement("div");d.textContent=sidstar;d.className="bold";box.appendChild(d)}
  if(p.route){const r=document.createElement("div");r.className="route";r.textContent=p.route;box.appendChild(r)}
  if(p.waypoints&&p.waypoints.length){const dt=document.createElement("details");dt.open=!!open;const sm=document.createElement("summary");sm.textContent="Waypoints ("+p.waypoints.length+")";dt.appendChild(sm);
    for(const w of p.waypoints){const row=document.createElement("div");row.className="wp";for(const v of [w.ident,w.via||"",w.alt!=null?w.alt+" ft":""]){const c=document.createElement("span");c.textContent=v;row.appendChild(c)}dt.appendChild(row)}box.appendChild(dt)}
  return box}
function plans(s){const k=JSON.stringify(s.plan);if(k===planKey)return;planKey=k;for(const [id,open] of [["plancfg",false],["planflt",false]]){const el=$(id);const keep=el.querySelector("details")?.open;const b=planHtml(s.plan,keep||open);el.textContent="";if(b)el.appendChild(b)}}
function chips(s){const f=s.fields,c=$("chips"),rb=s.rwy_bad||{},k=JSON.stringify([f,rb]);if(k===chipsKey)return;chipsKey=k;c.textContent="";
  const add=(k,v,w)=>{if(v){const d=document.createElement("span");d.className="chip"+(w?" bad":"");const b=document.createElement("b");b.textContent=k;d.append(b,v);c.appendChild(d)}};
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
function fdata(s){const k=JSON.stringify([s.flight,s.sim_ok]);if(k===fdKey)return;fdKey=k;const b=$("fdbody");b.textContent="";const f=s.flight||[];
  const mk=(c,t)=>{const e=document.createElement("div");e.className=c;e.textContent=t;return e};
  if(!f.length||s.sim_ok===false){b.appendChild(mk("meta",s.sim_ok===false?"SimConnect unavailable":"No data yet"));return}
  const grid=document.createElement("div");grid.className="cg";
  for(const [n,v] of f){const c=document.createElement("div");c.className="ci";c.appendChild(mk("n",n));c.appendChild(mk("v"+(v.startsWith("\u2014")?" na":""),v));grid.appendChild(c)}
  b.appendChild(grid)}
function fpills(s){const e=$("fpills"),defs=s.pill_defs||[],p=s.pills||{},k=JSON.stringify(defs);
  if(k!==e.dataset.k){e.dataset.k=k;e.textContent="";for(const [key,l] of defs){const c=document.createElement("span");c.className="chip";c.id="pill-"+key;const b=document.createElement("b");b.textContent=l;c.appendChild(b);const v=document.createElement("span");c.appendChild(v);e.appendChild(c)}}
  for(const [key] of defs){const v=$("pill-"+key).lastChild;const t=p[key]||"\u2014";if(v.textContent!==t)v.textContent=t}}
let clKey="",showLive=localStorage.getItem("showLive")==="1";
$("livesw").checked=showLive;$("livesw").onchange=()=>{showLive=$("livesw").checked;localStorage.setItem("showLive",showLive?"1":"0");if(last)checklist(last)};
let craftEl=null,clrTimer=null,sqkEdit=false,sqkVal="";
function sqkShow(){const e=$("sqk");if(sqkEdit)return;e.className=RO()?"chip":"chip sqk";e.textContent="";const b=document.createElement("b");b.textContent="SQK";e.append(b,sqkVal||"\u2014")}
function sqkStart(){if(sqkEdit||RO())return;sqkEdit=true;const e=$("sqk"),i=document.createElement("input");i.inputMode="numeric";i.maxLength=4;i.pattern="[0-7]*";i.value=sqkVal;
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
    i.disabled=RO();i.setAttribute("autocomplete","off");i.setAttribute("autocapitalize",f.k==="t"?"off":"characters");i.setAttribute("autocorrect","off");i.spellcheck=false;
    if(f.k==="t"){i.inputMode="numeric";i.maxLength=4;i.pattern="[0-7]*"}
    i.oninput=()=>{if(f.k==="t"){const v=i.value.replace(/[^0-7]/g,"");if(v!==i.value)i.value=v}if(f.k==="f"&&i.value.length===7){const n=craftEl.querySelector('[data-c="t"]');if(n)n.focus()}clearTimeout(clrTimer);clrTimer=setTimeout(clrSend,400)};
    i.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();i.blur()}};
    d.appendChild(lb);d.appendChild(i);g.appendChild(d)}
  el.appendChild(g);const b=document.createElement("div");b.className="cb";const rb=document.createElement("button");rb.className="ghost";rb.disabled=RO();rb.textContent="Reset to plan";
  rb.onclick=async()=>{clearTimeout(clrTimer);clrTimer=null;try{const s=await api("/api/clearance",{reset:true});syncCraft(s.clearance,true)}catch(e){}};
  b.appendChild(rb);el.appendChild(b);craftEl=el;return el}
function syncCraft(c,force){if(c&&!sqkEdit){sqkVal=c.t||"";sqkShow()}if(!craftEl||!c||(clrTimer&&!force))return;
  craftEl.querySelectorAll("[data-c]").forEach(i=>{const v=c[i.dataset.c]||"";if(i.value!==v&&(force||document.activeElement!==i))i.value=v})}
let inpDirty={},inpTimer=null;const rPend={};  // rPend: valori inviati al programma e non ancora confermati dallo snapshot (solo pagina remota)
function inpSend(){clearTimeout(inpTimer);inpTimer=null;const o=inpDirty;inpDirty={};
  if(REMOTE){const ks=Object.keys(o);if(!ks.length)return;Object.assign(rPend,o);  // remoto: la pagina non assume l'esito, lo snapshot e' la verita'
    api("/api/fields",o).catch(()=>{}).then(()=>{ks.forEach(k=>{if(rPend[k]===o[k])delete rPend[k]});if(last)syncInputs(last)});return}
  if(last)Object.assign(last.fields,o);if(Object.keys(o).length)api("/api/fields",o).then(s=>{lastRev=s.rev}).catch(()=>{})}
function fval(k){return k in inpDirty?inpDirty[k]:k in rPend?rPend[k]:((last&&last.fields[k])||"")}
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
function fStart(el,sel){if(RO()||el.querySelector("input"))return;const k=el.dataset.f,i=document.createElement("input");i.value=fval(k);i.dataset.f=k;
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
  if(ks.length){const b=document.createElement("button");b.className="rs";b.disabled=RO();b.textContent="\u21ba";b.title="Reset to plan";b.setAttribute("aria-label","Reset to plan");
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
  const idx=ph.findIndex(p=>p.id===cur),strip=$("clstrip"),key=ph.length+"|"+cur+"|"+RO();
  if(key!==clKey){clKey=key;strip.textContent="";
    for(const p of ph){const b=document.createElement("button");b.className="clchip"+(p.id===cur?" cur":"");b.disabled=RO();b.textContent=p.name;b.onclick=()=>clGo({phase:p.id});strip.appendChild(b);
      if(p.id===cur)setTimeout(()=>b.scrollIntoView({inline:"center",block:"nearest"}),0)}}
  $("clprev").onclick=()=>clGo({step:-1});$("clnext").onclick=()=>clGo({step:1});
  $("clprev").disabled=RO()||idx<=0;$("clnext").disabled=RO()||idx>=ph.length-1;
  $("clname").textContent=c.phase.name;const sm=$("clsum");sm.textContent=c.bad+" out of spec";sm.className="clsum"+(c.bad?" bad":"");
  const body=$("clbody"),bk=JSON.stringify([cur,c.phase.name,c.bad,c.subphases.map(sp=>[sp.name,sp.items.map(i=>[i.text,i.status,i.kind,i.detail,i.response,i.type,i.live,i.raw,i.show,i.latched,i.was_ok,i.expected,i.inputs,i.layout,i.atis_hint,i.datis_hint,i.wx_hint])]),showLive,s.test_links,RO()]);
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
      if((it.show||it.kind==="wait")&&it.live&&it.live.length)tx.appendChild(mk("div","now nv","NOW: "+it.live.map(l=>l[1]).join(", ")));
      if(it.kind==="wait")tx.appendChild(mk("div","now nv","Waiting: "+(it.gate||it.detail||"")));
      if(it.was_ok&&it.live&&it.live.length)tx.appendChild(mk("div","now nv","was OK \u00b7 NOW: "+it.live.map(l=>l[1]).join(", ")));
      if(it.kind==="mismatch"){const mm=[...it.detail.matchAll(/actual ([^;,]+)/g)].map(m=>m[1].trim());
        tx.appendChild(mm.length?mk("div","now","NOW: "+mm.join(", ")):mk("div","d",it.detail))}
      r.appendChild(tx);body.appendChild(r)}}
  if(keep){const i=craftEl.querySelector('[data-c="'+keep[0]+'"]');if(i){i.focus({preventScroll:true});try{i.setSelectionRange(keep[1],keep[2])}catch(e){}}}
  if(keepF){const c=body.querySelector('.fld[data-f="'+keepF[0]+'"]');if(c)fStart(c,[keepF[1],keepF[2]])}
  window.scrollTo(x,y)}
  syncCraft(s.clearance);syncInputs(s);
  const nb=$("clnextbtn"),nx=ph[idx+1];nb.hidden=RO()||!nx;if(nx){nb.textContent="Next phase: "+nx.name;nb.onclick=()=>{clGo({step:1});$("clcard").scrollIntoView({block:"start"})}}}
function speech(s){const sp=$("speech"),tx=$("sptxt"),l=s.log,ago=s.spoke_ago==null?1e9:s.spoke_ago;
  if(s.spoke_n!==spN){if(spN!==null)spExp=false;spN=s.spoke_n;spUntil=Date.now()+Math.max(0,10-ago)*1000}
  const big=Date.now()<spUntil&&l.length>0;sp.className=(big?"big":"")+(spExp&&!big?" exp":"");
  if(l.length){const [t,...rest]=l[0].split("  ");tx.textContent=big?rest.join("  "):t+"  "+rest.join("  ");tx.className="";$("sptime").textContent=t}
  else{tx.textContent="Listening…";tx.className="idle";$("sptime").textContent=""}}
function render(){const s=last;if(!s)return;const fly=s.phase==="flying";
  if(view==="flight"&&shownView!=="flight")window.scrollTo(0,0);shownView=view;  // solo al cambio vista: la vista Volo parte dall'alto (pills visibili)
  $("main").classList.toggle("fv",view==="flight");$("cfg").hidden=view!=="config";$("flt").hidden=view!=="flight";$("chips").hidden=$("more").hidden=$("sqk").hidden=$("fpills").hidden=view!=="flight";
  const a=$("sim");a.textContent=s.sim_demo?"SimConnect: demo":s.sim_ok===null?"SimConnect: waiting":s.sim_ok?"SimConnect: connected":s.dll_missing?"SimConnect: DLL missing":"SimConnect: not connected";a.className="pill "+(s.sim_ok===null?"":s.sim_ok?"ok":"bad");
  $("simstat").textContent=s.sim_demo?"Demo flight: the simulator is not read.":s.sim_ok?"Connected to MSFS.":(s.sim_error||(REMOTE&&s.dll_missing?"SimConnect.dll is missing: download it from the program on the PC.":"Waiting for MSFS…"));$("simmsg").textContent=s.dll_fetching?"Downloading…":(s.dll_msg||"");$("simdll").hidden=!s.dll_missing;$("simdll").disabled=!!s.dll_fetching;$("simretry").hidden=!!s.sim_demo;
  $("go").textContent=fly?"Back to flight":"Briefing complete";$("go").className="big "+(fly?"fly":"");$("end").hidden=!fly;$("end2").hidden=!fly;
  $("info").textContent=s.info;chips(s);checklist(s);cockpit(s);fdata(s);fpills(s);plans(s);
  speech(s);
  const l=s.log;
  $("log").textContent="";for(const t of l.slice(1)){const li=document.createElement("li");li.textContent=t;$("log").appendChild(li)}}
function apply(s){const prev=last;last=s;
  if(!prev||prev.phase!==s.phase)view=s.phase==="flying"?"flight":"config";  // cambio di fase → vista di default
  render()}
async function poll(){try{const s=await api("/api/state");
  if(lastRev===-1){build(s);fill(s);lastRev=s.rev}else if(s.rev!==lastRev){fill(s);lastRev=s.rev}apply(s)}catch(e){const a=$("sim");a.textContent="Connection lost…";a.className="pill bad"}setTimeout(poll,1500)}
let rseq=null,repoch=null,rlast=null;
function norm(s){const d={fields:{},plan:{},flight:[],monitor:[],pills:{},pill_defs:[],log:[],cl_phases:[],clearance:{},rwy_bad:{},atis_warn:{},wx_dep:null,wx_dest:null,info:"",test_links:false,checklist:null,spoke_n:0,labels:[],modes:[],cl_modes:[],simbrief:"",rev:0,dll_missing:false};
  for(const k in d)if(s[k]===undefined)s[k]=d[k];return s}
function rRO(){document.querySelectorAll("#cfg input:not(#deploc),#cfg select,#go,#end,#end2,#simretry").forEach(e=>e.disabled=RO());if(RO())$("load").disabled=true;else if($("info").textContent!=="Loading…")$("load").disabled=false}  // sola lettura: controlli del briefing spenti
function rbanner(t){const b=$("banner");b.textContent=t||"";b.hidden=!t}
function rdata(on){$("rdata").hidden=!on}
function remoteApply(d){
  if(!d.connected){rseq=null;repoch=null;rlast=null;last=null;rdata(false);rbanner("Program not connected");return}
  rCmd=d.cmd!==false;document.body.classList.toggle("ro",RO());  // senza "cmd" (server vecchio) si assume acceso: il server risponde 409 se non e' vero
  if(d.snapshot){if(rseq!==null&&d.epoch===repoch&&d.seq<rseq)return;  // risposta vecchia arrivata in ritardo: ignorata
    rseq=d.seq;repoch=d.epoch;rlast=norm(d.snapshot);rlast.spoke_ago=d.spoke_ago}
  else if(d.unchanged&&rlast){rlast.spoke_ago=d.spoke_ago}
  if(!rlast){rdata(false);rbanner("Program connected: waiting for the first data");return}
  if(!rBuilt&&rlast.labels.length){build(rlast);rBuilt=true;fill(rlast);lastRev=rlast.rev;rPrefill()}else if(rlast.rev!==lastRev){fill(rlast);lastRev=rlast.rev}
  if(rlast.phase==="briefing"&&!rlast.labels.length){rdata(false);rbanner("Update the program on the PC to edit the briefing remotely");return}
  rRO();rdata(true);rbanner(d.age!=null&&d.age>20?"No update for "+Math.round(d.age)+" s":RO()?"Remote control is off on the PC":"");apply(rlast)}
async function rPrefill(){try{await rToken();const v=rSb;  // ID SimBrief dell'account (da /api/auth/me, gia' letto per il token CSRF) nel campo vuoto: mai inviato da solo
  if(/^[A-Za-z0-9 ._-]{1,40}$/.test(v)){rPre=v;if(!$("simbrief").value&&!rDirty.has("simbrief"))$("simbrief").value=rPre}}catch(e){}}
async function rpoll(){const w=await rfetch();if(w!==null)setTimeout(rpoll,w)}
async function rfetch(){let wait=3000;  // una lettura dello stato: ritorna l'attesa prima della prossima (null: pagina lasciata)
  try{const r=await fetch("/api/flight/state"+(rseq!==null&&repoch!==null?"?seq="+rseq+"&epoch="+repoch:""),{credentials:"same-origin",cache:"no-store"});
    if(r.status===401){location.replace("/login");return null}
    if(r.status===429)wait=Math.max(1,+r.headers.get("Retry-After")||5)*1000;
    else if(!r.ok)throw new Error(r.status);
    else{const d=await r.json();remoteApply(d);wait=d.connected?1000:3000}}
  catch(e){rbanner("Connection lost\u2026")}
  return wait}
// ---- comandi della pagina remota: POST /api/flight/cmd/<nome> via server; un comando alla volta; poi si ATTENDE il prossimo
// snapshot e lo si restituisce: i chiamanti di api() non cambiano e la pagina non assume l'esito (lo stato vero e' quello del PC)
const CMD_PATHS={"/api/fields":"fields","/api/load-plan":"load-plan","/api/phase":"phase","/api/checklist-phase":"checklist-phase","/api/clearance":"clearance","/api/sim-retry":"sim-retry"};
const CMD_ERR={busy:"The program is busy, try again in a moment.",program_offline:"Program not connected.",commands_unsupported:"This program version cannot be controlled from the site: update it on the PC.",commands_disabled:"Remote control is off on the PC.",commands_off:"Remote control is switched off on the server."};
const LOAD_ERR={network:"SimBrief is not reachable.",not_found:"SimBrief did not find this ID or username.",bad_plan:"The SimBrief plan could not be read."};
const MERGE_MS=600,SNAP_WAIT_MS=5000;  // finestra in cui le modifiche ai campi si fondono in una richiesta; attesa massima dello snapshot
let rCsrf=null,rSb="",rq=[],rRunning=false,rErrTimer=null;
const rSleep=ms=>new Promise(r=>setTimeout(r,ms));
const rFail=(t,silent)=>{const e=new Error(t);e.fixed=true;e.silent=!!silent;return e};
const rClean=v=>typeof v==="string"?v.replace(/[\x00-\x1f\x7f-\x9f]/g," "):Array.isArray(v)?v.map(rClean):v&&typeof v==="object"?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,rClean(x)])):v;
function rErr(t){const e=$("rerr");e.textContent=t||"";e.hidden=!t;clearTimeout(rErrTimer);if(t)rErrTimer=setTimeout(()=>rErr(""),8000)}
async function rToken(force){if(rCsrf&&!force)return rCsrf;
  const r=await fetch("/api/auth/me",{credentials:"same-origin",cache:"no-store"});
  if(r.status===401){location.replace("/login");throw rFail("Please sign in again.",true)}
  if(!r.ok)throw rFail("Request failed.");
  const d=await r.json();if(!d||typeof d.csrf_token!=="string")throw rFail("Request failed.");
  rCsrf=d.csrf_token;rSb=typeof d.simbrief_id==="string"?d.simbrief_id:"";return rCsrf}
async function rPost(name,body){
  for(let n=0;;n++){const tok=await rToken(n>0);
    const r=await fetch("/api/flight/cmd/"+name,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":tok},body:JSON.stringify(body)});
    if(r.status===403&&n===0){rCsrf=null;continue}  // token scaduto: lo rilegge una volta
    return r}}
async function rCheck(name,r){if(r.ok)return;
  let d={};try{d=await r.json()}catch(e){}const err=d&&typeof d.error==="string"?d.error:"";
  if(r.status===401){location.replace("/login");throw rFail("Please sign in again.",true)}
  if(r.status===403){location.reload();throw rFail("Session expired: reloading.",true)}
  if(r.status===400)throw rFail("The program did not accept this change.");
  if(r.status===404)throw rFail("This is not available from the site.");
  if(r.status===409)throw rFail(CMD_ERR[err]||"The program cannot do that now.");
  if(r.status===429)throw rFail("Too many requests: wait a moment.");
  if(r.status===502)throw rFail(name==="load-plan"&&LOAD_ERR[d.reason]||"The program could not do that.");
  if(r.status===503)throw rFail(CMD_ERR.commands_off);
  if(r.status===504)throw rFail("The PC did not answer; the page shows the PC's real state.");
  throw rFail("Request failed.")}
async function rAwait(seq0,ep0){const end=Date.now()+SNAP_WAIT_MS;
  for(;;){await rfetch();if(rseq!==seq0||repoch!==ep0||Date.now()>=end)break;await rSleep(300)}
  return rlast||last}
async function rSend(name,body){
  if(name==="phase"&&body.phase==="briefing"){if(!confirm("End the flight and go back to the briefing?"))throw rFail("Cancelled",true);body={phase:"briefing",confirm:true}}
  const seq0=rseq,ep0=repoch,r=await rPost(name,rClean(body));await rCheck(name,r);rErr("");return rAwait(seq0,ep0)}
function rMerge(job,body){if(job.body.reset||body.reset)return false;  // un reset non si fonde con altri valori (l'ordine conta)
  if(job.name==="fields"){Object.assign(job.body,body);return true}
  if(job.name==="clearance"){Object.assign(job.body,body);return true}
  return false}
async function rRun(){rRunning=true;
  while(rq.length){const job=rq[0];if(job.wait)await rSleep(job.wait);job.started=true;
    try{const s=await rSend(job.name,job.body);job.ws.forEach(w=>w[0](s))}
    catch(e){if(!e.silent)rErr(e.fixed?e.message:"Connection lost.");job.ws.forEach(w=>w[1](e))}
    rq.shift()}
  rRunning=false}
function remoteApi(path,body){const name=CMD_PATHS[path];if(!name)return Promise.reject(rFail("This is not available from the site."));
  return new Promise((res,rej)=>{const last=rq[rq.length-1];
    if(last&&!last.started&&last.name===name&&rMerge(last,body)){last.ws.push([res,rej]);return}
    rq.push({name,body:Object.assign({},body),ws:[[res,rej]],started:false,wait:(name==="fields"||name==="clearance")&&!body.reset?MERGE_MS:0});
    if(!rRunning)rRun()})}
$("load").onclick=async()=>{$("load").disabled=true;$("info").textContent="Loading…";
  try{const raw=$("simbrief").value,v=raw.trim();if(REMOTE)await rSave();const s=await api("/api/load-plan",REMOTE&&!v?{}:{simbrief:raw});rDirty.delete("simbrief");fill(s);lastRev=s.rev;apply(s)}catch(e){$("info").textContent="SimBrief error: "+e.message}$("load").disabled=false};
$("go").onclick=async()=>{try{if(REMOTE)await rSave();else await api("/api/fields",collect());
  if(phase==="flying"){view="flight";render()}  // la lettura continua, si torna solo alla vista Volo
  else{const s=await api("/api/phase",{phase:"flying"});phase=s.phase;apply(s)}}catch(e){if(!REMOTE)throw e}};
$("end").onclick=$("end2").onclick=async()=>{try{const s=await api("/api/phase",{phase:"briefing"});phase=s.phase;apply(s)}catch(e){if(!REMOTE)throw e}};  // remoto: confirm() in rSend, poi {"phase":"briefing","confirm":true}
$("speech").onclick=()=>{spExp=!spExp;if(last)speech(last)};
if(window.ResizeObserver)new ResizeObserver(()=>document.documentElement.style.setProperty("--sp-h",$("speech").offsetHeight+"px")).observe($("speech"));

$("edit").onclick=()=>{view="config";if(last)fill(last);render()};  // fill: Departure puo' essere cambiato dal T/O runway della checklist
const _apply=apply;apply=function(s){phase=s.phase;_apply(s)};
if(REMOTE)rpoll();else poll();
