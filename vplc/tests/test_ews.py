"""PS4A injector (SCENARIOS 2.4): the rogue-ews token gate and a real S7 write.

The write test runs the in-process vPLC S7 server used by the protocol tests (conftest.py,
test_protocols.py). After POST /fault/PS4A the runtime's %MW10 reads the injected value, the same
register a sanctioned SCADA write would move. ews.py never imports the runtime: it only speaks S7comm.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import ews
from conftest import free_port, make_runtime, program
from s7_server import S7Db1Server

SRC = program("    ready AT %IX0.0 : BOOL;\n    run AT %QX0.0 : BOOL;\n    spd AT %QW0 : INT;\n"
              "    der AT %MW10 : INT := 100;",
              "run := ready;\nIF run THEN spd := LIMIT(0, der, 100); ELSE spd := 0; END_IF;")

TOKEN = "test-write-token"
SCADA = {"X-Scada-Token": TOKEN}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=10) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    text = raw.decode()
    return code, (json.loads(text) if text.startswith("{") else text)


@pytest.fixture
def ews_server(monkeypatch):
    monkeypatch.setattr(ews, "WRITE_TOKEN", TOKEN)
    monkeypatch.setattr(ews, "STATE", {"active": False, "last_write_ts": None, "last_error": None})
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ews.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_ews_post_token_gate(ews_server, monkeypatch):
    # an empty token disables the POST endpoints (403), fail closed
    monkeypatch.setattr(ews, "WRITE_TOKEN", "")
    for path in ("/fault/PS4A", "/reset"):
        code, out = _call(ews_server + path, "POST", {}, SCADA)
        assert code == 403 and out["active"] is False
    # a bad or missing token is 401
    monkeypatch.setattr(ews, "WRITE_TOKEN", TOKEN)
    assert _call(ews_server + "/fault/PS4A", "POST", {}, {"X-Scada-Token": "wrong"})[0] == 401
    assert _call(ews_server + "/reset", "POST", {}, {})[0] == 401
    # the injector never turned on, and it never tried a PLC write
    code, st = _call(ews_server + "/state")
    assert code == 200 and st["active"] is False and st["last_error"] is None


def test_healthz(ews_server):
    code, body = _call(ews_server + "/healthz")
    assert code == 200 and body == "ok\n"


def test_fault_ps4a_writes_mw10_over_s7(ews_server, monkeypatch):
    rt = make_runtime(SRC, profile="siemens-s7-1200")
    rt.run()
    port = free_port()
    srv = S7Db1Server(rt, "127.0.0.1", port, "plc-stamping")
    srv.start()
    try:
        monkeypatch.setattr(ews, "TARGET", "127.0.0.1")
        monkeypatch.setattr(ews, "PORT", port)
        monkeypatch.setattr(ews, "BYTE", 292)            # DB1.DBW292 = %MW10
        monkeypatch.setattr(ews, "VALUE", 30)

        assert rt.read("MW", 10, 1) == [100]             # the task default before the fault

        code, out = _call(ews_server + "/fault/PS4A", "POST", {}, SCADA)
        assert code == 200 and out["active"] is True
        assert out["address"] == "DB1.DBW292" and out["value"] == 30 and out["last_write_ts"]

        rt.cycle()                                       # apply the queued %MW write
        assert rt.read("MW", 10, 1) == [30]              # the rogue setpoint landed on press-1
        assert rt.denied_writes == 0                     # byte 292 is inside %MW, nothing denied

        # reset clears the flag and does not touch the PLC
        code, out = _call(ews_server + "/reset", "POST", {}, SCADA)
        assert code == 200 and out["active"] is False
        rt.cycle()
        assert rt.read("MW", 10, 1) == [30]              # the value the reset left in place
    finally:
        srv.stop()


def test_fault_ps4a_reports_502_when_the_plc_is_unreachable(ews_server, monkeypatch):
    monkeypatch.setattr(ews, "TARGET", "127.0.0.1")
    monkeypatch.setattr(ews, "PORT", free_port())        # nothing listens here
    code, out = _call(ews_server + "/fault/PS4A", "POST", {}, SCADA)
    assert code == 502 and "S7 write failed" in out["error"]
    code, st = _call(ews_server + "/state")
    assert st["active"] is False and st["last_error"]
