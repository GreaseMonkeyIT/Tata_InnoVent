"""Cells, the spare feeder psu-c, and the field wiring of plant/sim/main.py (FLEET.md section 7).

The sim module loads here as its own copy, so these tests never share state with
test_physics.py. Physics steps run in the test thread with a fixed dt. The field-wiring
threads run for real against a fake field-port PLC on 127.0.0.1. The fake PLC is a pymodbus
3.6.9 server that holds only the FLEET.md 4.1 map: holding registers and coils 0..63 and
100..163. It does not run a task. The test sets RUN and SPEED_PCT in it directly.

Run:  python -m pytest plant/tests/test_cells.py -q      (from the repo root)
"""
import asyncio
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
_spec = importlib.util.spec_from_file_location("plant_sim_cells", _SIM)
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

BASE_NAMES = ["press-1", "press-2", "cnc-1", "qa-scanner-1",
              "conveyor-1", "compressor-1", "furnace-1", "chiller-1"]
IDLE_T0 = 61       # inside the compressor's idle window (see test_physics.py)

PACKAGING = [
    {"name": "pack-conveyor-1", "kind": "conveyor", "i_base": 9.0, "cooled": False},
    {"name": "pack-wrapper-1", "kind": "wrapper", "i_base": 14.0, "cooled": True,
     "tau": 50.0, "heat_k": 0.5},
    {"name": "pack-labeler-1", "kind": "labeler", "i_base": 4.0, "cooled": False,
     "v_sensitive": True},
]


# --------------------------------------------------------------------- helpers --
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_until(pred, timeout=8.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        with sim._lock:
            if pred():
                return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def tick(t, seconds, dt=1.0):
    """Step the whole world (rails A, B, C, the loop, every device) under the lock."""
    for _ in range(int(seconds / dt)):
        with sim._lock:
            sim.step_world(t, dt)
        t += dt
    return t


class FakeFieldPLC:
    """A field port that holds only the FLEET.md 4.1 map. Nothing else answers."""

    def __init__(self, port):
        from pymodbus.datastore import (ModbusServerContext, ModbusSlaveContext,
                                        ModbusSparseDataBlock)
        self.port = port
        hr = ModbusSparseDataBlock({0: [0] * 64, 100: [0] * 64}, mutable=False)
        co = ModbusSparseDataBlock({0: [False] * 64, 100: [False] * 64}, mutable=False)
        self.slave = ModbusSlaveContext(hr=hr, co=co, zero_mode=True)
        self.context = ModbusServerContext(slaves=self.slave, single=True)   # any unit id
        self.loop = asyncio.new_event_loop()
        self.server = None
        self._up = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self._up.wait(5), "fake PLC did not start"
        self._wait_listening()

    def _run(self):
        from pymodbus.server import ModbusTcpServer
        asyncio.set_event_loop(self.loop)

        async def serve():
            self.server = ModbusTcpServer(self.context, address=("127.0.0.1", self.port))
            asyncio.get_running_loop().call_soon(self._up.set)
            await self.server.serve_forever()

        self.loop.run_until_complete(serve())

    def _wait_listening(self):
        end = time.time() + 5
        while time.time() < end:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError("fake PLC is not listening")

    def set_run(self, k, on):
        self.slave.setValues(1, 100 + k, [bool(on)])        # %QX0.k

    def set_speed(self, k, pct):
        self.slave.setValues(3, 100 + k, [int(pct)])         # %QW k

    def sensor_words(self):
        return self.slave.getValues(3, 0, 32)                # %IW0..31

    def ready_bits(self):
        return self.slave.getValues(1, 0, 8)                 # %IX0.0..0.7

    def stop(self):
        """Close the listener and every open connection. A second call does nothing."""
        if self.server is not None and self.thread.is_alive():
            asyncio.run_coroutine_threadsafe(self.server.shutdown(), self.loop).result(5)
        self.thread.join(5)


class RecordingClient:
    """Stands in for the OpenPLC Modbus client and records every write."""

    def __init__(self):
        self.writes = []

    def write_registers(self, address, values, slave=1):
        self.writes.append((address, list(values)))

    def read_coils(self, address, count=1, slave=1):
        class R:
            bits = [False] * 8
            def isError(self):
                return False
        return R()


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


def packaging_body(plc_port, rail="psu-c", fail_open=False, cell="packaging", machines=None):
    return {"cell": cell, "plc_host": "127.0.0.1", "field_port": plc_port, "rail": rail,
            "fail_open": fail_open, "machines": machines or PACKAGING}


# --------------------------------------------------------------------- fixtures --
@pytest.fixture(autouse=True)
def world(monkeypatch):
    """A healthy world with only the default base cell (no threads), fast wiring timers."""
    monkeypatch.setattr(sim, "TICK_S", 0.05)
    monkeypatch.setattr(sim, "CELL_RETRY_S", 0.2)
    monkeypatch.setattr(sim, "CELL_TIMEOUT_S", 1.0)
    sim.init_cells(sim.DEFAULT_BASE_CELLS, join_s=5)
    random.seed(4321)
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()
    sim.LOOP.pump_health = 1.0
    sim.LOOP.flow = sim.LOOP.flow_nominal
    for r in sim.RAILS:
        r.voltage = r.v_src
    for d in sim.BASE_DEVICES:
        d.friction, d.temp, d.current, d.throughput, d.tripped = 1.0, 35.0, 0.0, 100.0, False
        d.replay = None
        d.recent.clear()
        if d.overload is not None:
            d.overload.reset()            # a latched relay would trip the chiller again
    sim.BY_NAME["compressor-1"].duty_fn = sim.compressor_duty
    for seg in sim.SEGMENTS.values():
        seg.calm()
    yield
    sim.init_cells(sim.DEFAULT_BASE_CELLS, join_s=5)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), sim.H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def plc():
    fake = FakeFieldPLC(free_port())
    yield fake
    fake.stop()


# ----------------------------------------------------- base plant is unchanged --
class _State:
    pass


class LegacyRelay:
    """The chiller-1 overload relay, written out again from SCENARIOS.md 2.2 so the guard
    checks main.py against the contract, not against itself. It uses no random numbers."""

    def __init__(self):
        self.heat, self.latched = 0.0, False

    def step(self, amps, dt):
        if amps > 1.02 * 22.0:
            self.heat += 1.0 * dt
        else:
            self.heat = max(0.0, self.heat - 0.5 * dt)
        if self.heat >= 90.0:
            self.latched = True
        return self.latched


def legacy_step(d, st, t, dt, relay=None):
    """Device.step as it was before cells (main.py at a1622ea), on a copy of the state. The
    temperature noise follows Device.temp_noise (tau-scaled since 2026-09-19, LOG-070).
    `relay` adds the PS2 overload relay (SCENARIOS.md 2.2). It trips st.tripped, not d.
    Two changes on purpose since then (LOG-075): a current is never below 0 A, and a machine
    that the voltage does not slow recovers its throughput at any rail voltage. The old
    formula kept press-2 near 0 % after its trip below, while it drew full current."""
    if st.tripped:
        st.current = max(random.gauss(0.2, 0.02), 0.0)
        st.throughput = max(0.0, st.throughput - 5.0)
        if d.loop is not None:
            st.temp += (35.0 - st.temp) * (dt / d.tau) + random.gauss(0, d.temp_noise())
        return
    duty = d.duty_fn(t)
    st.current = max(0.0, d.i_base * duty * d.friction + random.gauss(0, 0.05))
    v = d.rail.voltage
    low = v < 0.92 * d.rail.v_src
    if low:
        st.current *= min(1.15, (0.92 * d.rail.v_src) / max(v, 1.0))
    if low and d.v_sensitive:
        st.throughput = max(20.0, 100.0 * v / d.rail.v_src - random.uniform(0, 3))
    else:
        st.throughput = min(100.0, st.throughput + 2.0)
    if relay is not None and relay.step(st.current, dt):
        st.tripped = True
    if d.loop is not None:
        heat = d.heat_k * st.current
        share = max(d.loop.flow / d.loop.flow_nominal, 0.05)
        t_target = 35.0 + heat / share
        st.temp += (t_target - st.temp) * (dt / d.tau) + random.gauss(0, d.temp_noise())


def test_base_plant_unchanged_without_stamping_plc():
    """The default stamping cell has no PLC. Every base device must step exactly like the old
    formula, through faults, a trip, and both compressor windows. chiller-1 adds the contract
    relay: PS2 at n=350 must trip it in this run, at the same tick as the contract model."""
    cell = sim.CELLS["stamping"]
    assert cell.base and cell.fail_open and not cell.connected and cell.mode == "fail-open"
    assert [d.name for d in cell.devices] == ["press-1", "press-2"]
    chiller, relay, tripped_at = sim.BY_NAME["chiller-1"], LegacyRelay(), None
    t, dt = 0.0, 1.0
    for n in range(700):
        if n == 100:
            sim.FAULTS["PS1"]["apply"]()
        if n == 250:
            sim.FAULTS["PS5"]["apply"]()
        if n == 350:
            sim.FAULTS["PS2"]["apply"]()
        if n == 450:
            sim.BY_NAME["press-2"].tripped = True
        if n == 500:
            sim.BY_NAME["press-2"].tripped = False
        with sim._lock:
            devices = sim.all_devices()
            for rail in sim.RAILS:
                rail.step(sum(d.current for d in devices if d.rail is rail))
            sim.LOOP.step()
            for d in devices:
                st = _State()
                st.current, st.throughput, st.temp = d.current, d.throughput, d.temp
                st.tripped = d.tripped
                rng = random.getstate()
                legacy_step(d, st, t, dt, relay if d is chiller else None)
                after_legacy = random.getstate()
                random.setstate(rng)
                d.step(t, dt)
                assert d.speed_frac == 1.0
                assert (d.current, d.throughput, d.temp, d.tripped) == \
                    (st.current, st.throughput, st.temp, st.tripped), \
                    f"{d.name} diverged from the old formula at t={t}"
                assert random.getstate() == after_legacy
            assert chiller.overload.heat == relay.heat
            if chiller.tripped and tripped_at is None:
                tripped_at = n
        t += dt
    # PS2 lands at phase 50 with about 49 heat, so the relay trips about 41 s later.
    assert tripped_at is not None and 350 < tripped_at <= 450
    assert sim.state_json()["devices"]["chiller-1"]["trip_reason"] == "overload"


def test_idle_world_rails_with_psu_c():
    tick(IDLE_T0, 120)
    a, b, c = sim.RAIL_A, sim.RAIL_B, sim.RAIL_C
    assert 358 <= a.voltage <= 364 and 370 <= b.voltage <= 376     # the calibrated idle band
    assert (c.name, c.v_src, c.r_src) == ("psu-c", 400.0, 0.5)
    assert abs(c.voltage - 400.0) < 1.0                              # no load on the spare feeder
    state = sim.state_json()
    assert list(state["rails"]) == ["psu-a", "psu-b", "psu-c"]
    assert state["rails"]["psu-c"]["v_src"] == 400.0
    assert 'plant_bus_voltage_volts{namespace="plant",pod="psu-c"}' in sim.metrics_text()


def test_register_blocks_unchanged_by_cells():
    """The OpenPLC writes (MW0..15, MW24..31) keep their length, order, and bytes when cells
    with 8 machines exist."""
    assert sim.DEVICES is sim.BASE_DEVICES
    assert [d.name for d in sim.BASE_DEVICES] == BASE_NAMES

    def set_base():
        for i, d in enumerate(sim.BASE_DEVICES):
            d.current, d.throughput, d.temp = 10.0 + i, 50.0 + i, 40.0 + i
        sim.RAIL_A.voltage, sim.RAIL_B.voltage = 361.0, 373.0
        sim.LOOP.flow, sim.LOOP.pump_health = 120.0, 1.0

    def frames():
        client = RecordingClient()
        sim.plc_sync_once(client)
        return client.writes

    set_base()
    before = frames()
    assert [a for a, _ in before] == [sim.PLC_MW_BASE, sim.PLC_MW_BASE + 24]
    regs, thru = before[0][1], before[1][1]
    assert len(regs) == 16 and len(thru) == 8
    assert regs[8:16] == [100 + 10 * i for i in range(8)]          # amps x10 in BASE order
    assert thru == [500 + 10 * i for i in range(8)]                # throughput x10 in BASE order
    assert regs[0:4] == [400, 410, 420, 460]                       # press-1, press-2, cnc-1, furnace-1

    eight = [{"name": f"big-{k}", "i_base": 90.0, "cooled": True} for k in range(8)]
    sim.add_cell(packaging_body(1, cell="bigcell", rail="psu-a", machines=eight), start=False)
    sim.add_cell(packaging_body(1), start=False)
    for d in sim.CELL_DEVICES:
        d.current, d.throughput, d.temp = 99.0, 77.0, 66.0
    set_base()
    after = frames()
    assert after == before
    assert len(sim.CELL_DEVICES) == 11 and [d.name for d in sim.BASE_DEVICES] == BASE_NAMES


# -------------------------------------------------------------- POST /cells --
def test_post_cells_adds_idle_machines_and_domains(server):
    dead_port = free_port()                        # nothing listens: the cell has no PLC
    code, body = http(server, "POST", "/cells", packaging_body(dead_port))
    assert code == 201, body
    assert body["cell"] == "packaging" and body["base"] is False
    assert body["machines"] == ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1"]
    assert body["rail"] == "psu-c" and body["mode"] == "idle" and body["connected"] is False

    tick(IDLE_T0, 30)
    code, state = http(server, "GET", "/state")
    assert code == 200
    names = list(state["devices"])
    assert names[:8] == BASE_NAMES and names[8:] == body["machines"]
    for spec in PACKAGING:
        dev = state["devices"][spec["name"]]
        assert dev["cell"] == "packaging" and dev["controller"] == "127.0.0.1"
        assert dev["commanded"] == {"run": None, "speed_pct": None}
        assert dev["rail"] == "psu-c" and dev["kind"] == spec["kind"]
        assert dev["cooled"] is spec["cooled"]
        assert dev["speed_pct"] == 3.0 and dev["throughput"] == 0.0       # idle, no work
        assert dev["amps"] < spec["i_base"] * 0.03 + 0.3                  # idle draw only
        assert dev["temp"] is None or dev["temp"] < 36.0                  # a new machine starts cold
    press = state["devices"]["press-1"]
    assert press["cell"] == "stamping" and press["controller"] == "plc-stamping"
    assert state["devices"]["cnc-1"]["cell"] is None
    assert state["devices"]["cnc-1"]["commanded"] == {"run": None, "speed_pct": None}
    assert state["cells"]["packaging"]["mode"] == "idle"
    assert state["cells"]["stamping"] == {**state["cells"]["stamping"], "plc": "plc-stamping",
                                          "rail": "psu-a", "fail_open": True, "base": True,
                                          "machines": ["press-1", "press-2"], "connected": False}

    code, doms = http(server, "GET", "/domains")
    assert code == 200
    assert doms["domains"]["rail:psu-c"] == body["machines"] + ["psu-c"]
    assert doms["domains"]["loop:cool-1"][-3:] == ["pack-wrapper-1", "chiller-1", "cool-1"]
    assert doms["domains"]["net:field-1"] == ["hmi-gw", "plc-stamping", "127.0.0.1", "field-1"]

    code, cells = http(server, "GET", "/cells")
    assert code == 200 and set(cells["cells"]) == {"stamping", "packaging"}

    code, text = http(server, "GET", "/metrics")
    assert 'plant_cell_connected{namespace="plant",pod="packaging"} 0' in text
    assert 'plant_cell_connected{namespace="plant",pod="stamping"} 0' in text
    assert 'plant_current_draw_amps{namespace="plant",pod="pack-wrapper-1"}' in text
    assert 'plant_temp_celsius{namespace="plant",pod="pack-wrapper-1"}' in text
    assert "plant_commanded_speed_pct" not in text                   # no PLC, no command

    # refusals
    assert http(server, "POST", "/cells", packaging_body(dead_port))[0] == 409
    clash = [{"name": "press-1", "i_base": 5.0}]
    assert http(server, "POST", "/cells", packaging_body(dead_port, cell="x", machines=clash))[0] == 409
    assert http(server, "POST", "/cells", packaging_body(dead_port, cell="y", rail="psu-z"))[0] == 400
    nine = [{"name": f"m-{k}", "i_base": 1.0} for k in range(9)]
    assert http(server, "POST", "/cells", packaging_body(dead_port, cell="z", machines=nine))[0] == 400
    assert http(server, "POST", "/cells", {"cell": "Bad Name"})[0] == 400
    no_amps = [{"name": "m-1"}]
    assert http(server, "POST", "/cells", packaging_body(dead_port, cell="w", machines=no_amps))[0] == 400
    # a thermal time constant under MIN_TAU_S would make the explicit thermal step unstable
    fast = [{"name": "m-2", "i_base": 1.0, "cooled": True, "tau": sim.MIN_TAU_S - 1.0}]
    assert http(server, "POST", "/cells", packaging_body(dead_port, cell="v", machines=fast))[0] == 400


# --------------------------------------------------------- the fake field PLC --
def test_fake_plc_drives_run_and_speed_with_ramp(plc):
    cell = sim.add_cell(packaging_body(plc.port))
    wrapper = sim.BY_NAME["pack-wrapper-1"]
    wait_until(lambda: cell.connected and wrapper.cmd_run is not None, what="first sync")
    assert cell.mode == "closed-loop"
    assert (wrapper.cmd_run, wrapper.cmd_speed_pct) == (False, 0)   # the PLC holds it out of RUN

    t = tick(IDLE_T0, 5)
    assert wrapper.speed_frac == sim.IDLE_SPEED_FRAC and wrapper.throughput == 0.0

    # the sim wrote the 3.1 wiring to the PLC: AMPS, TEMP, VOLTS, THROUGHPUT, READY.
    # Physics is paused between ticks here, so the next sync carries these exact values.
    expect = [int(wrapper.current * 10), int(wrapper.temp * 10), int(sim.RAIL_C.voltage * 10), 0]
    wait_until(lambda: plc.sensor_words()[4:8] == expect, what="sensor words after the ticks")
    words = plc.sensor_words()
    conveyor = sim.BY_NAME["pack-conveyor-1"]
    assert words[0:4] == [int(conveyor.current * 10), 0, int(sim.RAIL_C.voltage * 10), 0]
    assert words[1] == 0 and words[9] == 0                            # not cooled: TEMP 0
    assert words[12:] == [0] * 20                                     # empty slots 3..7
    assert plc.ready_bits() == [True, True, True, False, False, False, False, False]

    plc.set_speed(1, 60)
    plc.set_run(1, True)
    wait_until(lambda: wrapper.cmd_run and wrapper.cmd_speed_pct == 60, what="RUN at 60 %")
    speeds = [wrapper.speed_frac]
    for _ in range(9):
        t = tick(t, 1)
        speeds.append(wrapper.speed_frac)
        assert abs(wrapper.current - 14.0 * wrapper.speed_frac) < 0.3   # current follows speed
        assert wrapper.throughput == pytest.approx(100.0 * wrapper.speed_frac)
    steps = [b - a for a, b in zip(speeds, speeds[1:])]
    assert all(0 <= s <= 0.10 + 1e-9 for s in steps)                  # the drive ramp
    assert speeds[1] == pytest.approx(0.13) and speeds[6] == pytest.approx(0.60)
    assert speeds[-1] == 0.60
    text = sim.metrics_text()
    assert 'plant_commanded_speed_pct{namespace="plant",pod="pack-wrapper-1"} 60.0000' in text
    assert 'plant_commanded_speed_pct{namespace="plant",pod="pack-conveyor-1"} 0.0000' in text
    assert 'plant_cell_connected{namespace="plant",pod="packaging"} 1' in text
    dev = sim.state_json()["devices"]["pack-wrapper-1"]
    assert dev["commanded"] == {"run": True, "speed_pct": 60} and dev["speed_pct"] == 60.0

    plc.set_speed(1, 20)
    wait_until(lambda: wrapper.cmd_speed_pct == 20, what="SPEED 20 %")
    t = tick(t, 1)
    assert wrapper.speed_frac == pytest.approx(0.50)                  # down at the same ramp
    t = tick(t, 5)
    assert wrapper.speed_frac == 0.20

    plc.set_run(1, False)
    wait_until(lambda: wrapper.cmd_run is False, what="RUN off")
    t = tick(t, 1)
    assert wrapper.throughput == 0.0 and wrapper.speed_frac == pytest.approx(0.10)
    t = tick(t, 2)
    assert wrapper.speed_frac == sim.IDLE_SPEED_FRAC


def test_fail_open_base_cell_reverts_to_full_speed_when_plc_goes_away(plc):
    sim.init_cells(f"stamping|127.0.0.1:{plc.port}|failopen|press-1,press-2")
    cell = sim.CELLS["stamping"]
    press = sim.BY_NAME["press-1"]
    plc.set_speed(0, 50)
    plc.set_run(0, True)
    plc.set_speed(1, 50)
    plc.set_run(1, True)
    sim.start_cell(cell)
    wait_until(lambda: cell.connected and press.cmd_speed_pct == 50, what="stamping PLC")

    t = tick(IDLE_T0, 10)
    assert press.speed_frac == 0.50 and sim.BY_NAME["press-2"].speed_frac == 0.50
    assert abs(press.current - 42.0 * 0.5) < 0.3
    assert sim.BY_NAME["cnc-1"].speed_frac == 1.0                    # not in the cell
    wait_until(lambda: plc.sensor_words()[0] > 0, what="press-1 AMPS word")
    assert 'plant_commanded_speed_pct{namespace="plant",pod="press-1"} 50.0000' in sim.metrics_text()

    plc.stop()
    wait_until(lambda: not cell.connected, what="disconnect")
    assert cell.mode == "fail-open" and cell.last_error
    assert press.cmd_run is None and press.drive_target() == 1.0

    speeds = [press.speed_frac]
    for _ in range(6):
        t = tick(t, 1)
        speeds.append(press.speed_frac)
    assert all(0 <= b - a <= 0.10 + 1e-9 for a, b in zip(speeds, speeds[1:]))
    assert speeds[1] == pytest.approx(0.60)                           # the ramp, not a jump
    assert press.speed_frac == 1.0                                    # full speed again
    t = tick(t, 30)
    rail_a = sim.RAIL_A
    i_a = sum(d.current for d in sim.BASE_DEVICES if d.rail is rail_a)
    # the old formula at full speed: i_base * friction, times the brownout factor
    assert abs(press.current - 42.0 * min(1.15, 368.0 / rail_a.voltage)) < 0.3
    assert 358 <= rail_a.voltage <= 364                              # rail A back in its idle band
    assert abs(rail_a.voltage - (rail_a.v_src - i_a * rail_a.r_src)) < 1.0
    text = sim.metrics_text()
    assert 'plant_cell_connected{namespace="plant",pod="stamping"} 0' in text
    assert "plant_commanded_speed_pct" not in text


# ------------------------------------------------------------- DELETE /cells --
def test_delete_removes_cell_and_refuses_base_cell(server):
    code, _ = http(server, "POST", "/cells", packaging_body(free_port()))
    assert code == 201
    cell = sim.CELLS["packaging"]
    code, body = http(server, "DELETE", "/cells/packaging")
    assert code == 200 and body["removed"] == "packaging"
    assert body["machines"] == [m["name"] for m in PACKAGING]
    cell.thread.join(5)
    assert not cell.thread.is_alive()                                 # the wiring thread ended

    code, state = http(server, "GET", "/state")
    assert list(state["devices"]) == BASE_NAMES and set(state["cells"]) == {"stamping"}
    code, doms = http(server, "GET", "/domains")
    assert doms["domains"]["rail:psu-c"] == ["psu-c"]
    assert "pack-wrapper-1" not in doms["domains"]["loop:cool-1"]
    assert "pack-wrapper-1" not in http(server, "GET", "/metrics")[1]
    tick(IDLE_T0, 3)                                                  # physics still runs

    code, body = http(server, "DELETE", "/cells/stamping")
    assert code == 403 and "base cell" in body["error"]
    assert "stamping" in sim.CELLS and sim.BY_NAME["press-1"].cell is sim.CELLS["stamping"]
    assert http(server, "DELETE", "/cells/packaging")[0] == 404

    # the machine names are free again
    assert http(server, "POST", "/cells", packaging_body(free_port()))[0] == 201


# ---------------------------------------------------------------- /domains --
def test_domains_membership_including_psu_c():
    assert sim.domains() == {"domains": {
        "rail:psu-a": ["press-1", "press-2", "cnc-1", "qa-scanner-1", "psu-a"],
        "rail:psu-b": ["conveyor-1", "compressor-1", "furnace-1", "chiller-1", "psu-b"],
        "rail:psu-c": ["psu-c"],
        # PS7: the board sits above every rail, so a cause can land above the plant.
        "rail:incomer-1": ["psu-a", "psu-b", "psu-c", "incomer-1"],
        "loop:cool-1": ["press-1", "press-2", "cnc-1", "furnace-1", "chiller-1", "cool-1"],
        "net:field-1": ["hmi-gw", "plc-stamping", "field-1"]}}
    sim.add_cell(packaging_body(1), start=False)
    sim.add_cell(packaging_body(1, cell="bottling", rail="psu-b", machines=[
        {"name": "filler-pump-1", "i_base": 12.0, "cooled": True},
        {"name": "capper-1", "i_base": 5.0}]), start=False)
    doms = sim.domains()["domains"]
    assert doms["rail:psu-c"] == ["pack-conveyor-1", "pack-wrapper-1", "pack-labeler-1", "psu-c"]
    assert doms["rail:psu-b"] == ["conveyor-1", "compressor-1", "furnace-1", "chiller-1",
                                  "filler-pump-1", "capper-1", "psu-b"]
    assert doms["loop:cool-1"] == ["press-1", "press-2", "cnc-1", "furnace-1",
                                   "pack-wrapper-1", "filler-pump-1", "chiller-1", "cool-1"]
    assert doms["rail:psu-a"][-1] == "psu-a"
    # a new cell never joins the supply domain: it lists rails only
    assert doms["rail:incomer-1"] == ["psu-a", "psu-b", "psu-c", "incomer-1"]
    # both new cells use the PLC 127.0.0.1: the segment lists each PLC once
    assert doms["net:field-1"] == ["hmi-gw", "plc-stamping", "127.0.0.1", "field-1"]


# ----------------------------------------------------------- BASE_CELLS env --
def test_base_cells_parsing_and_default():
    if "BASE_CELLS" not in os.environ:
        assert sim.BASE_CELLS == sim.DEFAULT_BASE_CELLS
    assert sim.parse_base_cells(sim.DEFAULT_BASE_CELLS) == [{
        "cell": "stamping", "plc_host": "plc-stamping.fleet.svc.cluster.local",
        "field_port": 5020, "fail_open": True, "machines": ["press-1", "press-2"]}]
    parsed = sim.parse_base_cells(
        "a|h1:6000|failopen|cnc-1; bad entry ;b|h2|idle|furnace-1,nope,chiller-1;c|h3:x|failopen|x")
    assert [p["cell"] for p in parsed] == ["a", "b"]
    assert parsed[1]["field_port"] == 5020 and parsed[1]["fail_open"] is False

    sim.init_cells("a|h1:6000|failopen|cnc-1;b|h2|idle|furnace-1,nope,chiller-1,cnc-1")
    assert list(sim.CELLS) == ["a", "b"]
    assert [d.name for d in sim.CELLS["b"].devices] == ["furnace-1", "chiller-1"]
    assert sim.CELLS["b"].rail is sim.RAIL_B and sim.CELLS["b"].mode == "idle"
    assert sim.BY_NAME["press-1"].cell is None
    tick(IDLE_T0, 20)
    assert sim.BY_NAME["cnc-1"].speed_frac == 1.0                    # fail-open: full speed
    assert sim.BY_NAME["furnace-1"].speed_frac == sim.IDLE_SPEED_FRAC  # no PLC, not fail-open
    assert sim.BY_NAME["furnace-1"].throughput == 0.0

    sim.init_cells("")
    assert sim.CELLS == {} and all(d.cell is None for d in sim.BASE_DEVICES)


def test_cell_frames_layout():
    cell = sim.add_cell(packaging_body(1), start=False)
    for k, d in enumerate(cell.devices):
        d.current, d.temp, d.throughput = 1.5 + k, 40.0 + k, 90.0 + k
    cell.devices[2].tripped = True
    sim.RAIL_C.voltage = 398.76
    hr, coils = sim.cell_frames(cell)
    assert len(hr) == 32 and len(coils) == 8
    assert hr[0:4] == [15, 0, 3987, 900]          # conveyor: not cooled, TEMP 0
    assert hr[4:8] == [25, 410, 3987, 910]        # wrapper: cooled
    assert hr[8:12] == [35, 0, 3987, 920]
    assert hr[12:] == [0] * 20
    assert coils == [True, True, False, False, False, False, False, False]
