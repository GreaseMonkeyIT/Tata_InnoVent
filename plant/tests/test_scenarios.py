"""The PS3 field segment, the PS4B replayed AMPS word, the feeder meter, and the new plant-sim
interface keys (SCENARIOS.md sections 2.3, 2.5, 3, and 10). The PS2 relay tests live in
test_physics.py, because that file steps Device.step with its own loop.

The sim module loads here as its own copy, so these tests never share state with the other
plant test files. Physics steps run in the test thread with a fixed dt. The segment tests pass
an explicit wall time where the result depends on it.

Run:  python -m pytest plant/tests/test_scenarios.py -q      (from the repo root)
"""
import importlib.util
import json
import os
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

_SIM = os.path.join(os.path.dirname(__file__), "..", "sim", "main.py")
_spec = importlib.util.spec_from_file_location("plant_sim_scenarios", _SIM)
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

IDLE_T0 = 61       # inside the compressor's idle window (see test_physics.py)


def tick(t, seconds, dt=1.0):
    """Step the whole world under the lock, as the sim loop does."""
    for _ in range(int(seconds / dt)):
        with sim._lock:
            sim.step_world(t, dt)
        t += dt
    return t


def metric(text, name, pod):
    """The value of one series in a /metrics text, or None."""
    head = f'{name}{{namespace="plant",pod="{pod}"}} '
    for line in text.splitlines():
        if line.startswith(head):
            return float(line[len(head):])
    return None


def http(port, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw, code, ctype = r.read().decode(), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raw, code, ctype = e.read().decode(), e.code, e.headers.get("Content-Type", "")
    return code, (json.loads(raw) if "json" in ctype else raw)


@pytest.fixture(autouse=True)
def world():
    """A healthy world with only the default base cell (no threads) and a calm segment."""
    sim.init_cells(sim.DEFAULT_BASE_CELLS, join_s=5)
    random.seed(2468)
    with sim._lock:
        sim.reset_plant()
    sim.LOOP.flow = sim.LOOP.flow_nominal
    for r in sim.RAILS:
        r.voltage = r.v_src
    for d in sim.BASE_DEVICES:
        d.friction, d.temp, d.current, d.throughput = 1.0, 35.0, 0.0, 100.0
        d.recent.clear()
    for seg in sim.SEGMENTS.values():
        seg._frames.clear()
        seg._waits.clear()
    yield
    with sim._lock:
        sim.reset_plant()
    sim.init_cells(sim.DEFAULT_BASE_CELLS, join_s=5)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), sim.H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


# --------------------------------------------------------- PS3: the queue model --
def test_mm1k_drops_only_above_rho_one():
    for rho in (0.0, 0.1, 0.5, 0.8, 0.9):
        p, n = sim.mm1k(rho, 50)
        assert p < 1e-3 and 0.0 <= n < 10.0, rho
    p1, n1 = sim.mm1k(1.0, 50)
    assert p1 == pytest.approx(1 / 51) and n1 == 25.0
    near, _ = sim.mm1k(1.0 + 1e-4, 50)
    assert near == pytest.approx(p1, rel=1e-2)                   # no jump at rho 1
    drops = [sim.mm1k(rho, 50)[0] for rho in (1.0, 1.1, 1.25, 1.5, 2.0, 3.0)]
    assert drops == sorted(drops) and drops[0] < drops[-1]
    assert sim.mm1k(1.5, 50)[0] == pytest.approx(1 - 1 / 1.5, abs=1e-3)   # about 0.33
    p, n = sim.mm1k(1e6, 50)                                     # no overflow
    assert p == pytest.approx(1.0, abs=1e-5) and n == pytest.approx(50.0, abs=1e-3)


def test_segment_ps3_ramp_and_calm():
    seg = sim.Segment("seg-test", ["talk-1"], 1000, 50)
    t0 = 1_000_000.0
    rest = seg.to_json({}, 2.0, now=t0)
    assert set(rest) == {"capacity_fps", "utilization", "latency_ms", "drop_ratio", "members"}
    assert rest["members"]["talk-1"]["kind"] == "talker"
    assert rest["members"]["talk-1"]["offered_fps"] == pytest.approx(20.0, abs=2.0)
    assert rest["utilization"] < 0.03 and rest["drop_ratio"] == 0.0
    assert rest["latency_ms"] == pytest.approx(1.0, abs=0.1)     # one frame time at 1000 fps

    seg.storm("talk-1", 1500, 45, now=t0)
    mid = seg.to_json({}, 2.0, now=t0 + 20)                      # rho about 0.68
    end = seg.to_json({}, 2.0, now=t0 + 60)                      # rho about 1.5
    assert 0.6 < mid["utilization"] < 0.75 and mid["drop_ratio"] < 1e-3
    assert 1.4 < end["utilization"] < 1.6 and end["drop_ratio"] > 0.25
    assert end["latency_ms"] > 10 * rest["latency_ms"]
    assert end["members"]["talk-1"]["latency_ms"] > end["latency_ms"]   # retransmits add wait
    seg.calm()
    assert seg.to_json({}, 2.0, now=t0 + 61)["utilization"] < 0.03


def test_request_drops_only_above_rho_one():
    """Sample real request waits. One request per second, so the member adds little load."""
    seg = sim.Segment("seg-test", ["talk-1"], 1000, 50)
    t0 = 2_000_000.0
    calm = [seg.request_delay("plc-x", 2.0, now=t0 + i) for i in range(2000)]
    assert max(calm) < 0.01                                      # no loss, no retransmit
    seg.storm("talk-1", 850, 1, now=t0 + 2000)                   # rho about 0.87
    busy = [seg.request_delay("plc-x", 2.0, now=t0 + 2010 + i) for i in range(2000)]
    assert sum(d >= 2.0 for d in busy) == 0
    seg.storm("talk-1", 1500, 1, now=t0 + 4010)                  # rho about 1.5
    storm = [seg.request_delay("plc-x", 2.0, now=t0 + 4020 + i) for i in range(2000)]
    drops = sum(d >= 2.0 for d in storm) / len(storm)
    assert 0.03 < drops < 0.3                                    # four losses in a row: q^4
    assert sum(d > 0.1 for d in storm) > 0.3 * len(storm)        # most requests retransmit


# ------------------------------------------------ PS3: the wrapper on the real link --
class FakeClient:
    """Stands in for a ModbusTcpClient. It records each request and answers it."""

    def __init__(self):
        self.calls = []
        self.connected = True

    def connect(self):
        return True

    def close(self):
        self.connected = False

    def _answer(self, name, *a, **kw):
        self.calls.append(name)

        class R:
            registers = [100] * 8
            bits = [True] * 8

            def isError(self):
                return False
        return R()

    def write_registers(self, *a, **kw):
        return self._answer("write_registers")

    def write_coils(self, *a, **kw):
        return self._answer("write_coils")

    def read_holding_registers(self, *a, **kw):
        return self._answer("read_holding_registers")

    def read_coils(self, *a, **kw):
        return self._answer("read_coils")


class FakeCell:
    def __init__(self):
        self.name, self.controller, self.stop = "cell-x", "plc-x", threading.Event()


def test_segment_client_raises_on_simulated_timeout(monkeypatch):
    monkeypatch.setattr(sim, "CELL_TIMEOUT_S", 0.05)
    cell, client = FakeCell(), FakeClient()
    calm = sim.SegmentClient(sim.Segment("seg-a", ["talk-1"], 1000, 50), cell, client)
    assert calm.write_registers(0, [0] * 32, slave=1).isError() is False
    assert client.calls == ["write_registers"]
    assert calm.connected is True and calm.connect() is True     # these pass through

    jammed = sim.Segment("seg-b", ["talk-1"], 1.0, 50)           # rho 20: the queue is full
    wrapped = sim.SegmentClient(jammed, cell, client)
    start = time.time()
    with pytest.raises(ConnectionError, match="simulated segment drop"):
        wrapped.read_coils(100, count=8, slave=1)
    assert time.time() - start >= 0.04                           # it waited the timeout
    assert client.calls == ["write_registers"]                   # the real request never went out
    assert jammed.to_json({"plc-x": _cell_like()}, 0.05)["members"]["plc-x"]["latency_ms"] == 50.0

    cell.stop.set()                                              # a removed cell stops waiting
    with pytest.raises(ConnectionError):
        wrapped.read_coils(100, count=8, slave=1)


def _cell_like():
    c = FakeCell()
    c.last_ok, c.failures = None, 0
    return c


def test_cell_link_drops_under_storm_and_recovers(monkeypatch):
    """The base cell's field thread runs through field-1. A jammed segment disconnects it with
    the simulated drop. A calm segment lets it reconnect. OpenPLC is not involved."""
    fakes = []

    def make_client(*a, **kw):
        fakes.append(FakeClient())
        return fakes[-1]

    monkeypatch.setattr(sim, "ModbusTcpClient", make_client)
    monkeypatch.setattr(sim, "TICK_S", 0.02)
    monkeypatch.setattr(sim, "CELL_RETRY_S", 0.05)
    monkeypatch.setattr(sim, "CELL_TIMEOUT_S", 0.05)
    seg = sim.FIELD_NET
    assert seg is sim.SEGMENTS["field-1"]
    cell = sim.CELLS["stamping"]
    sim.start_cell(cell)
    try:
        _wait(lambda: cell.connected, "first sync")
        assert "plc-stamping" in seg._frames                     # the requests went through field-1
        monkeypatch.setattr(seg, "capacity_fps", 1.0)
        _wait(lambda: not cell.connected and "simulated segment drop" in (cell.last_error or ""),
              "a simulated drop")
        assert cell.failures >= 1 and cell.mode == "fail-open"
        monkeypatch.setattr(seg, "capacity_fps", 1000.0)
        _wait(lambda: cell.connected, "reconnect")
        state = sim.state_json()                                 # takes _lock itself
        member = state["segments"]["field-1"]["members"]["plc-stamping"]
        assert member["kind"] == "plc" and member["cell"] == "stamping"
        assert member["offered_fps"] > 0 and member["failures"] >= 1
        assert member["sync_age_s"] is not None and member["sync_age_s"] < 5
    finally:
        cell.stop.set()
        cell.thread.join(5)


def _wait(pred, what, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        with sim._lock:
            if pred():
                return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def test_ps3_fault_ramps_hmi_gw_and_reset_calms_it():
    assert sim.STORM == (sim.SEGMENTS["field-1"], "hmi-gw")
    t0 = time.time()
    with sim._lock:
        sim.FAULTS["PS3"]["apply"]()
        sim.ACTIVE.add("PS3")
        seg = sim.segments_json(now=t0 + 60)["field-1"]
    assert seg["members"]["hmi-gw"]["offered_fps"] == pytest.approx(1500, rel=0.15)
    assert seg["utilization"] > 1.2 and seg["drop_ratio"] > 0.15
    with sim._lock:
        sim.reset_plant()
        seg = sim.segments_json(now=t0 + 61)["field-1"]
    assert seg["members"]["hmi-gw"]["offered_fps"] == pytest.approx(20, abs=2)
    assert sim.ACTIVE == set()


def test_segment_names_are_reserved_and_parsed(server):
    assert sim.parse_segments(sim.DEFAULT_FIELD_SEGMENTS) == [
        {"name": "field-1", "talkers": ["hmi-gw"], "capacity_fps": 1000.0, "buffer": 50}]
    assert sim.parse_segments("") == []
    parsed = sim.parse_segments("a|t1,t2|500|10;bad;b|press-1|1000|50;c|t3|x|5;d|t4|-1|5;e||800|20")
    assert [p["name"] for p in parsed] == ["a", "e"] and parsed[1]["talkers"] == []
    for name in ("hmi-gw", "field-1"):
        body = {"cell": name, "plc_host": "127.0.0.1", "machines": [{"name": "m-9", "i_base": 1.0}]}
        assert http(server, "POST", "/cells", body)[0] == 409
        body = {"cell": "c-9", "plc_host": "127.0.0.1", "machines": [{"name": name, "i_base": 1.0}]}
        assert http(server, "POST", "/cells", body)[0] == 409


# ------------------------------------------------------- PS4B: the replayed word --
def test_ps4b_changes_only_the_vplc_amps_word():
    t = tick(IDLE_T0, 40)
    press, press2 = sim.BY_NAME["press-1"], sim.BY_NAME["press-2"]
    recorded = list(press.recent)
    assert len(recorded) == sim.REPLAY_N == 30 and max(recorded) < 45.0
    with sim._lock:
        sim.FAULTS["PS4B"]["apply"]()
        sim.ACTIVE.add("PS4B")
    assert press.replay["buf"] == recorded
    tick(t, 60)
    assert 58.0 < press.current < 64.0                           # about 61 A, the truth
    assert press.temp < sim.TRIP_C                               # no trip

    cell, t0 = sim.CELLS["stamping"], press.replay["t0"]
    with sim._lock:
        for k in (0, 7, 29, 30, 45):                             # indexed by wall time, looping
            hr, coils = sim.cell_frames(cell, now=t0 + (k + 0.5) * sim.TICK_S)
            assert hr[0] == sim._word10(recorded[k % 30])
            assert hr[1:4] == [sim._word10(press.temp), sim._word10(press.rail.voltage),
                               sim._word10(press.throughput)]
            assert hr[4] == sim._word10(press2.current)          # press-2 keeps the truth
            assert coils[0] is True
        regs, _ = sim.plc_frames()
    text, state = sim.metrics_text(), sim.state_json()          # both take _lock themselves
    assert regs[8] == int(press.current * 10)                    # OpenPLC MW8 keeps the truth
    assert metric(text, "plant_current_draw_amps", "press-1") == pytest.approx(press.current, abs=1e-4)
    assert metric(text, "plant_heat_load_watts", "press-1") == pytest.approx(0.55 * press.current, abs=1e-3)
    assert state["devices"]["press-1"]["amps"] == round(press.current, 2)
    assert "PS4B" in state["active_faults"]

    with sim._lock:
        sim.reset_plant()
        hr, _ = sim.cell_frames(cell, now=t0 + 5)
    assert press.replay is None and press.friction == 1.0
    assert hr[0] == sim._word10(press.current)


# ------------------------------------------------------------- the feeder meter --
def test_feeder_amps_equal_the_device_sum():
    sim.add_cell({"cell": "packaging", "plc_host": "127.0.0.1", "field_port": 1,
                  "machines": [{"name": "pack-1", "i_base": 9.0}]}, start=False)
    t = IDLE_T0
    with sim._lock:
        sim.FAULTS["PS2"]["apply"]()                             # load changes every tick
    for _ in range(3):
        t = tick(t, 7)
        with sim._lock:
            sums = {r.name: sum(d.current for d in sim.all_devices() if d.rail is r)
                    for r in sim.RAILS}
        text, state = sim.metrics_text(), sim.state_json()      # both take _lock themselves
        for rail, amps in sums.items():
            assert state["rails"][rail]["amps"] == round(amps, 2)
            assert metric(text, "plant_feeder_current_amps", rail) == pytest.approx(amps, abs=1e-4)
    assert sums["psu-c"] > 0.0 and sums["psu-a"] > 100.0


# ---------------------------------------------------------- the interface keys --
def test_state_domains_and_metrics_carry_the_new_keys(server):
    tick(IDLE_T0, 5)
    code, doms = http(server, "GET", "/domains")
    assert code == 200
    assert doms["domains"]["loop:cool-1"][-2:] == ["chiller-1", "cool-1"]
    assert doms["domains"]["net:field-1"] == ["hmi-gw", "plc-stamping", "field-1"]

    for fid in ("PS2", "PS3", "PS4B"):
        assert http(server, "POST", f"/fault/{fid.lower()}")[0] == 200
    code, state = http(server, "GET", "/state")
    assert state["active_faults"] == ["PS2", "PS3", "PS4B"]
    assert set(state["rails"]["psu-a"]) == {"volts", "v_src", "amps"}
    assert all(dev["trip_reason"] is None for dev in state["devices"].values())
    seg = state["segments"]["field-1"]
    assert set(seg) == {"capacity_fps", "utilization", "latency_ms", "drop_ratio", "members"}
    assert seg["capacity_fps"] == 1000.0
    assert set(seg["members"]["hmi-gw"]) == {"kind", "offered_fps", "latency_ms"}
    assert set(seg["members"]["plc-stamping"]) == {"kind", "cell", "offered_fps", "latency_ms",
                                                  "sync_age_s", "failures"}
    assert seg["members"]["plc-stamping"]["sync_age_s"] is None  # no field thread here

    code, text = http(server, "GET", "/metrics")
    for rail in ("psu-a", "psu-b", "psu-c"):
        assert metric(text, "plant_feeder_current_amps", rail) is not None
    assert metric(text, "plant_motor_tripped", "chiller-1") == 0.0
    assert 0.0 <= metric(text, "plant_overload_ratio", "chiller-1") <= 1.0
    assert metric(text, "plant_cooling_shortfall_watts", "chiller-1") >= 0.0
    for member in ("hmi-gw", "plc-stamping"):
        assert metric(text, "plant_net_offered_fps", member) is not None
        assert metric(text, "plant_net_latency_ms", member) is not None
    assert metric(text, "plant_net_utilization_ratio", "field-1") is not None
    assert metric(text, "plant_net_drop_ratio", "field-1") is not None
    for fid in ("PS3", "PS4B"):
        assert f'plant_fault_active{{namespace="plant",pod="plant-sim",fault="{fid}"}} 1' in text
    assert metric(text, "plant_trip_active", "chiller-1") is None   # not an OpenPLC coil

    chiller = sim.BY_NAME["chiller-1"]
    with sim._lock:
        chiller.overload.latched, chiller.overload.heat, chiller.tripped = True, 90.0, True
    code, state = http(server, "GET", "/state")
    assert state["devices"]["chiller-1"]["trip_reason"] == "overload"
    assert metric(http(server, "GET", "/metrics")[1], "plant_motor_tripped", "chiller-1") == 1.0

    assert http(server, "POST", "/reset")[0] == 200
    code, state = http(server, "GET", "/state")
    assert state["active_faults"] == [] and state["devices"]["chiller-1"]["tripped"] is False
    assert state["devices"]["chiller-1"]["trip_reason"] is None
    assert sim.BY_NAME["press-1"].replay is None


# ---------------------------------- LOG-075: the review before the sim code freeze --
def test_a_repeat_fault_post_changes_nothing(server):
    """The console can send one fault twice (a double click). A second PS4B apply recorded the
    replay from the faulted current, so the vPLC word matched the truth and the evidence was gone."""
    tick(IDLE_T0, 40)
    press, cell = sim.BY_NAME["press-1"], sim.CELLS["stamping"]
    code, text = http(server, "POST", "/fault/ps4b")
    assert code == 200 and "injected" in text
    recorded = list(press.replay["buf"])
    tick(IDLE_T0 + 40, 30)                                       # the friction is up now
    code, text = http(server, "POST", "/fault/PS4B")
    assert code == 200 and "already active" in text
    assert press.replay["buf"] == recorded                       # the recording is the healthy one
    with sim._lock:
        hr, _ = sim.cell_frames(cell)
    assert press.current > 55.0 and hr[0] < 450                  # the word still hides the truth
    assert http(server, "POST", "/reset")[0] == 200
    assert http(server, "POST", "/fault/ps4b")[1].startswith("PS4B injected")   # fresh after a reset


def test_a_silent_client_does_not_block_the_server():
    """One client that connects and sends nothing must not freeze /metrics or the liveness probe.
    The old single-threaded server waited on it with no timeout."""
    srv = sim.make_server(0, "127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_address[1]
    silent = socket.create_connection(("127.0.0.1", port))
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/healthz")
        with urllib.request.urlopen(req, timeout=3) as r:
            assert r.status == 200
    finally:
        silent.close()
        srv.shutdown()
        srv.server_close()
