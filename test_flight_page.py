"""Pagina /flight del sito (sola lettura): id di flight.html contro app.js, niente codice inline, app.js in modalita'
remota eseguita con node su un DOM finto (se node manca, quei test si saltano)."""
from __future__ import annotations

import glob
import json
import pathlib
import re
import shutil
import subprocess

import pytest

import snapshot
from test_snapshot import make_state

ROOT = pathlib.Path(__file__).resolve().parent
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
FLIGHT = (ROOT / "server" / "public" / "flight.html").read_text(encoding="utf-8")
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node") or next(iter(sorted(glob.glob(str(pathlib.Path.home() / ".nvm/versions/node/*/bin/node")))), None)


def ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def js_ids():
    return set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', JS))


# id usati da app.js che la pagina remota NON ha di proposito (briefing, calibrazione, scaricamento DLL, comandi):
LOCAL_ONLY = {"deploc", "simdll", "tech", "thrback", "thrcal", "thrcard", "thrclear",
              "thrclose", "threrr", "thrmsg", "thrnext", "thrsum", "thrsumtxt", "thrskip", "thrcancel",
              "thrstart", "thrst", "thrtab", "thrtext", "thrtitle", "thrwarn", "thrbtns", "banner", "rdata", "rerr"}
REMOTE_ONLY = {"banner", "rdata", "rerr"}


def test_every_id_used_by_app_js_is_in_flight_html_or_declared_local_only():
    used, remote, local = js_ids(), ids(FLIGHT), ids(INDEX)
    missing = used - remote - LOCAL_ONLY
    assert not missing, f"id usati da app.js ma assenti da flight.html e non dichiarati solo-locali: {sorted(missing)}"
    assert (LOCAL_ONLY - REMOTE_ONLY - {"deploc"}) <= local, sorted((LOCAL_ONLY - REMOTE_ONLY - {"deploc"}) - local)  # niente voci morte nella lista
    assert REMOTE_ONLY <= remote and REMOTE_ONLY.isdisjoint(local)


def test_flight_html_has_the_views_the_remote_render_needs():
    need = {"main", "hdr", "sim", "chips", "sqk", "fpills", "more", "livesw", "fd", "fdbody", "cs", "csbody", "planflt", "log",
            "flt", "clcard", "clprev", "clstrip", "clnext", "clname", "clsum", "clbody", "clnextbtn", "speech", "sptxt", "sptime",
            "banner", "rdata", "cfg", "simbrief", "mode", "clmode", "load", "info", "apt", "plancfg", "grid", "simstat",
            "simretry", "simmsg", "go", "end", "end2", "edit"}
    assert need <= ids(FLIGHT)
    assert not (ids(FLIGHT) - ids(INDEX) - REMOTE_ONLY), sorted(ids(FLIGHT) - ids(INDEX) - REMOTE_ONLY)  # nessun id inventato
    assert 'data-mode="remote"' in FLIGHT and "no-referrer" not in FLIGHT  # la politica e' nell'header, non nel markup


def test_flight_html_briefing_section_has_no_technical_or_local_only_controls():
    cfg = FLIGHT[FLIGHT.index('<section id="cfg">'):FLIGHT.index('<section id="flt"')]
    for local in ("tech", "thr", "simdll", "Calibrat", "Download", "deploc"):
        assert local not in cfg, local
    assert 'id="simretry"' in cfg and 'id="simstat"' in cfg and 'id="go"' in cfg


def test_flight_html_is_static_and_loads_only_whitelisted_files():
    assert not re.search(r"<style\b|\sstyle\s*=|\son[a-z]+\s*=|javascript:", FLIGHT, re.I)
    assert re.findall(r'<script\b[^>]*>', FLIGHT, re.I) == ['<script src="/app.js">']
    assert re.findall(r'(?:src|href)="(/[^"#?]*)"', FLIGHT) == ["/app.css", "/waiting", "/account", "/app.js"]
    assert "http://" not in FLIGHT and "https://" not in FLIGHT


def test_app_js_never_builds_html_from_data_and_has_no_inline_style():
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\"",
                "style.cssText", ".style.fontWeight", "javascript:"):
        assert bad not in JS, bad
    assert not re.search(r"\.style\.(?!setProperty)", JS)  # solo --sp-h via CSSOM (ammesso dalla CSP)
    assert "http://" not in JS and "https://" not in JS


def test_remote_controls_are_on_unless_the_pc_refuses_commands():
    assert "if(REMOTE&&body)return remoteApi(path,body)" in JS and "Read-only" not in JS
    for fn in ("function sqkStart(){if(sqkEdit||RO())return;", "function fStart(el,sel){if(RO()||"):
        assert fn in JS, fn
    assert "i.disabled=RO()" in JS and "b.disabled=RO()" in JS and "nb.hidden=RO()||!nx" in JS and "REMOTE||" not in JS.replace("view=REMOTE||s.phase", "")
    assert "const RO=()=>REMOTE&&!rCmd" in JS and "rCmd=d.cmd!==false" in JS and "Remote control is off on the PC" in JS


def test_polling_is_one_second_in_flight_and_three_when_not_connected_with_backoff_on_429():
    body = JS[JS.index("async function rpoll"):JS.index('$("load").onclick')]
    assert "wait=d.connected?1000:3000" in body and "let wait=3000" in body and "Retry-After" in body
    assert 'location.replace("/login")' in body and '"/api/flight/state"' in body and "cache:\"no-store\"" in body


def test_index_style_ids_exist_in_the_css():
    css = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
    for sel in ("#loadp", "#thrtitle", "#thrtext", "#thrwarn", "#threrr", "#thrbtns", "#thrmsg", "#thrst", "#simstat", "#endp",
                ".row>.f2", ".row>.f1", ".xs", "#thrclose", "#end", "#load"):
        assert sel in css, sel
        if sel.startswith("#"):
            assert 'id="%s"' % sel[1:].split(",")[0] in INDEX or sel in ("#thrclose,#end,#load",), sel


# ---------------------------------------------------------------- app.js remoto con node

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [jsPath, plan, idList] = [process.argv[2], JSON.parse(process.argv[3]), JSON.parse(process.argv[4])];
function mk(id) {
  const store = { textContent: "", hidden: false, className: "", disabled: false, dataset: {}, value: "", checked: false, id: id || "" };
  return new Proxy(function () {}, {
    get(t, p) {
      if (p === "__store") return store;
      if (typeof p === "symbol" || p === "then") return undefined;
      if (p in store) return store[p];
      if (p === "classList") return { add(c) { store.className += " " + c; }, toggle() {}, remove() {}, contains() { return false; } };
      return () => mk();
    },
    set(t, p, v) { store[p] = v; return true; },
    apply() { return mk(); },
  });
}
const els = {};
for (const id of idList) els[id] = mk(id);
const body = mk(); body.dataset = { mode: "remote" };
const timers = [], replaced = [], urls = [], errors = [];
const document = { body, getElementById: (id) => els[id], createElement: () => mk(), querySelector: () => null,
  querySelectorAll: () => ({ forEach() {} }), addEventListener() {}, documentElement: { style: { setProperty() {} } }, activeElement: null };
let n = 0;
const fetch = (url) => {
  if (url === "/api/auth/me")  // account data (CSRF token, SimBrief ID): not part of the state plan
    return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, json: () => Promise.resolve({ csrf_token: "T", simbrief_id: "SB" }), text: () => Promise.resolve("") });
  urls.push(url);
  const [status, bodyObj, headers] = plan[Math.min(n++, plan.length - 1)];
  return Promise.resolve({ ok: status >= 200 && status < 300, status, headers: { get: (k) => (headers || {})[k] || null },
    json: () => Promise.resolve(bodyObj), text: () => Promise.resolve("") });
};
const window = { scrollY: 0, scrollX: 0, scrollTo() {} };
const ctx = vm.createContext({ document, window, fetch, JSON, Promise, Date, Math, localStorage: { getItem: () => null, setItem() {} },
  setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; }, clearTimeout() {}, location: { replace: (p) => replaced.push(p) },
  confirm: () => false, console, Array, Object, String, Number, RegExp, parseFloat, parseInt, isFinite });
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  vm.runInContext(fs.readFileSync(jsPath, "utf8"), ctx);
  const out = [];
  const shot = () => out.push({ banner: els.banner.__store.textContent, bannerHidden: els.banner.__store.hidden, rdataHidden: els.rdata.__store.hidden,
    sim: els.sim.__store.textContent, clname: els.clname.__store.textContent, sptxt: els.sptxt.__store.textContent,
    log: els.log.__store.textContent });
  try {
    for (let i = 0; i < plan.length; i++) { await tick(); await tick(); await tick(); shot(); const k = timers.findIndex((t) => t[1] >= 1000); if (k < 0) break; const [f] = timers.splice(k, 1)[0]; f(); }
    await tick(); await tick(); await tick(); shot();
  } catch (e) { errors.push(String(e && e.stack || e)); }
  console.log(JSON.stringify({ out, replaced, urls, errors, waits: timers.filter((t) => t[1] >= 1000).map((t) => t[1]) }));
})();
"""


def run_remote(tmp_path, plan):
    script = tmp_path / "h.js"
    script.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run([NODE, str(script), str(ROOT / "web" / "app.js"), json.dumps(plan), json.dumps(sorted(ids(FLIGHT)))],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out


def flying_view(**over):
    v = snapshot.remote_view(make_state().snapshot())
    v.update(over)
    return v


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_remote_page_states(tmp_path):
    v = flying_view()
    plan = [[200, {"connected": False}], [200, {"connected": True, "seq": 0, "age": None, "spoke_ago": None, "snapshot": None}],
            [200, {"connected": True, "seq": 1, "epoch": 4, "age": 0.5, "spoke_ago": None, "snapshot": v}],
            [200, {"connected": True, "seq": 1, "age": 25.0, "spoke_ago": None, "snapshot": None, "unchanged": True}],
            [200, {"connected": True, "seq": 2, "age": 0.1, "spoke_ago": None, "snapshot": dict(v, phase="briefing")}],
            [200, {"connected": False}]]
    r = run_remote(tmp_path, plan)
    o = r["out"]
    assert o[0]["banner"] == "Program not connected" and o[0]["rdataHidden"] is True and o[0]["clname"] == ""
    assert "waiting for the first data" in o[1]["banner"] and o[1]["rdataHidden"] is True
    assert o[2]["bannerHidden"] is True and o[2]["rdataHidden"] is False and o[2]["clname"] == v["checklist"]["phase"]["name"]
    assert o[2]["sim"] == "SimConnect: demo" and "Gear up" in o[2]["log"] + o[2]["sptxt"]
    assert o[3]["banner"] == "No update for 25 s" and o[3]["rdataHidden"] is False
    assert o[4]["bannerHidden"] is True and o[4]["rdataHidden"] is False  # briefing: the page shows the briefing view (U3)
    assert o[5]["banner"] == "Program not connected" and o[5]["rdataHidden"] is True
    assert r["urls"][0] == "/api/flight/state" and r["urls"][1] == "/api/flight/state"
    assert r["urls"][3] == "/api/flight/state?seq=1&epoch=4"  # after a snapshot: only changes are asked for


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_remote_polling_intervals_401_and_429(tmp_path):
    v = flying_view()
    r = run_remote(tmp_path, [[200, {"connected": False}]])
    assert r["waits"] == [3000]
    r = run_remote(tmp_path, [[200, {"connected": True, "seq": 1, "age": 0.1, "spoke_ago": None, "snapshot": v}]])
    assert r["waits"] == [1000]
    r = run_remote(tmp_path, [[429, {"error": "rate_limited"}, {"Retry-After": "7"}]])
    assert r["waits"] == [7000]
    r = run_remote(tmp_path, [[401, {"error": "unauthenticated"}]])
    assert r["replaced"] == ["/login"] and r["waits"] == []
    r = run_remote(tmp_path, [[500, {}]])
    assert r["out"][0]["banner"].startswith("Connection lost") and r["waits"] == [3000]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_remote_render_survives_every_checklist_phase_and_hostile_text(tmp_path):
    views = []
    st = make_state(big=True)
    for p in st.cl.phases:
        try:
            st.set_cl_phase(p["id"])
        except ValueError:
            continue
        views.append(snapshot.remote_view(st.snapshot()))
    hostile = "<img src=x onerror=alert(1)>"
    bad = dict(views[0], log=[hostile] * 3, fields={"origin": hostile}, plan={"route": hostile, "sid": hostile,
               "waypoints": [{"ident": hostile, "via": hostile, "alt": 1}]})
    plan = [[200, {"connected": True, "seq": i + 1, "age": 0.1, "spoke_ago": 3.0, "snapshot": v}] for i, v in enumerate(views + [bad])]
    r = run_remote(tmp_path, plan)
    assert len(r["out"]) >= len(views) + 1
    for o in r["out"][:len(views) + 1]:  # an exception in render() would surface as "Connection lost"
        assert o["rdataHidden"] is False and o["bannerHidden"] is True and o["clname"], o
