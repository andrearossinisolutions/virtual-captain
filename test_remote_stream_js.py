"""Client SSE della pagina remota (web/app.js, U4 consegna C5) provato con node: EventSource finto, orologio e timer virtuali,
stesso DOM finto di test_remote_commands_js.py. Snapshot, delta (d e rm) sul raw, epoch, seq inatteso, ricreazione manuale con
backoff e ?last, fallback a polling (5 s, 3 errori, 429/404, niente EventSource), ritorno a SSE dopo 2 minuti, silenzio 35 s,
offline, age estrapolata, rAwait con lo stream. Senza node i test si saltano."""
from __future__ import annotations

import json
import os
import re
import subprocess

from test_remote_commands_js import FLIGHT, HARNESS, JS, JS_PATH, NODE, ids, needs_node, view

OLD_TIMER = "  setTimeout: (f, ms) => { log.timers.push(ms); fakeNow += ms; setImmediate(f); return log.timers.length; }, clearTimeout() {},"
assert OLD_TIMER in HARNESS and "const ctx = vm.createContext({" in HARNESS and "global.__t = { els," in HARNESS
SCHED = r"""
const sched = { q: [], id: 0,
  set(f, ms) { const id = ++sched.id; sched.q.push({ id, f, at: fakeNow + (ms || 0) }); return id; },
  clear(id) { sched.q = sched.q.filter((x) => x.id !== id); },
  async advance(ms) {
    const end = fakeNow + ms;
    for (;;) {
      sched.q.sort((a, b) => a.at - b.at || a.id - b.id);
      const n = sched.q[0];
      if (!n || n.at > end) break;
      sched.q.shift(); fakeNow = Math.max(fakeNow, n.at); n.f();
      for (let i = 0; i < 15; i++) await new Promise((r) => setImmediate(r));
    }
    fakeNow = end; for (let i = 0; i < 15; i++) await new Promise((r) => setImmediate(r));
  } };
class ES {
  constructor(url) { this.url = url; this.readyState = 0; this.l = {}; this.closed = false; ES.all.push(this); }
  addEventListener(n, f) { this.l[n] = f; }
  close() { this.closed = true; this.readyState = 2; }
  emit(n, data, id) { this.readyState = 1; if (id !== undefined) this.lid = id; this.l[n]({ data: typeof data === "string" ? data : JSON.stringify(data), lastEventId: this.lid || "" }); }
  fail(closed) { this.readyState = closed ? 2 : 0; if (this.onerror) this.onerror({}); }
}
ES.all = [];
"""
H = (HARNESS.replace(OLD_TIMER, "  setTimeout: sched.set, clearTimeout: sched.clear, EventSource: process.env.NO_ES ? undefined : ES, AbortController,")
     .replace("const ctx = vm.createContext({", SCHED + "const ctx = vm.createContext({")
     .replace("global.__t = { els,", "global.__t = { sched, ES, els,"))

PRELUDE = """
Math.random = () => 0.5;                                    // jitter neutral: delays are the nominal ones
const last = () => t.ES.all[t.ES.all.length - 1];
const SNAP = t.state(1).snapshot; delete SNAP.dll_missing;  // a key that norm() would add: never in the raw
const snap = (extra) => Object.assign({}, SNAP, extra || {});
const bn = () => t.els.banner.__store.textContent;
const urls = () => t.ES.all.map((e) => e.url);
const polls = () => t.log.fetches.filter((f) => f.key === "GET /api/flight/state").length;
const meta = (o) => Object.assign({connected: true, age: 0.2, spoke_ago: null, cmd: true}, o || {});
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
t.handlers["GET /api/auth/me"] = () => [200, {csrf_token: "TOK1", simbrief_id: "SB"}];
"""


def run(tmp_path, scenario, no_es=False):
    script = tmp_path / "h.js"
    script.write_text(H, encoding="utf-8")
    env = dict(os.environ, **({"NO_ES": "1"} if no_es else {}))
    res = subprocess.run([NODE, str(script), str(JS_PATH), json.dumps(ids(FLIGHT)), PRELUDE + scenario, json.dumps(view())],
                         capture_output=True, text=True, timeout=90, env=env)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out["r"] if "r" in out else out


@needs_node
def test_snapshot_then_delta_with_d_and_rm_merge_on_the_raw_not_on_the_normalised(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()");
const es = last();
es.emit("snapshot", snap({info: "ONE", zz: 1}), "7.1"); es.emit("meta", meta());
const a = {info: t.els.info.__store.textContent, seq: t.run("rseq"), epoch: t.run("repoch"), rawHasDefault: "dll_missing" in t.run("rraw"), shown: !t.els.rdata.__store.hidden, bn: bn()};
es.emit("delta", {d: {info: "TWO", fields: {v1: "150"}}, rm: ["zz"]}, "7.3");
out.r = {a, info: t.els.info.__store.textContent, seq: t.run("rseq"), raw: JSON.parse(JSON.stringify(t.run("rraw"))),
         v1: t.run("rlast.fields.v1"), zz: t.run("rlast.zz"), defaultInNorm: t.run("rlast.dll_missing"), urls: urls()};
""")
    assert r["a"] == {"info": "ONE", "seq": 1, "epoch": 7, "rawHasDefault": False, "shown": True, "bn": ""}
    assert r["info"] == "TWO" and r["seq"] == 3 and r["v1"] == "150" and r.get("zz") is None and r["defaultInNorm"] is False
    assert r["raw"]["info"] == "TWO" and "zz" not in r["raw"] and "dll_missing" not in r["raw"]
    assert r["urls"] == ["/api/flight/stream"]  # first opening: no ?last


@needs_node
def test_a_different_epoch_replaces_everything_and_a_delta_of_the_old_epoch_is_doubtful(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()");
let es = last();
es.emit("snapshot", snap({info: "OLD", zz: 1}), "7.5");
es.emit("snapshot", snap({info: "NEW"}), "9.1");           // another program connection: lower seq, accepted
const a = {info: t.els.info.__store.textContent, ep: t.run("repoch"), seq: t.run("rseq"), zz: "zz" in t.run("rraw")};
es.emit("delta", {d: {info: "X"}, rm: []}, "7.6");          // epoch of the previous connection: not trusted
await t.sched.advance(1500);
out.r = {a, closed: es.closed, urls: urls(), info: t.els.info.__store.textContent};
""")
    assert r["a"] == {"info": "NEW", "ep": 9, "seq": 1, "zz": False}
    assert r["closed"] is True and r["urls"] == ["/api/flight/stream", "/api/flight/stream"] and r["info"] == "NEW"


@needs_node
def test_unexpected_seq_or_dubious_data_reopens_without_last(tmp_path):
    r = run(tmp_path, """
const res = [];
for (const bad of [["delta", {d: {}, rm: []}, "7.2"],        // seq not newer
                   ["delta", {d: {}, rm: []}, "8.9"],        // other epoch
                   ["delta", {d: {}, rm: []}, "garbage"],    // id not "<epoch>.<seq>"
                   ["delta", {d: [], rm: []}, "7.9"],        // d not an object
                   ["delta", {d: {}}, "7.9"],                // rm missing
                   ["delta", "not json", "7.9"],
                   ["snapshot", snap(), ""]]) {              // snapshot without id
  await t.run("rstart()");
  const es = last(); es.emit("snapshot", snap({info: "A"}), "7.2"); es.emit("meta", meta());
  const n = t.ES.all.length;
  es.emit(...bad); await t.sched.advance(1500);
  res.push([es.closed, t.ES.all.length - n, last().url]);
}
const first = t.ES.all.length;                                // delta before any snapshot
await t.run("rstart()"); last().emit("delta", {d: {}, rm: []}, "7.1"); await t.sched.advance(1500);
res.push([last().url, t.ES.all.length - first]);
out.r = res;
""")
    for closed, n, url in r[:-1]:
        assert closed is True and n == 1 and url == "/api/flight/stream"  # complete snapshot: no ?last
    assert r[-1][0] == "/api/flight/stream" and r[-1][1] == 2


@needs_node
def test_closed_stream_is_recreated_by_hand_with_backoff_and_last_then_falls_back_after_three_errors(tmp_path):
    r = run(tmp_path, """
t.handlers["GET /api/flight/stream"] = () => [503, {error: "x"}];
await t.run("rstart()");
const es = last(); es.emit("snapshot", snap(), "7.3"); es.emit("meta", meta());
es.fail(true); await t.sched.advance(0);                      // 503: EventSource is CLOSED and does not retry by itself (the probe settles)
await t.sched.advance(900);
const early = t.ES.all.length; await t.sched.advance(300);     // 1 s nominal
const second = last();
second.fail(true); await t.sched.advance(0); await t.sched.advance(1500);   // 2 s: not yet
const early2 = t.ES.all.length; await t.sched.advance(600);
const third = last(); third.fail(true); await t.sched.advance(10);
out.r = {early, n2: early2, urls: urls(), mode: t.run("rmode"), bannerShown: bn(), polling: polls()};
await t.sched.advance(100);
out.r.polling2 = polls();
""")
    assert r["early"] == 1 and r["n2"] == 2 and r["urls"] == ["/api/flight/stream", "/api/flight/stream?last=7.3", "/api/flight/stream?last=7.3"]
    assert r["mode"] == "poll" and r["polling2"] >= 1  # third error: polling


@needs_node
def test_http_status_of_the_stream_is_read_with_a_probe_429_and_404_go_to_polling_401_to_login(tmp_path):
    r = run(tmp_path, """
const res = {};
for (const st of [429, 404, 401]) {
  t.ES.all.length = 0; t.log.replaced.length = 0;
  t.handlers["GET /api/flight/stream"] = () => [st, {}];
  await t.run("rstart()"); last().fail(true); await t.sched.advance(10);
  res[st] = {mode: t.run("rmode"), n: t.ES.all.length, replaced: t.log.replaced.slice()};
}
out.r = res;
""")
    assert r["429"]["mode"] == "poll" and r["429"]["n"] == 1 and r["404"]["mode"] == "poll" and r["404"]["n"] == 1
    assert r["401"]["replaced"] == ["/login"]


@needs_node
def test_no_event_within_five_seconds_falls_back_to_polling(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()");
await t.sched.advance(4900); const before = {mode: t.run("rmode"), p: polls()};
await t.sched.advance(200);
out.r = {before, mode: t.run("rmode"), closed: last().closed, polled: polls() >= 1, info: t.els.info.__store.textContent !== undefined};
""")
    assert r["before"] == {"mode": "sse", "p": 0} and r["mode"] == "poll" and r["closed"] is True and r["polled"] is True


@needs_node
def test_three_errors_while_connecting_fall_back_without_probe(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); const es = last(); es.emit("meta", meta());
es.fail(false); es.fail(false); const m1 = t.run("rmode"); es.fail(false);
await t.sched.advance(10);
out.r = {m1, mode: t.run("rmode"), probes: t.log.fetches.filter((f) => f.key === "GET /api/flight/stream").length};
""")
    assert r == {"m1": "sse", "mode": "poll", "probes": 0}  # CONNECTING: the browser retries by itself, no new object


@needs_node
def test_without_eventsource_the_page_polls(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); await t.sched.advance(10);
out.r = {mode: t.run("rmode"), polls: polls(), timers: t.sched.q.length};
""", no_es=True)
    assert r["mode"] == "poll" and r["polls"] >= 1


@needs_node
def test_sse_is_tried_again_after_two_minutes_and_polling_stops_when_it_works(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); await t.sched.advance(5100);          // no event: polling
const m0 = t.run("rmode"); await t.sched.advance(100000);
const n1 = t.ES.all.length; await t.sched.advance(21000);       // 2 min after the fallback: trial (no ?last: the raw is not kept while polling)
const trial = last(); const n2 = t.ES.all.length;
trial.fail(true); await t.sched.advance(10);                    // trial fails: still polling, next trial in 2 min
const m1 = t.run("rmode"); await t.sched.advance(121000);
const t2 = last(); const n3 = t.ES.all.length;
t2.emit("snapshot", snap({info: "VIA SSE"}), "7.1"); t2.emit("meta", meta());
const m2 = t.run("rmode"); const p0 = polls(); await t.sched.advance(10000);
out.r = {m0, n1, n2, m1, n3, m2, stopped: polls() === p0, urls: urls(), info: t.els.info.__store.textContent};
""")
    assert (r["m0"], r["n1"], r["n2"], r["m1"], r["n3"], r["m2"]) == ("poll", 1, 2, "poll", 3, "sse")
    assert r["stopped"] is True and r["info"] == "VIA SSE" and set(r["urls"]) == {"/api/flight/stream"}


@needs_node
def test_total_silence_for_35_seconds_shows_connection_lost_and_reconnects_with_last(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); const es = last();
es.emit("snapshot", snap(), "7.4"); es.emit("meta", meta({age: 0}));
await t.sched.advance(34900); const before = {bn: bn(), n: t.ES.all.length};
await t.sched.advance(200); const after = {bn: bn(), closed: es.closed};
await t.sched.advance(1500);
out.r = {before, after, urls: urls()};
""")
    assert r["before"]["n"] == 1 and "Connection lost" not in r["before"]["bn"]
    assert r["after"] == {"bn": "Connection lost…", "closed": True}
    assert r["urls"] == ["/api/flight/stream", "/api/flight/stream?last=7.4"]


@needs_node
def test_any_event_resets_the_silence_timer(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); const es = last(); es.emit("snapshot", snap(), "7.1");
for (let i = 0; i < 12; i++) { await t.sched.advance(5000); es.emit("meta", meta()); }   // 60 s with a meta every 5 s
out.r = {n: t.ES.all.length, closed: es.closed, mode: t.run("rmode")};
""")
    assert r == {"n": 1, "closed": False, "mode": "sse"}


@needs_node
def test_offline_event_or_disconnected_meta_show_program_not_connected_and_a_new_snapshot_recovers(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); const es = last();
es.emit("snapshot", snap({info: "A"}), "7.1"); es.emit("meta", meta());
es.emit("offline", {}); const a = {bn: bn(), hidden: t.els.rdata.__store.hidden, seq: t.run("rseq")};
es.emit("meta", meta({connected: false, age: null, cmd: false}));
const b = bn();
es.emit("snapshot", snap({info: "B"}), "12.1"); es.emit("meta", meta());
const c = {bn: bn(), hidden: t.els.rdata.__store.hidden, info: t.els.info.__store.textContent};
es.emit("meta", meta({connected: false})); es.emit("delta", {d: {info: "C"}, rm: []}, "12.2");   // delta without raw: reopen in full
await t.sched.advance(1500);
out.r = {a, b, c, urls: urls()};
""")
    assert r["a"] == {"bn": "Program not connected", "hidden": True, "seq": None} and r["b"] == "Program not connected"
    assert r["c"] == {"bn": "", "hidden": False, "info": "B"} and r["urls"] == ["/api/flight/stream"] * 2


@needs_node
def test_age_and_spoke_ago_are_extrapolated_between_meta_events(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); const es = last();
es.emit("snapshot", snap(), "7.1"); es.emit("meta", meta({age: 2, spoke_ago: 3}));
await t.sched.advance(10000); es.emit("meta", meta({age: 12, spoke_ago: 13})); const a = bn();   // meta says 12 s: no banner
await t.sched.advance(9000); const b = bn();                                                      // 21 s extrapolated, no event
es.emit("delta", {d: {info: "X"}, rm: []}, "7.2"); const c = bn();                                  // a delta: the program was heard just now
out.r = {a, b, c, ago: t.run("rlast.spoke_ago")};
""")
    assert r["a"] == "" and re.fullmatch(r"No update for 2[01] s", r["b"]) and r["c"] == "" and r["ago"] >= 22


@needs_node
def test_rawait_with_the_stream_resolves_on_the_next_seq_without_polling(tmp_path):
    r = run(tmp_path, """
t.handlers["POST /api/flight/cmd/fields"] = () => [200, {ok: true}];
await t.run("rstart()"); const es = last(); es.emit("snapshot", snap({fields: {v1: ""}}), "7.1"); es.emit("meta", meta());
const p = t.run('api("/api/fields", {v1: "150"})');
let done = false; p.then(() => { done = true; });
await t.sched.advance(800);                                                          // merge window, POST sent
const posted = t.log.fetches.filter((f) => f.key === "POST /api/flight/cmd/fields").length, waiting = !done;
es.emit("delta", {d: {fields: {v1: "150"}}, rm: []}, "7.2");
const s = await p;
// no new snapshot: the wait ends after at most 5 s
const p2 = t.run('api("/api/fields", {v2: "157"})'); let done2 = false; p2.then(() => { done2 = true; });
await t.sched.advance(800); const t0 = t.fakeNow; await t.sched.advance(4000); const still = !done2; await t.sched.advance(1500);
const s2 = await p2;
out.r = {posted, waiting, v1: s.fields.v1, still, done2, v1b: s2.fields.v1, polls: polls()};
""")
    assert r == {"posted": 1, "waiting": True, "v1": "150", "still": True, "done2": True, "v1b": "150", "polls": 0}


@needs_node
def test_rawait_in_polling_mode_is_unchanged_and_late_polling_answers_are_dropped_once_on_sse(tmp_path):
    r = run(tmp_path, """
await t.run("rstart()"); await t.sched.advance(5100);                               // polling
const es = null;
t.handlers["GET /api/flight/state"] = () => [200, t.state(1)];
await t.run("rfetch()"); const a = t.run("rseq");
t.ES.all.length = 0; await t.sched.advance(120000);                                 // trial
const tr = last(); tr.emit("snapshot", snap({info: "S"}), "5.1");
await t.run("rfetch()");                                                            // a poll answer arriving after the switch
out.r = {a, info: t.els.info.__store.textContent, ep: t.run("repoch"), raw: t.run("rraw") !== null};
""")
    assert r == {"a": 1, "info": "S", "ep": 5, "raw": True}


def test_static_rules_of_the_stream_client():
    assert JS.count("new EventSource(") == 1 and "innerHTML" not in JS and "outerHTML" not in JS and "eval(" not in JS
    assert "if(REMOTE)rstart();else poll();" in JS
    blk = JS[JS.index("// ---- stream SSE"):JS.index("// ---- comandi della pagina remota")]
    assert "http://" not in blk and "https://" not in blk and "document." not in blk and "localStorage" not in blk
    used = set(re.findall(r'\$\("([^"]+)"\)', blk))
    assert used <= set(ids(FLIGHT)), used          # ids used by the block are all in flight.html
    assert not re.search(r"<script(?![^>]*\bsrc=)", FLIGHT) and not re.search(r"\son\w+=|style=", FLIGHT)  # no inline code
    assert "/api/state" not in blk and "/api/fields" not in blk  # the local mode is untouched
    assert 'dataset.mode==="remote"' in JS.split("\n")[0]


@needs_node
def test_app_js_syntax_with_node_check():
    res = subprocess.run([NODE, "--check", str(JS_PATH)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
