import json
import pathlib
import tempfile
import urllib.request

import web
from state import State


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r) if "json" in r.headers["Content-Type"] else r.read().decode()


def test_api_flow():
    cfg = pathlib.Path(tempfile.mkdtemp()) / "cfg.json"
    st = State(cfg)
    srv = web.serve(st, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert "Virtual Captain" in call(base, "/")
        s = call(base, "/api/state")
        assert s["phase"] == "briefing" and s["fields"]["ta"] == ""
        s = call(base, "/api/fields", {"ta": "6000", "accel_agl": "1000", "mode": "silent", "simbrief": "x"})
        assert s["fields"]["ta"] == "6000" and s["mode"] == "silent"
        b = st.briefing()
        assert (b.ta, b.accel_agl, b.mode) == (6000, 1000, "silent")
        assert call(base, "/api/phase", {"phase": "flying"})["phase"] == "flying"
        # si ricordano solo ID, modalità e accelerazione
        saved = json.loads(cfg.read_text())
        assert saved == {"accel_agl": "1000", "simbrief": "x", "mode": "silent"}
        assert State(cfg).fields["ta"] == "" and State(cfg).fields["accel_agl"] == "1000"
    finally:
        srv.shutdown()


def test_bad_values_use_defaults():
    st = State(pathlib.Path(tempfile.mkdtemp()) / "c.json")
    st.update({"ta": "abc", "accel_agl": ""})
    b = st.briefing()
    assert (b.ta, b.accel_agl) == (5000, 1500)


if __name__ == "__main__":
    test_api_flow()
    test_bad_values_use_defaults()
    print("ok")
