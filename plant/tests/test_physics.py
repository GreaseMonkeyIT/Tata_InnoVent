"""Physics invariants for plant/sim/main.py (LOG-038 — operator: "check the physics").

These test the MODEL, not the HTTP server: the sim module is imported (main() is guarded),
the world is stepped deterministically, and the assertions are the physical claims the demo
makes out loud:

  1. Rail sag is Ohm's law over aggregate draw, and rails are isolated from each other.
  2. PS1 emerges: friction -> amps -> rail-A sag -> V-sensitive victims degrade. Nothing scripted.
  3. Thermal steady states sit at the LOG-032 calibration (the PS5 story depends on them).
  4. First-order thermal lag cannot overshoot its target.
  5. PS5 drops flow and ramps cooled machines ACROSS the 78 C trip line (bounded above it).
  6. A trip has consequences: amps ~ 0, work stops, machine cools, THE RAIL RECOVERS.
  7. PS2 is a duty cycle (heavy window / idle window), and stuck-on removes the cycle.
  8. Reset returns the world to steady.

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


def step_world(t0, seconds, dt=1.0):
    """One deterministic pass of the sim loop's physics (no threads, no wall clock)."""
    t = t0
    for _ in range(int(seconds / dt)):
        for rail in (sim.RAIL_A, sim.RAIL_B):
            i_total = sum(d.current for d in sim.DEVICES if d.rail is rail)
            rail.step(i_total)
        sim.LOOP.step()
        for d in sim.DEVICES:
            d.step(t, dt)
        t += dt
    return t


@pytest.fixture(autouse=True)
def fresh_world():
    """Deterministic, healthy world before every test."""
    random.seed(1234)
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()
    sim.LOOP.pump_health = 1.0
    sim.LOOP.flow = sim.LOOP.flow_nominal
    for r in (sim.RAIL_A, sim.RAIL_B):
        r.voltage = r.v_src
    for d in sim.DEVICES:
        d.friction = 1.0
        d.temp = 35.0
        d.current = 0.0
        d.throughput = 100.0
        d.tripped = False
    sim.BY_NAME["compressor-1"].duty_fn = sim.compressor_duty
    yield


# The compressor's 300 s duty cycle: t in [0,60) is the heavy window. Settle in the idle
# window (t0=61) so steady-state assertions aren't polluted by the aggressor.
IDLE_T0 = 61


def test_rail_sag_is_ohms_law_and_bounded():
    step_world(IDLE_T0, 120)
    a, b = sim.RAIL_A, sim.RAIL_B
    ia = sum(d.current for d in sim.DEVICES if d.rail is a)
    # V = v_src - I*R within noise; idle rail A ~ 361 V (111 A * 0.35 ohm)
    assert abs(a.voltage - (a.v_src - ia * a.r_src)) < 1.0
    assert 358 <= a.voltage <= 364
    assert 370 <= b.voltage <= 376          # idle rail B ~ 373 V
    assert a.voltage < a.v_src and b.voltage < b.v_src


def test_rails_are_isolated():
    step_world(IDLE_T0, 120)
    b_before = sim.RAIL_B.voltage
    sim.FAULTS["PS1"]["apply"]()            # rail-A fault only
    step_world(IDLE_T0 + 120, 120)
    assert sim.RAIL_A.voltage < 352         # A sags hard (~345-348)
    assert abs(sim.RAIL_B.voltage - b_before) < 2.0   # B never notices


def avg_thru(name, t0, ticks=12):
    """Noise-robust throughput: average over a settled window (uniform(0,3) jitter per tick)."""
    vals = []
    t = t0
    for _ in range(ticks):
        t = step_world(t, 1)
        vals.append(sim.BY_NAME[name].throughput)
    return sum(vals) / len(vals), t


def test_ps1_cascade_emerges_not_scripted():
    step_world(IDLE_T0, 120)
    amps_before = sim.BY_NAME["press-1"].current
    cnc_before, t = avg_thru("cnc-1", IDLE_T0 + 120)
    sim.FAULTS["PS1"]["apply"]()
    step_world(t, 180)
    press = sim.BY_NAME["press-1"]
    assert press.current > 1.6 * amps_before          # friction -> torque -> amps
    assert sim.RAIL_A.voltage < 352                   # deep sag, well past the idle ~361 V
    # NOTE the baseline: rail A idles at 0.90*Vsrc BY DESIGN (thin margin, LOG-032), which is
    # already inside the 0.92 brownout band — so V-sensitive machines idle near ~89-90 %, not
    # 100 %. PS1's mark is the DELTA below that, not "degraded vs perfect".
    cnc_after, _ = avg_thru("cnc-1", t + 180)
    assert cnc_after < cnc_before - 2.5               # V-sensitive victim visibly worse
    assert sim.BY_NAME["conveyor-1"].throughput > 99  # rail-B bystander untouched


def test_thermal_steady_state_matches_calibration():
    step_world(IDLE_T0, 600)                # > 5x the longest tau
    approx = {"press-1": 58.3, "press-2": 55.9, "cnc-1": 48.8, "furnace-1": 65.0}
    for name, want in approx.items():
        got = sim.BY_NAME[name].temp
        assert abs(got - want) < 3.0, f"{name}: {got:.1f} C vs calibrated ~{want} C"
        assert got < sim.TRIP_C - 5         # healthy plant lives well below trip


def test_first_order_lag_never_overshoots():
    press = sim.BY_NAME["press-1"]
    peak = 0.0
    t = IDLE_T0
    for _ in range(60):
        t = step_world(t, 10)
        peak = max(peak, press.temp)
    # target ~ 58.3; first-order + small noise must never meaningfully exceed it
    assert peak < 60.5


def test_ps5_flow_drops_and_temps_cross_trip_bounded():
    step_world(IDLE_T0, 600)                # healthy steady first
    sim.FAULTS["PS5"]["apply"]()
    step_world(IDLE_T0 + 600, 60)
    assert 48 <= sim.LOOP.flow <= 60        # 120 * 0.45 = 54
    step_world(IDLE_T0 + 660, 300)          # let the ramp play out
    press, furnace = sim.BY_NAME["press-1"], sim.BY_NAME["furnace-1"]
    assert press.temp > sim.TRIP_C          # the forecast target is genuinely crossed
    assert furnace.temp > sim.TRIP_C
    assert press.temp < 92                  # ...and bounded near the 86.8 C closed form
    assert furnace.temp < 107               # ~101.7 C closed form


def test_trip_consequences_amps_zero_work_stops_rail_recovers():
    step_world(IDLE_T0, 120)
    v_before = sim.RAIL_A.voltage
    press = sim.BY_NAME["press-1"]
    t_before = press.temp
    press.tripped = True                    # what the PLC coil does (2F)
    step_world(IDLE_T0 + 120, 60)
    assert press.current < 1.0              # contactor open
    assert press.throughput == 0.0          # work stopped
    assert press.temp < t_before            # cooling toward ambient
    assert sim.RAIL_A.voltage > v_before + 10   # the rail BREATHES again — physics


def test_ps2_is_a_duty_cycle_until_stuck_on():
    step_world(2, 30)                       # inside the heavy window (t % 300 < 60)
    high = sim.BY_NAME["compressor-1"].current
    step_world(150, 60)                     # idle window
    low = sim.BY_NAME["compressor-1"].current
    assert high > 45 and low < 12           # 55 A vs 6.6 A personalities
    sim.FAULTS["PS2"]["apply"]()
    step_world(150, 60)                     # would be idle — but it's stuck on
    assert sim.BY_NAME["compressor-1"].current > 45


def test_reset_returns_to_steady():
    for fid in ("PS1", "PS5"):
        sim.FAULTS[fid]["apply"]()
        sim.ACTIVE.add(fid)
    step_world(IDLE_T0, 240)
    for fid in list(sim.ACTIVE):            # what POST /reset does
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()
    step_world(IDLE_T0 + 240, 600)
    assert 358 <= sim.RAIL_A.voltage <= 364
    assert abs(sim.BY_NAME["press-1"].temp - 58.3) < 3.0
    # steady-state cnc-1 sits ~89-90 % (thin-margin rail keeps it in the brownout band — see
    # the NOTE in test_ps1_cascade); recovery means returning to THAT band, not to 100 %.
    cnc_steady, _ = avg_thru("cnc-1", IDLE_T0 + 840)
    assert cnc_steady > 87.0


def test_hard_bounds_over_a_mixed_run():
    t = 0
    sim.FAULTS["PS1"]["apply"]()
    for _ in range(40):
        t = step_world(t, 10)
    for d in sim.DEVICES:
        assert 0.0 <= d.throughput <= 100.5
        if d.loop is not None:
            assert 30.0 <= d.temp <= 115.0
    assert sim.LOOP.flow >= 5.0
    for r in (sim.RAIL_A, sim.RAIL_B):
        assert r.voltage <= r.v_src + 1.0
