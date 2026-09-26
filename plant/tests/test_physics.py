"""Physics invariants for plant/sim/main.py (LOG-038, realism pass LOG-100).

These test the MODEL, not the HTTP server: the sim module is imported (main() is guarded),
the world is stepped deterministically with the sim's own step_world, and the assertions are the
physical claims the demo makes out loud. The parameters come from published sources or are
marked project choices in main.py (sources: SCENARIOS.md section 12).

  1. Rail sag is Ohm's law over aggregate draw, rails are isolated, and a rail in normal running
     stays within about 4 % of its 400 V rating (IEC 60364-5-52).
  2. PS1 emerges: friction -> amps -> rail-A sag. Nothing scripted.
  3. Each cooled machine settles at its calibrated normal temperature, below its own trip.
  4. First-order thermal lag cannot overshoot its target.
  5. PS5 drops flow and ramps cooled machines ACROSS their own trips (bounded above them).
  6. A trip has consequences: amps ~ 0, work stops, the machine cools, THE RAIL RECOVERS.
  7. The compressor follows its pressure band: about 60 s loaded in 300 s, 25 % current unloaded.
     PS2 (the transducer fails low) keeps it loaded, and the safety valve holds the receiver.
  8. The chiller's overload relay follows the IEC 60947-4-1 class 10 curve. Normal running never
     trips it. PS2 does not trip it either: PS2 overloads the chiller's CAPACITY, so the supply
     water warms and the furnace-1 coil water crosses its 55 C trip.
  9. ANSI 27 undervoltage stops the chiller under PS7, the unit waits out its anti-recycle timer.
 10. The supply costs no random numbers at nominal, a dip sags every rail together with no machine
     leading, and the brownout threshold keeps using the fixed rating.

Run:  python -m pytest plant/tests/test_physics.py -q      (from the repo root)
"""
import importlib.util
import os
import random
import sys

import pytest

_SIM = os.path.join(os.path.dirname(__file__), "..", "sim", "main.py")
spec = importlib.util.spec_from_file_location("plant_sim", _SIM)
sim = importlib.util.module_from_spec(spec)
sys.modules["plant_sim"] = sim
spec.loader.exec_module(sim)

CHILLER = sim.BY_NAME["chiller-1"]
COMP = sim.BY_NAME["compressor-1"]


def step_world(t0, seconds, dt=1.0):
    """The sim's own physics tick, stepped without threads or a wall clock."""
    t = t0
    for _ in range(int(seconds / dt)):
        sim.step_world(t, dt)
        t += dt
    return t


def latch(t0, seconds):
    """step_world plus the OpenPLC thermal latch (plc/program.st): a machine at its trip stops.
    Returns (t, {machine: seconds after t0 when it tripped})."""
    trips, t = {}, t0
    for _ in range(int(seconds)):
        sim.step_world(t, 1.0)
        t += 1.0
        for d in sim.DEVICES:
            if d.trip_c is not None and not d.tripped and d.temp >= d.trip_c:
                d.tripped = True
                trips[d.name] = t - t0
    return t, trips


@pytest.fixture(autouse=True)
def fresh_world():
    """Deterministic, healthy world before every test."""
    reset_world(1234)
    yield


def reset_world(seed):
    """A healthy world with the given random seed."""
    random.seed(seed)
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()
    for fid in sim.FAULTS:
        sim.FAULTS[fid]["clear"]()
    sim.LOOP.pump_health = 1.0
    sim.LOOP.flow = sim.LOOP.flow_nominal
    sim.LOOP.t_supply = sim.LOOP_T_SETPOINT
    sim.SUPPLY.target = 1.0
    sim.SUPPLY.voltage = sim.SUPPLY.v_nom
    sim.AIR.pressure, sim.AIR.loaded, sim.AIR.pt_fault, sim.AIR.venting = 7.2, False, None, False
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        r.voltage = r.v_src
    for d in sim.DEVICES:
        d.friction = 1.0
        d.temp = sim.LOOP_T_SETPOINT + d.heat_k * d.i_base if d.loop is not None else sim.LOOP_T_SETPOINT
        d.current = 0.0
        d.throughput = 100.0
        d.tripped = False
        if d.overload is not None:
            d.overload.reset()
    CHILLER.unit.running, CHILLER.unit.last_start, CHILLER.unit.q_removed = True, -1e9, 0.0
    CHILLER.unit.uv.reset()


def steady(t0=0, seconds=900):
    """Settle the healthy plant: past the longest thermal lag and three compressor cycles."""
    return step_world(t0, seconds)


# ------------------------------------------------------------------- rails --
def test_rail_sag_is_ohms_law_and_within_the_iec_band():
    t = steady()
    a, b = sim.RAIL_A, sim.RAIL_B
    for _ in range(300):                              # one full compressor cycle
        t = step_world(t, 1)
        ia = sum(d.current for d in sim.DEVICES if d.rail is a)
        assert abs(a.voltage - (a.v_src - ia * a.r_src)) < 1.0
        for r in (a, b):
            assert 0.955 * r.v_src <= r.voltage < r.v_src, (r.name, r.voltage)   # at most about 4 % drop


def test_rails_are_isolated():
    t = steady()
    b_before = sim.RAIL_B.voltage
    a_before = sim.RAIL_A.voltage
    sim.FAULTS["PS1"]["apply"]()
    step_world(t, 120)
    assert a_before - sim.RAIL_A.voltage > 3.5      # A sags (about 4.6 V at 0.12 ohm)
    assert abs(sim.RAIL_B.voltage - b_before) < 7.0   # B only moves with its own compressor cycle


def test_ps1_cascade_emerges_not_scripted():
    t = steady()
    amps_before = sim.BY_NAME["press-1"].current
    v_before = sim.RAIL_A.voltage
    sim.FAULTS["PS1"]["apply"]()
    step_world(t, 180)
    press = sim.BY_NAME["press-1"]
    assert press.current > 1.6 * amps_before          # friction -> torque -> amps
    assert v_before - sim.RAIL_A.voltage > 3.5        # the sag every rail-A member sees
    # Motors run normally within 5 % of rating (NEMA MG1 allows 10 %): no brownout in PS1.
    assert sim.BY_NAME["cnc-1"].throughput > 99
    assert sim.BY_NAME["conveyor-1"].throughput > 99


# ----------------------------------------------------------------- thermal --
def test_thermal_steady_state_matches_calibration():
    steady(0, 1500)
    approx = {"press-1": 57.0, "press-2": 54.2, "cnc-1": 50.0, "furnace-1": 42.0}
    for name, want in approx.items():
        got = sim.BY_NAME[name].temp
        assert abs(got - want) < 2.0, f"{name}: {got:.1f} C vs calibrated ~{want} C"
        assert got < sim.BY_NAME[name].trip_c - 10, name   # the healthy plant lives well below trip
    assert sim.TRIP_LIMITS == {"press-1": 80.0, "press-2": 80.0, "cnc-1": 80.0, "furnace-1": 55.0}


def test_first_order_lag_never_overshoots():
    press = sim.BY_NAME["press-1"]
    peak, t = 0.0, 0
    for _ in range(60):
        t = step_world(t, 10)
        peak = max(peak, press.temp)
    assert peak < 59.0                      # target about 57 C plus the supply ripple


def test_ps5_flow_drops_and_temps_cross_their_trips_bounded():
    t = steady()
    sim.FAULTS["PS5"]["apply"]()
    t = step_world(t, 60)
    assert 48 <= sim.LOOP.flow <= 60        # 120 * 0.45 = 54
    step_world(t, 400)
    press, furnace = sim.BY_NAME["press-1"], sim.BY_NAME["furnace-1"]
    assert press.temp > press.trip_c and furnace.temp > furnace.trip_c
    assert press.temp < 95                  # the 92.4 C closed form
    assert furnace.temp < 62                # the 59.1 C closed form
    assert sim.BY_NAME["cnc-1"].temp < sim.BY_NAME["cnc-1"].trip_c   # cnc-1 stays below its trip
    assert abs(sim.LOOP.t_supply - sim.LOOP_T_SETPOINT) < 1.0         # the chiller keeps up


def test_ps5_trip_order_with_the_plc_latch():
    t = steady()
    sim.FAULTS["PS5"]["apply"]()
    _, trips = latch(t, 420)
    assert set(trips) == {"press-1", "furnace-1", "press-2"}
    assert 80 <= trips["press-1"] <= 200 and 80 <= trips["furnace-1"] <= 220
    assert trips["press-2"] > max(trips["press-1"], trips["furnace-1"])


def test_trip_consequences_amps_zero_work_stops_rail_recovers():
    t = steady()
    v_before = sim.RAIL_A.voltage
    press = sim.BY_NAME["press-1"]
    t_before = press.temp
    press.tripped = True                    # what the PLC coil does (2F)
    step_world(t, 60)
    assert press.current < 1.0              # contactor open
    assert press.throughput == 0.0          # work stopped
    assert press.temp < t_before            # cooling toward the supply water
    assert sim.RAIL_A.voltage > v_before + 4   # the rail BREATHES again: 42 A * 0.12 ohm


# ----------------------------------------------------------- compressor --
def test_the_compressor_follows_its_pressure_band():
    t = steady()
    amps = []
    for _ in range(900):
        t = step_world(t, 1)
        amps.append(COMP.current)
        assert 6.7 <= sim.AIR.pressure <= 7.7
    loaded = [a for a in amps if a > 0.6 * COMP.i_base]
    unloaded = [a for a in amps if a < 0.4 * COMP.i_base]
    assert 0.15 <= len(loaded) / len(amps) <= 0.27        # about 60 s loaded in every 300 s
    assert abs(sum(loaded) / len(loaded) - COMP.i_base) < 1.0
    share = (sum(unloaded) / len(unloaded)) / COMP.i_base
    assert 0.15 <= share <= 0.35                          # the sourced range of unloaded draw


def test_ps2_keeps_the_compressor_loaded_and_the_safety_valve_vents():
    t = steady()
    sim.FAULTS["PS2"]["apply"]()
    t = step_world(t, 240)
    assert sim.AIR.reading() == sim.PT_FAIL_BAR           # every controller sees 5.5 bar
    assert COMP.current > 0.9 * COMP.i_base               # so it never unloads
    assert sim.AIR.pressure == pytest.approx(sim.AIR_SAFETY_BAR)
    assert sim.AIR.venting
    sim.FAULTS["PS2"]["clear"]()
    step_world(t, 60)
    assert COMP.current < 0.4 * COMP.i_base               # the true reading is high: it unloads


# ------------------------------------------------------ the chiller relay --
def _time_to_trip(multiple, limit_s=10000):
    relay = sim.Overload(30.0)
    for s in range(1, limit_s + 1):
        if relay.step(30.0 * multiple, 1.0):
            return s
    return None


def test_overload_relay_follows_the_iec_class_10_curve():
    assert _time_to_trip(1.05) is None                    # no trip at 1.05 x Ir
    assert 400 <= _time_to_trip(1.2) <= 7200              # a trip at 1.2 x Ir, inside 2 h
    assert _time_to_trip(1.5) <= 240                      # class 10: at most 4 min at 1.5 x Ir
    assert 4 <= _time_to_trip(7.2) <= 10                  # class 10: 4 to 10 s at 7.2 x Ir


def test_the_relay_never_trips_in_normal_running():
    for seed in range(5):
        reset_world(seed)
        step_world(0, 3600)
        assert not CHILLER.overload.latched and not CHILLER.tripped, f"seed {seed}"
        assert CHILLER.current <= 1.02 * sim.CHILLER_RLA_A


def test_relay_holds_under_ps1_ps2_and_ps5():
    for fid in ("PS1", "PS2", "PS5"):
        reset_world(99)
        sim.FAULTS[fid]["apply"]()
        step_world(0, 1800)
        assert not CHILLER.overload.latched and not CHILLER.tripped, fid


# ------------------------------------------------------------- PS2: heat --
def test_ps2_overloads_the_chiller_capacity_and_the_supply_warms():
    t = steady()
    rest = max(sim.cooling_shortfall() for _ in range(1))
    t0_supply = sim.LOOP.t_supply
    sim.FAULTS["PS2"]["apply"]()
    t = step_world(t, 300)
    rise = sim.LOOP.t_supply - t0_supply
    assert rise > 3.0                                      # about 1 K per minute at first
    assert CHILLER.unit.running and CHILLER.unit.at_capacity(sim.LOOP.t_supply)
    assert CHILLER.current <= 1.02 * sim.CHILLER_RLA_A     # the unit limits its current to 100 % RLA
    assert sim.cooling_shortfall() > 30000.0 > rest        # W: the supply term dominates
    assert 115 <= sim.LOOP.flow <= 125 or sim.LOOP.pump_health == 1.0   # the pump is fine
    _, trips = latch(t, 1200)
    assert "furnace-1" in trips and trips["furnace-1"] + 300 < 1500    # about 16 min after the fault
    assert "press-1" not in trips and "press-2" not in trips


def test_cooling_shortfall_is_small_in_normal_running():
    t = steady()
    vals = []
    for _ in range(300):
        t = step_world(t, 1)
        vals.append(sim.cooling_shortfall())
    assert max(vals) < 15000.0 and sum(vals) / len(vals) < 6000.0     # the loaded-window ripple only


# ------------------------------------------------- PS7: undervoltage --
def test_undervoltage_stages_and_reset():
    uv = sim.Undervoltage()
    for _ in range(9):
        assert not uv.step(0.88, 1.0)       # below 90 % for 9 s: stage 1 still waits (10 s)
    assert uv.step(0.88, 1.0)               # 10 s: stage 1 trips
    assert uv.step(0.93, 1.0)               # it holds until the voltage is back above 95 %
    assert not uv.step(0.96, 1.0)
    uv2 = sim.Undervoltage()
    assert not uv2.step(0.84, 1.0) and uv2.step(0.84, 1.0)   # stage 2: 2 s below 85 %


def test_ps7_stops_the_chiller_on_undervoltage_and_the_supply_warms():
    t = steady()
    flow_before = sim.LOOP.flow
    sim.FAULTS["PS7"]["apply"]()
    t = step_world(t, 10)
    assert not CHILLER.unit.running and CHILLER.unit.uv.tripped
    assert sim.state_json()["devices"]["chiller-1"]["trip_reason"] == "undervoltage"
    assert not CHILLER.overload.latched                     # not an overload trip
    s0 = sim.LOOP.t_supply
    step_world(t, 120)
    assert sim.LOOP.t_supply - s0 > 4.0                     # no cooling at all: fast
    assert abs(sim.LOOP.flow - flow_before) < 6.0           # the pump keeps running
    assert sim.LOOP.pump_health == 1.0


def test_the_chiller_waits_for_its_anti_recycle_timer():
    unit = sim.ChillerUnit()
    unit.running, unit.last_start = False, 100.0
    unit.update(250.0, allowed=True)
    assert not unit.running                                 # 150 s after its last start
    unit.update(400.0, allowed=True)
    assert unit.running and unit.last_start == 400.0        # 300 s: it starts
    unit.update(410.0, allowed=False)
    assert not unit.running


def test_ps7_reset_restores_the_board_and_the_chiller_restarts():
    sim.FAULTS["PS7"]["apply"]()
    sim.ACTIVE.add("PS7")
    t = step_world(0, 120)
    assert sim.SUPPLY.dipped() and sim.state_json()["supply"]["dipped"] is True
    with sim._lock:
        sim.reset_plant()
    assert sim.SUPPLY.target == 1.0 and sim.ACTIVE == set()
    step_world(t, 60)
    assert abs(sim.SUPPLY.voltage - sim.SUPPLY.v_nom) < 1e-9
    assert sim.RAIL_C.voltage > 398.0                       # the idle feeder is back at the board
    assert CHILLER.unit.running                             # its last start is long past


# --------------------------------------------- the supply above the rails --
def test_supply_at_nominal_costs_no_random_numbers():
    random.seed(4242)
    before = random.getstate()
    for _ in range(500):
        sim.SUPPLY.step(120.0, 1.0)
    assert random.getstate() == before
    assert sim.SUPPLY.voltage == sim.SUPPLY.v_nom


def test_rail_v_src_is_the_fixed_rating_not_the_live_board():
    sim.FAULTS["PS7"]["apply"]()
    step_world(0, 60)
    assert sim.SUPPLY.voltage < 0.9 * sim.SUPPLY.v_nom
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        assert r.v_src == r.v_nom == 400.0
        assert abs(r.v_in - sim.SUPPLY.voltage) < 1e-9


def test_ps7_sags_every_rail_together_with_no_machine_leading():
    t = steady()
    before = {r.name: r.voltage for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C)}
    amps_before = {d.name: d.current for d in sim.DEVICES}
    sim.FAULTS["PS7"]["apply"]()
    step_world(t, 30)
    drop = sim.SUPPLY.v_nom - sim.SUPPLY.voltage
    assert abs(drop - 0.15 * 400.0) < 1.0
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        assert before[r.name] - r.voltage >= 0.8 * drop, r.name
    assert abs((before["psu-c"] - sim.RAIL_C.voltage) - drop) < 1.0
    for d in sim.DEVICES:
        if amps_before[d.name] > 1.0 and d.name not in ("compressor-1", "chiller-1"):
            assert d.current <= 1.30 * amps_before[d.name], d.name


def test_ps7_brownout_uses_the_rating_so_victims_degrade():
    t = steady()
    cnc = sim.BY_NAME["cnc-1"]
    assert cnc.throughput > 99.0
    sim.FAULTS["PS7"]["apply"]()
    step_world(t, 30)
    assert 60.0 <= cnc.throughput <= 84.0, cnc.throughput


# -------------------------------------------------------- general --
def test_reset_returns_to_steady():
    for fid in ("PS1", "PS5"):
        sim.FAULTS[fid]["apply"]()
        sim.ACTIVE.add(fid)
    t = step_world(0, 240)
    with sim._lock:
        sim.reset_plant()
    step_world(t, 900)
    assert 385.0 <= sim.RAIL_A.voltage <= 389.0
    assert abs(sim.BY_NAME["press-1"].temp - 57.0) < 2.0
    assert sim.BY_NAME["cnc-1"].throughput > 99.0


def test_hard_bounds_over_a_mixed_run():
    t = 0
    sim.FAULTS["PS1"]["apply"]()
    sim.FAULTS["PS2"]["apply"]()
    for _ in range(60):
        t = step_world(t, 10)
    for d in sim.DEVICES:
        assert 0.0 <= d.throughput <= 100.5
        if d.loop is not None:
            assert 25.0 <= d.temp <= 115.0
    assert sim.LOOP.flow >= 5.0
    for r in (sim.RAIL_A, sim.RAIL_B):
        assert r.voltage <= r.v_src + 1.0


def test_throughput_recovers_after_a_trip():
    t = steady()
    press = sim.BY_NAME["press-1"]
    assert not press.commanded() and press.running()
    press.tripped = True
    t = step_world(t, 40)
    assert press.throughput == 0.0
    press.tripped = False
    step_world(t, 60)
    assert press.current > 40.0 and press.throughput == 100.0


def test_current_is_never_negative():
    d = sim.Device("idle-probe", sim.RAIL_C, i_base=0.01)
    lowest = float("inf")
    for t in range(500):
        d.step(float(t), 1.0)
        lowest = min(lowest, d.current)
    assert lowest >= 0.0


def test_a_stall_is_never_one_giant_step():
    assert sim.tick_dt(100.0, 0.0) == sim.MAX_DT_S
    assert sim.tick_dt(10.0, 9.0) == pytest.approx(1.0)
    assert sim.tick_dt(5.0, 5.0) == pytest.approx(1e-3)
    taus = [d.tau for d in sim.DEVICES if d.loop is not None] + [sim.MIN_TAU_S]
    assert sim.MAX_DT_S / min(taus) <= 0.5
    assert sim.MAX_DT_S / sim.OL_TAU_S < 0.05               # the relay integrates smoothly


def test_cooled_machines_start_warm():
    fresh = sim.Device("press-probe", sim.RAIL_A, i_base=42.0, loop=sim.LOOP, tau=120.0, heat_k=0.69)
    assert fresh.temp == pytest.approx(sim.LOOP_T_SETPOINT + 0.69 * 42.0)
    assert sim.Device("conveyor-probe", sim.RAIL_B, i_base=18.0).temp == sim.LOOP_T_SETPOINT
    warm = {d.name: sim.LOOP_T_SETPOINT + d.heat_k * d.i_base for d in sim.DEVICES if d.loop is not None}
    step_world(0, 1500)
    for name, start in warm.items():
        assert abs(sim.BY_NAME[name].temp - start) < 1.5, f"{name}: settles far from its start"


def test_utilities_cell_carries_the_instrument_words():
    cell = sim.Cell("utilities", "plc-utilities.fleet.svc.cluster.local", 5020, sim.RAIL_B, True,
                    [COMP, CHILLER], base=True)
    steady(0, 300)
    hr, coils = sim.cell_frames(cell)
    assert len(hr) == 36 and coils[:2] == [True, True]
    assert hr[32] == int(sim.AIR.reading() * 100)
    assert hr[33] == int(sim.LOOP.t_supply * 10) and hr[34] == int(sim.LOOP.flow * 10)
    sim.FAULTS["PS2"]["apply"]()
    assert sim.cell_frames(cell)[0][32] == int(sim.PT_FAIL_BAR * 100)   # the PLC sees the bad reading
    stamping = sim.Cell("s", "plc-x", 5020, sim.RAIL_A, True, [sim.BY_NAME["press-1"]], base=True)
    assert len(sim.cell_frames(stamping)[0]) == 32                      # no instruments: 32 words
