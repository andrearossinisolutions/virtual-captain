"""Adattatore dei comandi della pagina remota (web/app.js, U3 consegna 1) provato con node su un DOM finto:
un solo comando alla volta, CSRF letto da /api/auth/me, errori con testi fissi, attesa dello snapshot reale,
conferma solo per "briefing", campi non confermati non sovrascritti. Senza node i test si saltano."""
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
JS_PATH = ROOT / "web" / "app.js"
JS = JS_PATH.read_text(encoding="utf-8")
FLIGHT = (ROOT / "server" / "public" / "flight.html").read_text(encoding="utf-8")
NODE = shutil.which("node") or next(iter(sorted(glob.glob(str(pathlib.Path.home() / ".nvm/versions/node/*/bin/node")))), None)
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const [jsPath, idList, scenario, viewJson] = [process.argv[2], JSON.parse(process.argv[3]), process.argv[4], JSON.parse(process.argv[5])];
function mk(id) {
  const store = { textContent: "", hidden: false, className: "", disabled: false, dataset: {}, value: "", checked: false, id: id || "" };
  return new Proxy(function () {}, {
    get(t, p) {
      if (p === "__store") return store;
      if (typeof p === "symbol" || p === "then") return undefined;
      if (p in store) return store[p];
      if (p === "classList") return { add(c) { store.className += " " + c; }, toggle(c, on) { store.toggled = [c, on]; }, remove() {}, contains() { return false; } };
      return () => mk();
    },
    set(t, p, v) { store[p] = v; return true; },
    apply() { return mk(); },
  });
}
const els = {};
for (const id of idList) els[id] = mk(id);
const body = mk(); body.dataset = { mode: "remote" };
const log = { fetches: [], replaced: [], reloaded: 0, confirms: [], timers: [] };
let fakeNow = 1_000_000, inflight = 0, maxInflight = 0;
const handlers = {};            // "METHOD url" -> (opts) => [status, obj, headers] | Promise
const resp = (status, obj, headers) => ({ ok: status >= 200 && status < 300, status, headers: { get: (k) => (headers || {})[k] || null },
  json: () => Promise.resolve(obj), text: () => Promise.resolve(JSON.stringify(obj)) });
const fetch = async (url, opts) => {
  opts = opts || {};
  const key = (opts.method || "GET") + " " + url.split("?")[0];
  log.fetches.push({ key, url, opts, body: opts.body ? JSON.parse(opts.body) : null });
  inflight++; maxInflight = Math.max(maxInflight, inflight);
  try {
    const h = handlers[key];
    if (!h) throw new Error("no handler for " + key);
    const [status, obj, headers] = await h(opts, url);
    return resp(status, obj, headers);
  } finally { inflight--; }
};
const document = { body, getElementById: (id) => els[id], createElement: () => mk(), querySelector: () => null,
  querySelectorAll: () => ({ forEach() {} }), addEventListener() {}, documentElement: { style: { setProperty() {} } }, activeElement: null };
let confirmAnswer = true;
const ctx = vm.createContext({ document, window: { scrollY: 0, scrollX: 0, scrollTo() {} }, fetch, JSON, Promise, Math,
  Date: { now: () => fakeNow }, localStorage: { getItem: () => null, setItem() {} },
  setTimeout: (f, ms) => { log.timers.push(ms); fakeNow += ms; setImmediate(f); return log.timers.length; }, clearTimeout() {},
  location: { replace: (p) => log.replaced.push(p), reload: () => { log.reloaded++; } },
  confirm: (t) => { log.confirms.push(t); return confirmAnswer; }, console, Array, Object, String, Number, RegExp, parseFloat, parseInt, isFinite, Error, TypeError, Set });
const run = (code) => vm.runInContext(code, ctx);
const tick = () => new Promise((r) => setImmediate(r));
const settle = async () => { for (let i = 0; i < 40; i++) await tick(); };
const state = (seq, extra) => ({ connected: true, seq, epoch: 1, age: 0.1, spoke_ago: null, cmd: true, snapshot: Object.assign({}, viewJson, extra || {}) });
const stateH = (list) => { let n = 0; return () => [200, list[Math.min(n++, list.length - 1)]]; };
global.__t = { els, log, handlers, resp, run, tick, settle, state, stateH, set confirmAnswer(v) { confirmAnswer = v; }, get maxInflight() { return maxInflight; },
  get fakeNow() { return fakeNow; } };
(async () => {
  const out = { errors: [] };
  try {
    run(fs.readFileSync(jsPath, "utf8").replace(/\nif\(REMOTE\)rstart\(\);else poll\(\);\s*$/, "\n"));  // senza avviare il polling: le prove lo guidano
    const f = new Function("t", "out", "return (async () => {" + scenario + "})()");
    await f(global.__t, out);
  } catch (e) { out.errors.push(String(e && e.stack || e)); }
  console.log(JSON.stringify(out));
})();
"""


def ids(html):
    return sorted(set(re.findall(r'\bid="([^"]+)"', html)))


def view(**over):
    v = snapshot.remote_view(make_state().snapshot())
    v.update(over)
    return v


def run_js(tmp_path, scenario, **over):
    script = tmp_path / "h.js"
    script.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run([NODE, str(script), str(JS_PATH), json.dumps(ids(FLIGHT)), scenario, json.dumps(view(**over))],
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out


# comune: stato iniziale del PC (seq 1), poi dopo il comando seq 2 con il campo cambiato
PRELUDE = """
const me = () => [200, {csrf_token: "TOK1", simbrief_id: "SB"}];
t.handlers["GET /api/auth/me"] = me;
"""


@needs_node
def test_command_posts_with_csrf_waits_for_the_next_snapshot_and_returns_it(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
let polls = 0;
t.handlers["GET /api/flight/state"] = () => { polls++; return [200, t.state(polls <= 1 ? 1 : 2, polls <= 1 ? {} : {fields: {v1: "150"}})]; };
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
await t.run("rfetch()");                               // the page already shows seq 1
const s = await t.run('api("/api/fields", {v1: "150"})');
const s2 = await t.run('api("/api/fields", {v2: "157"})');   // the second command: the token is cached
const posts = t.log.fetches.filter(f => f.opts.method === "POST");
out.r = {fieldsV1: s.fields.v1, rseq: t.run("rseq"), posts: posts.map(p => [p.key, p.body, p.opts.headers, p.opts.credentials]),
         me: t.log.fetches.filter(f => f.key === "GET /api/auth/me").length, err: t.els.rerr.__store.textContent};
""")
    r = out["r"]
    assert r["fieldsV1"] == "150" and r["rseq"] == 2  # the snapshot of the PC (new seq), not what the page assumed
    assert r["posts"][0] == ["POST /api/flight/cmd/fields", {"v1": "150"},
                             {"Content-Type": "application/json", "X-CSRF-Token": "TOK1"}, "same-origin"]
    assert r["posts"][1][1] == {"v2": "157"} and r["me"] == 1 and r["err"] == ""


@needs_node
def test_snapshot_is_awaited_for_at_most_five_seconds_and_the_unchanged_one_is_returned(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];       // the PC never produces a new snapshot (no-op command)
t.handlers["POST /api/flight/cmd/sim-retry"] = () => [200, {ok: true}];
await t.run("rfetch()");
const t0 = t.fakeNow;
const s = await t.run('api("/api/sim-retry", {})');
out.r = {same: s.phase === "flying", waited: t.fakeNow - t0, polls: t.log.fetches.filter(f => f.key === "GET /api/flight/state").length};
""")
    assert out["r"]["same"] is True and 4500 <= out["r"]["waited"] <= 6000 and out["r"]["polls"] >= 3


@needs_node
def test_csrf_is_read_again_once_on_403_then_the_page_reloads(tmp_path):
    out = run_js(tmp_path, """
let me = 0;
t.handlers["GET /api/auth/me"] = () => [200, {csrf_token: "TOK" + (++me)}];
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
let posts = 0;
t.handlers["POST /api/flight/cmd/fields"] = (o) => { posts++; return posts === 1 ? [403, {error: "forbidden"}] : [200, {ok: true}]; };
await t.run("rfetch()");
await t.run('api("/api/fields", {v1: "1"})');
const used = t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => f.opts.headers["X-CSRF-Token"]);
t.handlers["POST /api/flight/cmd/phase"] = () => [403, {error: "forbidden"}];
let rejected = false;
try { await t.run('api("/api/phase", {phase: "flying"})'); } catch (e) { rejected = true; }
out.r = {used, rejected, reloaded: t.log.reloaded, me, err: t.els.rerr.__store.textContent};
""")
    r = out["r"]
    assert r["used"][:2] == ["TOK1", "TOK2"] and r["rejected"] and r["reloaded"] == 1 and r["me"] == 3


ERROR_TABLE = [
    (409, {"error": "busy"}, "The program is busy, try again in a moment.", "fields"),
    (409, {"error": "program_offline"}, "Program not connected.", "fields"),
    (409, {"error": "commands_unsupported"}, "This program version cannot be controlled from the site: update it on the PC.", "fields"),
    (409, {"error": "commands_disabled"}, "Remote control is off on the PC.", "fields"),
    (409, {"error": "whatever"}, "The program cannot do that now.", "fields"),
    (429, {"error": "rate_limited"}, "Too many requests: wait a moment.", "fields"),
    (504, {"error": "timeout"}, "The PC did not answer; the page shows the PC's real state.", "fields"),
    (400, {"error": "invalid_request"}, "The program did not accept this change.", "fields"),
    (502, {"error": "command_failed"}, "The program could not do that.", "fields"),
    (502, {"error": "command_failed", "reason": "network"}, "SimBrief is not reachable.", "load-plan"),
    (502, {"error": "command_failed", "reason": "not_found"}, "SimBrief did not find this ID or username.", "load-plan"),
    (502, {"error": "command_failed", "reason": "bad_plan"}, "The SimBrief plan could not be read.", "load-plan"),
    (502, {"error": "command_failed", "reason": "network"}, "The program could not do that.", "fields"),  # only load-plan has reasons
    (404, {"error": "not_found"}, "This is not available from the site.", "fields"),
    (503, {"error": "commands_off"}, "Remote control is switched off on the server.", "fields"),
    (500, {"error": "boom <img src=x onerror=alert(1)>"}, "Request failed.", "fields"),
]


@needs_node
def test_errors_use_fixed_texts_never_the_server_text(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
const table = %s, res = [];
for (const [status, body, , name] of table) {
  t.handlers["POST /api/flight/cmd/" + name] = () => [status, body];
  let msg = null;
  try { await t.run('api("/api/' + name + '", {})'); } catch (e) { msg = e.message; }
  res.push([msg, t.els.rerr.__store.textContent, t.els.rerr.__store.hidden]);
}
out.res = res;
""" % json.dumps(ERROR_TABLE))
    for (status, body, text, _n), (msg, shown, hidden) in zip(ERROR_TABLE, out["res"]):
        assert msg == text and shown == text and hidden is False, (status, body)
        assert "<img" not in shown


@needs_node
def test_401_goes_to_login_and_a_dropped_network_is_a_fixed_text(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/fields"] = () => [401, {error: "unauthenticated"}];
try { await t.run('api("/api/fields", {v1: "1"})'); } catch (e) {}
const replaced = t.log.replaced.slice();
t.handlers["POST /api/flight/cmd/fields"] = () => { throw new TypeError("Failed to fetch https://evil"); };
let msg = null;
try { await t.run('api("/api/fields", {v1: "2"})'); } catch (e) { msg = e.message; }
out.r = {replaced, msg, shown: t.els.rerr.__store.textContent};
""")
    assert out["r"]["replaced"] == ["/login"] and out["r"]["shown"] == "Connection lost." and "evil" not in out["r"]["shown"]


@needs_node
def test_one_command_at_a_time_and_field_changes_merge(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
const gate = []; 
t.handlers["POST /api/flight/cmd/fields"] = () => new Promise(res => gate.push(() => res([200, {ok: true}])));
t.handlers["POST /api/flight/cmd/checklist-phase"] = () => [200, {ok: true}];
// three field edits in the same window: one request; a different command queues behind it
const p1 = t.run('api("/api/fields", {v1: "1"})'), p2 = t.run('api("/api/fields", {v2: "2"})'), p3 = t.run('api("/api/fields", {v1: "3"})');
const p4 = t.run('api("/api/checklist-phase", {step: 1})');
await t.settle(); await t.settle();
const before = t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => [f.key, f.body]);
gate.shift()();            // the PC answers the fields command
await Promise.all([p1, p2, p3, p4]);
// an edit made while a fields request is IN FLIGHT is a separate request (the first one already left)
const p5 = t.run('api("/api/fields", {v1: "5"})'); await t.settle();
const p6 = t.run('api("/api/fields", {v1: "6"})'); await t.settle();
gate.shift()(); await t.settle(); gate.shift()(); await Promise.all([p5, p6]);
const all = t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => [f.key, f.body]);
out.r = {before, all, maxInflight: t.maxInflight};
""")
    r = out["r"]
    assert r["before"] == [["POST /api/flight/cmd/fields", {"v1": "3", "v2": "2"}]]  # merged; phase waits its turn
    assert r["all"][1] == ["POST /api/flight/cmd/checklist-phase", {"step": 1}]
    assert [a[1] for a in r["all"][2:]] == [{"v1": "5"}, {"v1": "6"}]
    assert r["maxInflight"] == 1  # never two requests at once


@needs_node
def test_a_reset_does_not_merge_with_values_and_clearance_merges(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
t.handlers["POST /api/flight/cmd/clearance"] = () => [200, {ok: true}];
const ps = [t.run('api("/api/fields", {reset: ["v1"]})'), t.run('api("/api/fields", {v1: "9"})'),
            t.run('api("/api/clearance", {c: "EDDF"})'), t.run('api("/api/clearance", {t: "1200"})'), t.run('api("/api/clearance", {reset: true})')];
await Promise.all(ps);
out.r = t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => [f.key.replace("POST /api/flight/cmd/", ""), f.body]);
""")
    assert out["r"] == [["fields", {"reset": ["v1"]}], ["fields", {"v1": "9"}], ["clearance", {"c": "EDDF", "t": "1200"}],
                        ["clearance", {"reset": True}]]


@needs_node
def test_only_briefing_asks_for_confirmation(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/phase"] = () => [200, {ok: true}];
t.handlers["POST /api/flight/cmd/checklist-phase"] = () => [200, {ok: true}];
await t.run('api("/api/phase", {phase: "flying"})');
await t.run('api("/api/checklist-phase", {step: 1})');
const asked0 = t.log.confirms.length;
t.confirmAnswer = false;
let cancelled = null;
try { await t.run('api("/api/phase", {phase: "briefing"})'); } catch (e) { cancelled = [e.message, e.silent]; }
const postsAfterCancel = t.log.fetches.filter(f => f.key === "POST /api/flight/cmd/phase").length;
t.confirmAnswer = true;
await t.run('api("/api/phase", {phase: "briefing"})');
const phases = t.log.fetches.filter(f => f.key === "POST /api/flight/cmd/phase").map(f => f.body);
out.r = {asked0, confirms: t.log.confirms.length, cancelled, postsAfterCancel, phases, err: t.els.rerr.__store.textContent};
""")
    r = out["r"]
    assert r["asked0"] == 0 and r["confirms"] == 2 and r["cancelled"] == ["Cancelled", True]
    assert r["postsAfterCancel"] == 1 and r["phases"] == [{"phase": "flying"}, {"phase": "briefing", "confirm": True}]
    assert r["err"] == ""  # cancelling is not an error to show


@needs_node
def test_local_only_and_unknown_paths_never_leave_the_page(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
const res = [];
for (const p of ["/api/simdll-fetch", "/api/state", "/api/whatever", "/api/fetch_dll"]) {
  try { await t.run('api("' + p + '", {confirm: true})'); res.push("sent"); } catch (e) { res.push(e.message); }
}
out.r = {res, fetches: t.log.fetches.length};
""")
    assert out["r"]["fetches"] == 0 and set(out["r"]["res"]) == {"This is not available from the site."}


@needs_node
def test_control_characters_are_removed_before_sending(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/clearance"] = () => [200, {ok: true}];
await t.run('api("/api/clearance", ' + JSON.stringify({r: "A B\\nC\\u0000D"}) + ')');
out.r = t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => f.body);
""")
    assert out["r"] == [{"r": "A B C D"}]


@needs_node
def test_unconfirmed_fields_are_kept_while_the_command_is_in_flight_then_the_snapshot_wins(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
let release;
t.handlers["POST /api/flight/cmd/fields"] = () => new Promise(res => { release = () => res([200, {ok: true}]); });
t.run('inpDirty = {v1: "777"}; inpSend()');
await t.settle(); await t.tick();
await new Promise(r => setTimeout(r, 20)); await t.settle();
const during = t.run('fval("v1")');
const dirtyCleared = t.run('JSON.stringify(inpDirty)');
release(); await t.settle(); await new Promise(r => setTimeout(r, 20)); await t.settle();
const kept = t.run('fval("v1")');                          // response arrived, snapshot still without the change: kept (no jump back)
t.run("rPend.v1.t = -100000; syncInputs(last)");           // expired: the snapshot wins
out.r = {during, dirtyCleared, kept, after: t.run('fval("v1")'), pend: t.run("JSON.stringify(rPend)")};
""")
    r = out["r"]
    assert r["during"] == "777" and r["dirtyCleared"] == "{}" and r["kept"] == "777" and r["pend"] == "{}"
    assert r["after"] != "777"  # the PC's snapshot (without the change) is the truth once the value expires


@needs_node
def test_when_the_pc_refuses_commands_the_page_is_read_only(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
const st = t.state(1); st.cmd = false;
t.handlers["GET /api/flight/state"] = () => [200, st];
await t.run("rfetch()");
out.r = {ro: t.run("RO()"), banner: t.els.banner.__store.textContent, prev: t.els.clprev.__store.disabled, next: t.els.clnext.__store.disabled,
         nextbtn: t.els.clnextbtn.__store.hidden, toggled: t.els.main.__store.toggled || null};
const st2 = t.state(2); st2.cmd = true;
t.handlers["GET /api/flight/state"] = () => [200, st2];
await t.run("rfetch()");
out.r2 = {ro: t.run("RO()"), banner: t.els.banner.__store.textContent, bannerHidden: t.els.banner.__store.hidden};
""")
    r = out["r"]
    assert r["ro"] is True and r["banner"] == "Remote control is off on the PC" and r["prev"] is True and r["next"] is True and r["nextbtn"] is True
    assert out["r2"]["ro"] is False and out["r2"]["bannerHidden"] is True


@needs_node
def test_an_old_late_snapshot_does_not_replace_a_newer_one(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(5, {log: ["NEW"]})];
await t.run("rfetch()");
t.handlers["GET /api/flight/state"] = () => [200, t.state(3, {log: ["OLD"]})];
await t.run("rfetch()");
out.r = {seq: t.run("rseq"), log: t.run("rlast.log[0]")};
""")
    assert out["r"] == {"seq": 5, "log": "NEW"}


def test_adapter_source_rules():
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in JS, bad
    body = JS[JS.index("// ---- comandi della pagina remota"):JS.index('$("load").onclick')]
    assert "textContent" in body and "credentials:\"same-origin\"" in body and '"X-CSRF-Token":tok' in body
    assert "http://" not in body and "https://" not in body
    assert body.count('"/api/flight/cmd/"') == 1  # a single place builds the URL
    for local in ("simdll",):
        assert local not in body
    assert "confirm(" in body and body.count("confirm(") == 1


@needs_node
def test_briefing_phase_shows_the_briefing_view_and_default_view_follows_the_phase(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1, {phase: "briefing", info: "Plan loaded", simbrief: "SB1"})];
await t.run("rfetch()");
const a = {banner: t.els.banner.__store.textContent, hidden: t.els.rdata.__store.hidden, view: t.run("view"), cfg: t.els.cfg.__store.hidden,
           flt: t.els.flt.__store.hidden, info: t.els.info.__store.textContent};
t.handlers["GET /api/flight/state"] = () => [200, t.state(2, {phase: "flying"})];
await t.run("rfetch()");
out.r = {a, view2: t.run("view"), cfg2: t.els.cfg.__store.hidden, flt2: t.els.flt.__store.hidden};
""")
    r = out["r"]
    assert r["a"] == {"banner": "", "hidden": False, "view": "config", "cfg": False, "flt": True, "info": "Plan loaded"}
    assert r["view2"] == "flight" and r["cfg2"] is True and r["flt2"] is False


@needs_node
def test_only_modified_fields_are_sent_and_the_simbrief_id_never_goes_with_fields(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
t.run('rDirty.set("v1", {value: "150"}); rDirty.set("mode", {value: "training"})');
await t.run("rSave()");
t.run('rDirty.clear()'); 
await t.run("rSave()");   // nothing modified: nothing sent
const posts = t.log.fetches.filter(f => f.key === "POST /api/flight/cmd/fields").map(f => f.body);
out.r = {posts, pre: t.run("rPre"), body: (t.run('rDirty.set("simbrief", {value: "X"}); rDirty.set("v1", {value: "1"})'), t.run("JSON.stringify(rBody())"))};
""")
    r = out["r"]
    assert r["posts"] == [{"v1": "150", "mode": "training"}] and r["pre"] == "SB" and r["body"] == '{"v1":"1"}'


@needs_node
def test_end_flight_asks_for_confirmation_and_sends_the_confirm_flag(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/phase"] = () => [200, {ok: true}];
t.confirmAnswer = false;
await t.els.end.__store.onclick();
const n0 = t.log.fetches.filter(f => f.key === "POST /api/flight/cmd/phase").length;
t.confirmAnswer = true;
await t.els.end.__store.onclick();
out.r = {n0, posts: t.log.fetches.filter(f => f.key === "POST /api/flight/cmd/phase").map(f => f.body), confirms: t.log.confirms.length};
""")
    r = out["r"]
    assert r["n0"] == 0 and r["posts"] == [{"phase": "briefing", "confirm": True}] and r["confirms"] == 2


@needs_node
def test_load_plan_uses_the_typed_id_and_the_field_is_not_overwritten(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1, {simbrief: "OLD"})];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
t.handlers["POST /api/flight/cmd/load-plan"] = () => [200, {ok: true}];
t.els.simbrief.__store.value = "TYPED";
await t.els.simbrief.__store.oninput();
t.run('rDirty.set("v1", {value: "150"})');
await t.els.load.__store.onclick();
out.r = {posts: t.log.fetches.filter(f => f.key.startsWith("POST")).map(f => [f.key, f.body]), dirty: t.run('rDirty.has("simbrief")')};
""")
    r = out["r"]
    assert r["posts"][-1] == ["POST /api/flight/cmd/load-plan", {"simbrief": "TYPED"}]
    assert ["POST /api/flight/cmd/fields", {"v1": "150"}] in r["posts"] and r["dirty"] is False


@needs_node
def test_failed_fields_stay_dirty_and_old_program_without_labels_gets_a_hint(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()");
t.handlers["POST /api/flight/cmd/fields"] = () => [409, {error: "busy"}];
t.run('rDirty.set("v1", {value: "150"})');
await t.run("rSave()");
const kept = t.run('rDirty.has("v1")');
t.handlers["GET /api/flight/state"] = () => [200, t.state(2, {phase: "briefing", labels: []})];
await t.run("rfetch()");
out.r = {kept, banner: t.els.banner.__store.textContent, hidden: t.els.rdata.__store.hidden};
""")
    assert out["r"] == {"kept": True, "banner": "Update the program on the PC to edit the briefing remotely", "hidden": True}


@needs_node
def test_rebuild_is_deferred_while_an_inline_field_is_edited_and_the_pure_functions(tmp_path):
    out = run_js(tmp_path, """
const R = {};
R.can = [t.run("canRebuild(false,false,null,0)"), t.run("canRebuild(true,false,null,0)"), t.run("canRebuild(true,true,null,0)"),
         t.run("canRebuild(true,false,1000,1000+RB_IDLE-1)"), t.run("canRebuild(true,false,1000,1000+RB_IDLE)"), t.run("canRebuild(true,false,null,99999)")];
R.pend = [t.run('pendDone("27012","27012",false,0)'), t.run('pendDone("27012","27/012",false,0)'), t.run('pendDone("27012","27012",true,0)'),
          t.run('pendDone("27012","",false,10000)'), t.run('pendDone("27012","",true,99999)')];
// isEditing: campo inline attivo (data-f / data-c), valori non ancora inviati
R.edit = [t.run("isEditing()")];
t.run('document.activeElement = {dataset: {f: "atis_wind"}}'); R.edit.push(t.run("isEditing()"));
t.run('document.activeElement = {dataset: {c: "f"}}'); R.edit.push(t.run("isEditing()"));
t.run('document.activeElement = {dataset: {k: "origin"}}'); R.edit.push(t.run("isEditing()"));
t.run('document.activeElement = null; inpDirty = {v1: "15"}'); R.edit.push(t.run("isEditing()"));
t.run('inpDirty = {}'); R.edit.push(t.run("isEditing()"));
out.r = R;
""")
    r = out["r"]
    assert r["can"] == [True, False, True, False, True, False]  # idle time since the LAST activity, not since the first deferral
    assert r["pend"] == [True, False, False, True, False]  # snapshot has the value / expired, never while the field is active
    assert r["edit"] == [False, True, True, False, True, False]


@needs_node
def test_checklist_body_is_not_rebuilt_while_editing_and_rebuilt_after(tmp_path):
    out = run_js(tmp_path, """
const R = {};
const cl = (txt, ph) => ({cl_phase: ph || "p1", cl_phases: [{id: "p1", name: "P1"}, {id: "p2", name: "P2"}],
  checklist: {phase: {name: "P1"}, bad: 0, subphases: [{name: "", items: [{text: txt, status: "ok", type: "check", kind: "ok", detail: ""}]}]}});
t.run("last = " + JSON.stringify(Object.assign({}, cl("A"), {fields: {}})));
t.run("checklist(last)"); R.k1 = t.run("clBodyKey");
t.run('document.activeElement = {dataset: {f: "atis_wind"}}');
t.run("last = " + JSON.stringify(Object.assign({}, cl("B"), {fields: {}})));
t.run("checklist(last)"); R.kEdit = t.run("clBodyKey") === R.k1; R.pending = t.run("rbSince") !== null;
t.run("flushRebuild()"); R.kStill = t.run("clBodyKey") === R.k1;          // still editing: nothing
t.run('document.activeElement = null; flushRebuild()');                  // blur: rebuild with the latest state
R.kAfter = t.run("clBodyKey") !== R.k1; R.clear = t.run("rbSince") === null;
// phase change while editing: rebuilds anyway
t.run('document.activeElement = {dataset: {f: "atis_wind"}}');
t.run("last = " + JSON.stringify(Object.assign({}, cl("B", "p2"), {fields: {}}))); const k2 = t.run("clBodyKey");
t.run("checklist(last)"); R.phase = t.run("clBodyKey") !== k2;
out.r = R;
""")
    r = out["r"]
    assert r["kEdit"] and r["pending"] and r["kStill"] and r["kAfter"] and r["clear"] and r["phase"]


@needs_node
def test_rpend_survives_the_response_until_the_snapshot_has_the_value(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
const R = {};
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
t.run("last = {fields: {v1: '100'}}");
await t.run('api("/api/fields", {v1: "150"})');
t.run("inpDirty = {v1: '150'}; inpSend()");
await t.settle();
R.afterResp = t.run("fval('v1')");                    // snapshot still old: the sent value is kept (no jump back)
t.run("last = {fields: {v1: '150'}}; syncInputs(last)"); R.afterSnap = [t.run("'v1' in rPend"), t.run("fval('v1')")];
out.r = R;
""")
    r = out["r"]
    assert r["afterResp"] == "150" and r["afterSnap"] == [False, "150"]


@needs_node
def test_rbact_postpones_the_forced_rebuild_while_the_user_keeps_typing(tmp_path):
    out = run_js(tmp_path, """
const R = {};
const cl = (txt, bad) => ({cl_phase: "p1", cl_phases: [{id: "p1", name: "P1"}], fields: {},
  checklist: {phase: {name: "P1"}, bad: bad || 0, subphases: [{name: "", items: [{text: txt, status: "ok", type: "check", kind: "ok", detail: ""}]}]}});
t.run("last = " + JSON.stringify(cl("A"))); t.run("checklist(last)"); R.k1 = t.run("clBodyKey");
t.run('document.activeElement = {dataset: {f: "atis_wind"}}');
t.run("last = " + JSON.stringify(cl("B", 1))); t.run("checklist(last)");
R.header = t.run("$('clsum').textContent");                 // body deferred: header stays coherent with the old body
t.run("rbSince = Date.now() - 60000; rbAct = Date.now() - 1000");   // deferred long ago but typed 1 s ago
t.run("checklist(last)"); R.keptWhileTyping = t.run("clBodyKey") === R.k1;
t.run("rbAct = Date.now() - RB_IDLE - 1");                   // idle long enough
t.run("checklist(last)"); R.rebuilt = t.run("clBodyKey") !== R.k1; R.header2 = t.run("$('clsum').textContent");
out.r = R;
""")
    r = out["r"]
    assert r["keptWhileTyping"] and r["rebuilt"]
    assert r["header"] == "0 out of spec" and r["header2"] == "1 out of spec"


@needs_node
def test_rpend_prune_ttl_failure_and_normalised_value(tmp_path):
    out = run_js(tmp_path, PRELUDE + """
const R = {};
t.run("last = {fields: {v1: '100'}}; rPend.v1 = {v: '150', t: Date.now()}; rPrune()"); R.fresh = t.run("'v1' in rPend");     // snapshot old, not expired: kept
t.run("rPend.v1.t = Date.now() - PEND_TTL"); t.run("rPrune()"); R.ttl = t.run("'v1' in rPend");                              // expired: dropped
t.run("rPend.v1 = {v: '150', t: Date.now()}; last.fields.v1 = '150'; document.activeElement = {dataset: {f: 'v1'}}; rPrune()"); R.active = t.run("'v1' in rPend");
t.run("document.activeElement = null; rPrune()"); R.confirmed = t.run("'v1' in rPend");
// the PC refuses the command: the value is released at once
t.handlers["POST /api/flight/cmd/fields"] = () => [409, {error: "busy"}];
t.run("last = {fields: {v1: '100'}}; inpDirty = {v1: '999'}; inpSend()"); R.during = t.run("fval('v1')");
await t.settle(); R.afterFail = t.run("fval('v1')");
// the PC normalises: the shown value follows the answer
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
t.handlers["GET /api/flight/state"] = () => [200, t.state(5, {fields: {v1: "150"}})];
t.run("inpDirty = {v1: '15'}; inpSend()"); await t.settle(); R.normalised = t.run("fval('v1')");
out.r = R;
""")
    r = out["r"]
    assert [r["fresh"], r["ttl"], r["active"], r["confirmed"]] == [True, False, True, False]
    assert r["during"] == "999" and r["afterFail"] == "100"
    assert r["normalised"] == "150", r


@needs_node
def test_now_text_names_every_failed_light_and_keeps_semicolons(tmp_path):
    out = run_js(tmp_path, """
out.r = [t.run('nowText("Nav & logo: expected NAV / LOGO, actual OFF")'),
         t.run('nowText("Landing L: expected OFF / RETRACT, actual ON; Beacon: expected OFF, actual ON; Nav & logo: expected NAV / LOGO, actual OFF")'),
         t.run('nowText("expected 5, actual 3")'), t.run('nowText("")'), t.run('nowText("boh")'),
         t.run('nowText("Radio: expected 118.0, actual 121.5;COM2; Beacon: expected OFF, actual ON")'),
         t.run('nowText("Beacon: expected OFF, actual ON; stray note")')];
""")
    assert out["r"] == ["Nav & logo OFF", "Landing L ON \u00b7 Beacon ON \u00b7 Nav & logo OFF", "3", "", "",
                        "Radio 121.5;COM2 \u00b7 Beacon ON", "Beacon ON; stray note"]


WIND_SETUP = """
t.run('var __ae = null; document.activeElement = null; var __i = {inputMode: "numeric", noEnd: false, isConnected: true, value: "270", events: [], failFocus: false,'
      + 'blur() { this.events.push("blur:" + this.noEnd); if (document.activeElement === this) document.activeElement = null; },'
      + 'focus() { this.events.push("focus"); if (!this.failFocus) document.activeElement = this; },'
      + 'onblur() { if (!this.noEnd) this.events.push("end"); }, setSelectionRange() {}};'
      + 'var __c = {dataset: {f: "atis_wind"}, querySelector: () => __i}; document.activeElement = __i;');
"""


@needs_node
def test_wind_keyboard_toggle_refocuses_after_the_event_without_closing(tmp_path):
    out = run_js(tmp_path, WIND_SETUP + """
const R = {};
R.mode0 = [t.run("!!windTxt.atis_wind"), t.run("__i.inputMode")];
t.run("windKb(__c)");
R.sync1 = [t.run("windTxt.atis_wind"), t.run("__i.inputMode"), t.run("__i.events.join()")];   // nothing happens synchronously inside the event
await t.settle();
R.mode1 = [t.run("__i.events.join()"), t.run("document.activeElement === __i"), t.run("__i.noEnd")];
t.run("__i.events = []; windKb(__c)"); await t.settle();
R.mode2 = [t.run("windTxt.atis_wind"), t.run("__i.inputMode"), t.run("document.activeElement === __i")];
out.r = R;
""")
    r = out["r"]
    assert r["mode0"] == [False, "numeric"]
    assert r["sync1"] == [True, "text", ""]
    assert r["mode1"] == ["blur:true,focus", True, False]   # the blur happens with noEnd set: the field is not closed
    assert r["mode2"] == [False, "numeric", True]


@needs_node
def test_wind_keyboard_toggle_closes_the_field_cleanly_when_focus_is_lost(tmp_path):
    out = run_js(tmp_path, WIND_SETUP + """
t.run("__i.failFocus = true; windKb(__c)"); await t.settle();
out.r = [t.run("__i.events.join()"), t.run("__i.noEnd")];
""")
    # blur (guarded), two focus attempts, then the clean close; never an orphan input
    assert out["r"] == ["blur:true,focus,focus,end", False]


def test_wind_field_is_numeric_by_default_with_a_keyboard_toggle():
    assert 'i.inputMode=isA&&sf==="wind"?(windTxt[k]?"text":"numeric")' in JS
    assert 'w.className="plus un kb"' in JS and "function windKb" in JS
