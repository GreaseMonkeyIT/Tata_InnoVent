"""Checks of live/plant_service.py: the live plant gives the readings of eval/nodes.py `record` for the
same run, value for value, faults included; a fault added during the run does not touch the plant
before its onset; the HTTP service serves those readings. Short runs (a 2-min warm-up) as in
eval/tests/test_nodes.py. `python -m live.plant_service --check 1` repeats the first check with the
full warm-up."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)

from live import plant_service as S                                  # noqa: E402

N = S.N


@pytest.fixture
def short(monkeypatch):
    monkeypatch.setattr(N, "WARMUP_S", 120.0)
    monkeypatch.setattr(N, "_VERSION", [])


@pytest.mark.parametrize("faults", [
    [],
    [("F6", 0.5, 60.0)],                           # the range rule runs on the batch plant first
    [("F3", 0.7, 30.0)],                           # a binary fault: no range rule
    [("F11", 0.3, 50.0)],                          # a sag: injected, then cleared after its duration
    [("F11", 0.9, 50.05)],                         # a dip of one cycle: cleared by a sub-step (N.fault_step)
    [("F6", 0.5, 60.0), ("F13", 0.5, 90.0)],      # a pair, as `simulate_multi` runs it
])
def test_live_plant_equals_record(short, faults):
    r = S.compare(40000, 0.05, faults)
    assert r["bad"] == []
    assert r["samples"] == 120 + 180 and r["lead"] == 120 and r["tags"] == len(r["plant"].tags)
    assert [f["state"] for f in r["plant"].faults] == ["cleared" if f[0] == "F11" else "on" for f in faults]


def test_fault_added_during_the_run(short):
    """A fault added while the plant runs starts at its onset and leaves the plant before it as it was
    (the range rule runs on a separate plant). The operator can clear an active fault and cancel a
    planned one."""
    a, b = S.LivePlant(40000), S.LivePlant(40000)
    for _ in range(1500):                          # 150 s: the warm-up ends at 120 s
        a.step()
        b.step()
    f = b.add_fault("F6", 0.5, 60.0)               # onset at 180 s
    g = b.add_fault("F13", 0.5)                    # no onset: now
    assert f["state"] == g["state"] == "planned" and g["onset_s"] == pytest.approx(30.0)
    while b.p.t < 175.0:
        a.step()
        b.step()
    assert g["state"] == "on"
    pre = [s for s in b.samples if s[1] < 150.0]
    assert [repr(s) for s in pre] == [repr(s) for s in list(a.samples)[:len(pre)]]
    assert b.clear_fault("F6") == 1 and f["state"] == "cancelled"
    assert b.clear_fault("F13") == 1 and g["state"] == "cleared" and g["off_t"] == b.p.t
    assert b.clear_fault("F13") == 0
    for bad in (("F99", 0.5), ("F6", 1.5), ("F6", True), ("F6", None), ("F6", 0.5, -1.0)):
        with pytest.raises(ValueError):
            b.add_fault(*bad)
    assert [x["fid"] for x in b.fault_log()] == ["F6", "F13"] and "_on" not in b.fault_log()[0]


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        body = r.read().decode()
        return r.status, (json.loads(body) if r.headers.get("Content-Type") == "application/json" else body)


def _post(base, path, obj):
    req = urllib.request.Request(base + path, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_service(short):
    """The service on a free port at 200 x: the readings it serves equal an offline run of the same
    seed, and the clock, the metrics, the faults and the speed answer as documented."""
    svc = S.Service(S.LivePlant(40000), speed=200.0)
    srv = S.make_server(svc, "127.0.0.1", 0)
    threading.Thread(target=svc.run, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        deadline = time.time() + 60.0
        while _get(base, "/clock")[1]["sample"] < 150:
            assert time.time() < deadline
            time.sleep(0.05)
        ref = S.LivePlant(40000)
        while ref.n < 150:
            ref.step()
        code, info = _get(base, "/info")
        assert code == 200 and info["seed"] == 40000 and info["warmup_s"] == 120.0 and info["tags"] == ref.tags
        code, got = _get(base, "/samples?from=0&limit=150")
        assert code == 200 and got["first"] == 0 and got["next"] == 150 and got["tags"] == info["tags"]
        assert [repr(r) for r in got["rows"]] == [repr([i, t, v]) for i, t, v in ref.samples]
        clock = _get(base, "/clock")[1]
        assert clock["warming"] is False and clock["speed"] == 200.0 and clock["anchor_t"] >= 120.0
        metrics = _get(base, "/metrics")[1]
        assert 'visr_value{tag="psu-a.voltage_v",station="psu-a"} ' in metrics and "visr_plant_seconds " in metrics
        assert _post(base, "/fault", {"fid": "F99", "severity": 0.5})[0] == 400
        code, f = _post(base, "/fault", {"fid": "f6", "severity": 0.5})
        assert code == 200 and f["fid"] == "F6" and f["state"] == "planned"
        assert [x["fid"] for x in _get(base, "/faults")[1]] == ["F6"]
        assert _post(base, "/fault/clear", {"fid": "F6"}) == (200, {"changed": 1})
        code, clock = _post(base, "/speed", {"speed": 50})
        assert code == 200 and clock["speed"] == 50.0
        assert _post(base, "/speed", {"speed": 0})[0] == 400
        assert _get(base, "/samples?from=999999")[1]["rows"] == []
        with pytest.raises(urllib.error.HTTPError):
            _get(base, "/nothing")
    finally:
        svc.stop.set()
        srv.shutdown()
        srv.server_close()


def test_floor_recording_leaves_the_readings_and_appends(short):
    """With the floor recording on, the readings are the same (the tracking table only reads the
    plant), and the segments after plant times, appended in turn, give the whole recording."""
    a, b = S.LivePlant(40000), S.LivePlant(40000, floor=True)
    svc = S.Service(b)
    segs, last = [], None
    for k in range(3000):                          # 300 s: the warm-up of 120 s and 180 s live
        a.step()
        b.step()
        if k % 700 == 699:
            seg = svc.floor(last)
            segs.append(seg)
            last = seg["meta"]["t_end"]
    assert [repr(s) for s in a.samples] == [repr(s) for s in b.samples]
    full = svc.floor(None)
    assert full["meta"]["t_start"] == 0.0 and full["values"]["t0"] == 2.0 and len(full["values"]["rows"]) > 100
    rows = [r for s in segs for r in s["values"]["rows"]]
    states = [x for s in segs for x in s["states"]]
    events = [x for s in segs for x in s["events"]]
    n = len(rows)
    assert rows == full["values"]["rows"][:n] and states == [x for x in full["states"] if x[0] <= last]
    assert events == [e for e in full["events"] if e[0] <= last]
    assert all(e[2] in full["items"] for e in events)
    assert S.Service(S.LivePlant(40000)).floor() is None


def test_test_split_is_refused():
    with pytest.raises(SystemExit):
        S.main(["--seed", str(N.SEED_BASE["test"]), "--check", "0.01"])
