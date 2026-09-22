"""PS6 (SCENARIOS 2.7): the tag-server memory-leak injector and the historian queue fields.

A real ThreadingHTTPServer with tagserver.H runs on a free localhost port. No PLC and no database
are needed here: the base poll and the historian loop are not started, so the tests only touch the
in-memory chaos flag and the /tags historian block.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import tagserver
from conftest import free_port

WRITE_TOKEN = "test-write-token"
SCADA = {"X-Scada-Token": WRITE_TOKEN}
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
    return code, json.loads(raw.decode())


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(tagserver, "WRITE_TOKEN", WRITE_TOKEN)
    monkeypatch.setattr(tagserver, "_HIST_Q", queue.Queue(maxsize=600))
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), tagserver.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        tagserver.chaos_reset(WRITE_TOKEN)               # stop any leak thread the test started
        httpd.shutdown()
        httpd.server_close()


def test_chaos_post_token_gate(server, monkeypatch):
    # an empty write token disables the endpoints (403), fail closed
    monkeypatch.setattr(tagserver, "WRITE_TOKEN", "")
    for path in ("/chaos/leak", "/chaos/reset"):
        code, out = _call(server + path, "POST", {}, SCADA)
        assert code == 403 and out["active"] is False
    # a bad token is 401
    monkeypatch.setattr(tagserver, "WRITE_TOKEN", WRITE_TOKEN)
    for path in ("/chaos/leak", "/chaos/reset"):
        code, out = _call(server + path, "POST", {}, {"X-Scada-Token": "wrong"})
        assert code == 401
    # a missing token is 401 too
    code, out = _call(server + "/chaos/leak", "POST", {}, {})
    assert code == 401
    # the flag never turned on
    assert _call(server + "/chaos")[1]["active"] is False


def test_leak_grows_then_reset_frees(server):
    # start a tiny leak so the buffer grows a little and quickly
    code, out = _call(server + "/chaos/leak", "POST", {"mib_per_s": 0.05}, SCADA)
    assert code == 200 and out["active"] is True and out["mib_per_s"] == 0.05

    deadline = time.time() + 5
    leaked = 0.0
    while time.time() < deadline:
        state = _call(server + "/chaos")[1]
        leaked = state["leaked_mib"]
        if leaked > 0:
            break
        time.sleep(0.05)
    assert leaked > 0, "leak did not grow"
    assert state["active"] is True and state["started_at"] is not None

    # reset stops the thread and frees the buffer
    code, out = _call(server + "/chaos/reset", "POST", {}, SCADA)
    assert code == 200 and out["reset"] is True
    state = _call(server + "/chaos")[1]
    assert state == {"active": False, "leaked_mib": 0.0, "mib_per_s": 0.05, "started_at": None}


def test_leak_rate_is_clamped_to_the_band(server):
    code, out = _call(server + "/chaos/leak", "POST", {"mib_per_s": 999}, SCADA)
    assert code == 200 and out["mib_per_s"] == 8.0          # clamped to the max
    code, out = _call(server + "/chaos/leak", "POST", {"mib_per_s": 0.0001}, SCADA)
    assert code == 200 and out["mib_per_s"] == 0.05         # clamped to the min
    code, out = _call(server + "/chaos/leak", "POST", {}, SCADA)
    assert code == 200 and out["mib_per_s"] == 0.5          # the default when the body omits it


def test_tags_historian_block_has_queue_fields(server):
    code, out = _call(server + "/tags")
    assert code == 200
    hist = out["historian"]
    assert {"queue_depth", "queue_max", "dropped_batches"} <= set(hist)
    assert hist["queue_max"] == 600
    assert hist["queue_depth"] == 0 and hist["dropped_batches"] == 0
