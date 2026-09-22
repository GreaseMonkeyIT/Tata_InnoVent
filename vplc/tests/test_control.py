"""Control HTTP (FLEET.md 6) and the enrollment client."""
import json
import time
import urllib.error
import urllib.request

import pytest

from conftest import make_runtime, program
from control import ControlServer
from enroll import Enroller

GOOD = program("    y AT %QW0 : INT;", "y := 7;")


def call(port, method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Device-Token"] = token
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.startswith("{") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


@pytest.fixture
def ctl():
    rt = make_runtime(GOOD, {"task": "seven"})
    rt.run()
    srv = ControlServer(rt, None, "secret", "127.0.0.1", 0)
    srv.start()
    yield rt, srv.port
    srv.stop()


def test_healthz_and_state(ctl):
    rt, port = ctl
    assert call(port, "GET", "/healthz")[0] == 200
    code, st = call(port, "GET", "/state")
    assert code == 200 and st["state"] == "RUN" and st["task"]["name"] == "seven"
    assert set(st) >= {"name", "profile", "scan", "started_at", "enrollment", "fault", "denied_writes"}


def test_control_writes_need_the_device_token(ctl):
    rt, port = ctl
    assert call(port, "POST", "/stop")[0] == 401
    assert call(port, "POST", "/stop", token="wrong")[0] == 401
    code, body = call(port, "POST", "/stop", token="secret")
    assert code == 200 and body["state"] == "STOP"
    assert call(port, "POST", "/run", token="secret")[1]["state"] == "RUN"


def test_task_load_compiles_first_and_keeps_the_old_task_on_error(ctl):
    rt, port = ctl
    code, body = call(port, "PUT", "/task", {"st": "PROGRAM x VAR a : INT; END_VAR a := TRUE; END_PROGRAM"},
                      token="secret")
    assert code == 400 and body["line"] == 1 and body["col"]
    assert rt.task.name == "seven"
    code, body = call(port, "PUT", "/task", {"st": program("    y AT %QW0 : INT;", "y := 9;"),
                                             "manifest": {"task": "nine"}}, token="secret")
    assert code == 200 and body["task"]["name"] == "nine"
    rt.cycle()
    assert rt.image.qw[0] == 9


def test_enrollment_body_headers_and_kick(capture):
    rt = make_runtime(GOOD, {"task": "seven", "cell": {"name": "c1", "rail": "psu-c", "machines": [
        {"name": "pack-conveyor-1", "prefix": "pack-conveyor"}]}}, profile="generic-iec")
    en = Enroller(rt, capture.url, "tok-1", "plc-test.fleet.svc.cluster.local", 502, heartbeat_s=60)
    rt.on_event = en.kick
    en.start()
    try:
        rt.run()                                         # first RUN kicks an enrollment
        assert capture.wait_for(1)
        req = capture.requests[0]
        assert req["headers"].get("X-Device-Token") == "tok-1"
        body = req["body"]
        assert body["name"] == "plc-test" and body["profile"] == "generic-iec"
        assert body["protocol"]["kind"] == "modbus" and body["protocol"]["port"] == 502
        assert body["task"]["name"] == "seven" and len(body["task"]["sha256"]) == 64
        assert body["cell"] == {"name": "c1", "rail": "psu-c", "machines": ["pack-conveyor-1"]}
        for _ in range(50):                              # the client records the answer after the server replies
            if en.status()["ok"] is not None:
                break
            time.sleep(0.05)
        assert en.status()["ok"] is True
    finally:
        en.stop()


def test_enrollment_failure_is_recorded_not_fatal():
    rt = make_runtime(GOOD)
    en = Enroller(rt, "http://127.0.0.1:1/enroll", "tok", "h", 502, timeout_s=0.5)
    assert en.enroll_once() is False
    assert en.status()["ok"] is False and "cannot reach" in en.status()["error"]
