"""FLEET.md 8 fixtures: the tag server fleet endpoints end to end.

A real ThreadingHTTPServer with tagserver.H runs on a free localhost port. The PLC is a
fake on localhost that speaks real Modbus TCP or S7comm (see conftest.py). The historian
is a mocked psycopg2, so no database is needed. The base plant poll does not run here.
"""
import json
import queue
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import fleet
import tagserver
from conftest import free_port, sample_image

ENROLL_KEY = "test-enroll-key"
WRITE_TOKEN = "test-write-token"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=10) as r:
            raw, code, hdrs = r.read(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, code, hdrs = e.read(), e.code, e.headers
    text = raw.decode()
    ctype = hdrs.get("Content-Type", "")
    return code, (json.loads(text) if ctype.startswith("application/json") else text), hdrs


def _enroll_body(name, kind, port, sha="aa" * 32, machines=None, cell="packaging"):
    protocol = {"kind": kind, "host": "127.0.0.1", "port": port}
    if kind == "s7comm":
        protocol.update(rack=0, slot=1, db=1)
    return {
        "name": name, "profile": "generic-iec" if kind == "modbus" else "siemens-s7-1200",
        "protocol": protocol,
        "task": {"name": "packaging-cell", "title": "Packaging cell sequencer", "sha256": sha,
                 "interval_ms": 100},
        "cell": {"name": cell, "rail": "psu-c",
                 "machines": machines or ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1"]},
        "io_extra": [{"asset": "SYS", "signal": "PACK_COUNT", "address": "%MW20", "unit": "count"}],
        "runtime": {"version": "0.1", "started_at": 1758000000.0},
    }


def _dev(name):
    return {"X-Device-Token": fleet.enroll_token(ENROLL_KEY, name)}


SCADA = {"X-Scada-Token": WRITE_TOKEN}


def _plc(base, name):
    code, body, _ = _call(base + "/fleet")
    assert code == 200
    return next((p for p in body if p["name"] == name), None)


def _wait(fn, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


def _all_good(base, name):
    p = _plc(base, name)
    return p if p and p["connected"] and all(t["quality"] == "GOOD" for t in p["tags"]) else None


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(tagserver, "ENROLL_KEY", ENROLL_KEY)
    monkeypatch.setattr(tagserver, "WRITE_TOKEN", WRITE_TOKEN)
    monkeypatch.setattr(tagserver, "FLEET_POLL_S", 0.1)
    monkeypatch.setattr(tagserver, "psycopg2", None)
    monkeypatch.setattr(tagserver, "_HIST_Q", queue.Queue(maxsize=600))
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), tagserver.H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        with tagserver._fleet_lock:
            entries = list(tagserver.FLEET.values())
            tagserver.FLEET.clear()
        for e in entries:
            e["stop"].set()
            e["thread"].join(5)
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- Modbus e2e --
def test_enroll_poll_write_delete_over_modbus(server, fake_modbus):
    name = "plc-packaging"
    body = _enroll_body(name, "modbus", fake_modbus.port)

    # a wrong device token is refused
    code, out, _ = _call(server + "/enroll", "POST", body, {"X-Device-Token": "00" * 32})
    assert code == 401 and out["enrolled"] is False
    assert _plc(server, name) is None

    # enroll: 31 tags, one poll thread
    code, out, _ = _call(server + "/enroll", "POST", body, _dev(name))
    assert (code, out) == (200, {"enrolled": True, "tags": 31, "poll": "started"})

    # the poll becomes GOOD with measured values from the fake PLC
    plc = _wait(lambda: _all_good(server, name))
    assert plc["protocol"]["kind"] == "modbus" and plc["protocol"]["port"] == fake_modbus.port
    assert plc["enrolled_at"] <= plc["first_good_at"] <= plc["last_good_at"]
    assert plc["poll_rtt_ms"] > 0
    tags_ = {t["tag"]: t for t in plc["tags"]}
    assert tags_["FLEET.PLC_PACKAGING.PACK_WRAPPER_1.AMPS"]["value"] == 14.2
    assert tags_["FLEET.PLC_PACKAGING.SYS.SCAN_MS"]["value"] == 0.21
    code, _, hdrs = _call(server + "/fleet")
    assert hdrs["X-Fleet-Enroll"] == "enabled"

    code, dom, _ = _call(server + "/domains")
    assert dom == {"domains": {"plc:plc-packaging": [
        "pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1", "plc-packaging"]}}
    code, text, _ = _call(server + "/metrics/fleet")
    assert code == 200
    assert 'scada_plc_connected{namespace="fleet",pod="plc-packaging"} 1' in text
    assert 'vplc_state{namespace="fleet",pod="plc-packaging"} 1' in text
    assert 'scada_tags_good{namespace="fleet",pod="plc-packaging"} 31' in text

    # a heartbeat with the same task hash keeps the thread and first_good_at
    with tagserver._fleet_lock:
        thread_before = tagserver.FLEET[name]["thread"]
    code, out, _ = _call(server + "/enroll", "POST", body, _dev(name))
    assert (code, out["poll"]) == (200, "kept")
    with tagserver._fleet_lock:
        assert tagserver.FLEET[name]["thread"] is thread_before
    assert _plc(server, name)["first_good_at"] == plc["first_good_at"]

    # the historian gets fleet rows in the plant_tags shape
    batch = tagserver._HIST_Q.get(timeout=5)
    assert "FLEET.PLC_PACKAGING.SYS.SCAN_MS" in batch

    # write allowed: a setpoint goes over Modbus into %MW11
    derate = "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT"
    code, out, _ = _call(server + f"/fleet/{name}/write", "POST", {"tag": derate, "value": 55}, SCADA)
    assert code == 200 and out["written"] is True and out["address"] == "%MW11" and out["raw"] == 55
    assert fake_modbus.mw(11) == 55
    _wait(lambda: next(t for t in _plc(server, name)["tags"] if t["tag"] == derate)["value"] == 55.0)

    # write denied
    denied = [
        ({"tag": "FLEET.PLC_PACKAGING.SYS.SCAN_MS", "value": 1}, SCADA, 403),
        ({"tag": "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.AMPS", "value": 1}, SCADA, 403),
        ({"tag": "FLEET.PLC_PACKAGING.SYS.PACK_COUNT", "value": 1}, SCADA, 403),
        ({"tag": "FLEET.PLC_PACKAGING.NOPE.DERATE_PCT", "value": 1}, SCADA, 403),
        ({"tag": derate, "value": 40}, {"X-Scada-Token": "wrong"}, 401),
        ({"tag": derate, "value": 40}, {}, 401),
        ({"tag": derate, "value": 150}, SCADA, 400),
        ({"tag": derate}, SCADA, 400),
    ]
    for wbody, hdrs, want in denied:
        code, out, _ = _call(server + f"/fleet/{name}/write", "POST", wbody, hdrs)
        assert code == want, (wbody, hdrs, code, out)
        assert out["written"] is False
    assert fake_modbus.mw(11) == 55                          # nothing else reached the PLC
    assert fake_modbus.mw(0) == 21
    code, _, _ = _call(server + "/fleet/plc-nobody/write", "POST", {"tag": derate, "value": 1}, SCADA)
    assert code == 404

    # delete: the token is required, then the poll stops and the tags go
    code, _, _ = _call(server + f"/fleet/{name}", "DELETE", headers={"X-Scada-Token": "wrong"})
    assert code == 401
    with tagserver._fleet_lock:
        thread = tagserver.FLEET[name]["thread"]
    code, out, _ = _call(server + f"/fleet/{name}", "DELETE", headers=SCADA)
    assert (code, out) == (200, {"deleted": True, "name": name})
    thread.join(5)
    assert not thread.is_alive()
    assert _call(server + "/fleet")[1] == []
    assert _call(server + "/domains")[1] == {"domains": {}}
    assert _call(server + "/metrics/fleet")[1] == ""
    assert _call(server + f"/fleet/{name}", "DELETE", headers=SCADA)[0] == 404


def test_new_task_hash_restarts_the_poll(server, fake_modbus):
    name = "plc-packaging"
    body = _enroll_body(name, "modbus", fake_modbus.port, sha="aa" * 32)
    assert _call(server + "/enroll", "POST", body, _dev(name))[1]["poll"] == "started"
    first = _wait(lambda: _all_good(server, name))
    with tagserver._fleet_lock:
        old_thread = tagserver.FLEET[name]["thread"]
    time.sleep(0.05)
    body2 = _enroll_body(name, "modbus", fake_modbus.port, sha="bb" * 32)
    code, out, _ = _call(server + "/enroll", "POST", body2, _dev(name))
    assert (code, out["poll"]) == (200, "restarted")
    old_thread.join(5)
    assert not old_thread.is_alive()
    second = _wait(lambda: _all_good(server, name))
    assert second["task"]["sha256"] == "bb" * 32
    assert second["first_good_at"] > first["first_good_at"]


def test_enroll_rejects_bad_bodies(server):
    code, out, _ = _call(server + "/enroll", "POST", {"name": "not-a-plc"}, {})
    assert code == 400
    body = _enroll_body("plc-x", "modbus", 1502)
    body["protocol"]["kind"] = "profinet"
    code, out, _ = _call(server + "/enroll", "POST", body, _dev("plc-x"))
    assert code == 400 and "protocol.kind" in out["error"]
    req = urllib.request.Request(server + "/enroll", data=b"{not json", method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        _OPENER.open(req, timeout=5)
    assert e.value.code == 400
    assert _call(server + "/nope", "POST", {})[0] == 404
    assert _call(server + "/nope", "DELETE")[0] == 404


def test_unreachable_plc_stays_bad_and_disconnected(server, monkeypatch):
    name = "plc-gone"
    port = free_port()                                        # nothing listens here
    code, out, _ = _call(server + "/enroll", "POST", _enroll_body(name, "modbus", port), _dev(name))
    assert code == 200
    plc = _wait(lambda: (lambda p: p if p and p["poll_error"] else None)(_plc(server, name)), 20)
    assert plc["connected"] is False and plc["first_good_at"] is None
    assert all(t["quality"] == "BAD" and t["value"] is None for t in plc["tags"])
    text = _call(server + "/metrics/fleet")[1]
    assert 'scada_plc_connected{namespace="fleet",pod="plc-gone"} 0' in text
    assert "scada_poll_rtt_ms" not in text and "vplc_state" not in text


# -------------------------------------------------------------------- S7 e2e --
def test_enroll_poll_write_over_s7(server, fake_s7):
    name = "plc-stamping"
    body = _enroll_body(name, "s7comm", fake_s7.port, machines=["press-1", "press-2"],
                        cell="stamping")
    body["io_extra"] = []
    code, out, _ = _call(server + "/enroll", "POST", body, _dev(name))
    assert (code, out) == (200, {"enrolled": True, "tags": 22, "poll": "started"})
    plc = _wait(lambda: _all_good(server, name))
    tags_ = {t["tag"]: t for t in plc["tags"]}
    assert tags_["FLEET.PLC_STAMPING.PRESS_1.AMPS"]["value"] == 9.1
    assert tags_["FLEET.PLC_STAMPING.STAMPING.CELL_ENABLE"]["value"] == 1.0
    code, out, _ = _call(server + f"/fleet/{name}/write", "POST",
                         {"tag": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "value": 55}, SCADA)
    assert code == 200 and out["address"] == "%MW10"
    assert fake_s7.mw(10) == 55
    code, out, _ = _call(server + f"/fleet/{name}/write", "POST",
                         {"tag": "FLEET.PLC_STAMPING.STAMPING.CELL_ENABLE", "value": 0}, SCADA)
    assert code == 200 and fake_s7.mw(8) == 0
    code, text, _ = _call(server + "/metrics/fleet")
    assert 'vplc_scan_time_ms{namespace="fleet",pod="plc-stamping"} 0.2100' in text


# ---------------------------------------------------------- fail closed ----
def test_empty_enroll_key_disables_enrollment(server, monkeypatch):
    monkeypatch.setattr(tagserver, "ENROLL_KEY", "")
    name = "plc-packaging"
    hmac_of_empty = {"X-Device-Token": fleet.enroll_token("", name)}
    code, out, _ = _call(server + "/enroll", "POST", _enroll_body(name, "modbus", 1502), hmac_of_empty)
    assert code == 403 and "disabled" in out["error"]
    code, body, hdrs = _call(server + "/fleet")
    assert (code, body, hdrs["X-Fleet-Enroll"]) == (200, [], "disabled")


def test_empty_write_token_disables_writes_and_deletes(server, fake_modbus, monkeypatch):
    name = "plc-packaging"
    assert _call(server + "/enroll", "POST", _enroll_body(name, "modbus", fake_modbus.port),
                 _dev(name))[0] == 200
    monkeypatch.setattr(tagserver, "WRITE_TOKEN", "")
    derate = "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT"
    for hdrs in ({"X-Scada-Token": ""}, {}, SCADA):
        code, out, _ = _call(server + f"/fleet/{name}/write", "POST", {"tag": derate, "value": 55}, hdrs)
        assert code == 403 and "disabled" in out["error"]
        assert _call(server + f"/fleet/{name}", "DELETE", headers=hdrs)[0] == 403
    assert fake_modbus.mw(11) == 100


# ------------------------------------------------------------ historian ----
def test_fleet_rows_go_to_plant_tags_with_the_plant_row_shape(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            calls.append(("execute", sql))

        def executemany(self, sql, rows):
            calls.append(("executemany", sql, list(rows)))

    class Conn:
        autocommit = False

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(tagserver, "psycopg2", types.SimpleNamespace(connect=lambda dsn: Conn()))
    monkeypatch.setitem(tagserver._DB, "conn", None)
    monkeypatch.setitem(tagserver._DB, "win_rows", 0)
    monkeypatch.setitem(tagserver.STATE, "historian",
                        {"connected": False, "rows_total": 0, "rows_per_s": 0.0})
    reg = fleet.parse_enrollment(_enroll_body("plc-packaging", "modbus", 502))
    fresh = fleet.decode(fleet.table_for(reg), sample_image(), ts=1758000000.5)

    tagserver._historian_write(fresh)

    assert calls[0][0] == "execute" and "CREATE TABLE IF NOT EXISTS plant_tags" in calls[0][1]
    inserts = [c for c in calls if c[0] == "executemany"]
    assert len(inserts) == 1
    sql, rows = inserts[0][1], inserts[0][2]
    assert sql == ("INSERT INTO plant_tags (ts, tag, value, quality) "
                   "VALUES (to_timestamp(%s), %s, %s, %s)")
    assert len(rows) == 31
    assert all(len(r) == 4 and isinstance(r[0], float) and r[1].startswith("FLEET.PLC_PACKAGING.")
               and isinstance(r[2], float) and r[3] == "GOOD" for r in rows)
    assert (1758000000.5, "FLEET.PLC_PACKAGING.PACK_WRAPPER_1.DERATE_PCT", 100.0, "GOOD") in rows
    assert (1758000000.5, "FLEET.PLC_PACKAGING.SYS.SCAN_MS", 0.21, "GOOD") in rows
    assert tagserver.STATE["historian"]["rows_total"] == 31
    assert tagserver.STATE["historian"]["connected"] is True


def test_history_step_writes_a_batch_and_backs_off_after_a_failure(monkeypatch):
    written = []
    q = queue.Queue(maxsize=10)
    monkeypatch.setattr(tagserver, "_HIST_Q", q)
    monkeypatch.setattr(tagserver, "psycopg2", types.SimpleNamespace(connect=None))
    monkeypatch.setitem(tagserver.STATE, "historian",
                        {"connected": True, "rows_total": 0, "rows_per_s": 0.0})
    monkeypatch.setattr(tagserver, "_historian_write", lambda batch: written.append(batch))
    assert tagserver.fleet_history_step(backoff_s=0.5, timeout=0.01) is False      # empty queue
    q.put({"a": 1})
    t0 = time.perf_counter()
    assert tagserver.fleet_history_step(backoff_s=0.5, timeout=1) is True
    assert written == [{"a": 1}] and time.perf_counter() - t0 < 0.4                # no backoff
    tagserver.STATE["historian"]["connected"] = False                               # the DB is down
    q.put({"b": 2})
    t0 = time.perf_counter()
    assert tagserver.fleet_history_step(backoff_s=0.5, timeout=1) is True
    assert written[-1] == {"b": 2} and time.perf_counter() - t0 >= 0.45             # backed off


def test_full_historian_queue_drops_the_oldest_batch(monkeypatch):
    q = queue.Queue(maxsize=2)
    monkeypatch.setattr(tagserver, "_HIST_Q", q)
    monkeypatch.setitem(tagserver._HIST_DROPPED, "batches", 0)
    for i in range(4):
        tagserver._fleet_history({"n": i})
    assert [q.get_nowait(), q.get_nowait()] == [{"n": 2}, {"n": 3}]
    assert tagserver._HIST_DROPPED["batches"] == 2
