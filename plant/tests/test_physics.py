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
  9. The chiller-1 overload relay (SCENARIOS.md 2.2) lives in Device.step, so this file's own
     step_world drives it. Normal cycles never trip it. PS2 trips it within 100 s from any
     cycle phase, and then the loop flow falls while pump health stays 1.0.
 10. PS7 (SCENARIOS.md 2.8): the supply above the rails costs no random numbers at nominal, a
     dip sags EVERY rail together with no machine leading, and the brownout threshold keeps
     using the fixed rating and not the dipped supply.

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
        sim.SUPPLY.step(sum(d.current for d in sim.DEVICES), dt)
        for rail in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
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
    reset_world(1234)
    yield


def reset_world(seed):
    """A healthy world with the given random seed."""
    random.seed(seed)
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()
    sim.LOOP.pump_health = 1.0
    sim.LOOP.flow = sim.LOOP.flow_nominal
    sim.SUPPLY.target = 1.0
    sim.SUPPLY.voltage = sim.SUPPLY.v_nom
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        r.voltage = r.v_src
    for d in sim.DEVICES:
        d.friction = 1.0
        d.temp = 35.0
        d.current = 0.0
        d.throughput = 100.0
        d.tripped = False
        if d.overload is not None:
            d.overload.reset()              # a latched relay would trip the chiller again
    sim.BY_NAME["compressor-1"].duty_fn = sim.compressor_duty


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
    step_world(IDLE_T0, 1500)               # > 5x the longest tau (furnace-1, 270 s)
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


# ----------------------------------------------- PS2: the chiller-1 overload relay --
def test_relay_never_trips_in_normal_cycles():
    """A normal 60 s compressor window adds about 61 heat, and the 240 s idle drains it.
    Twelve full cycles per seed, ten seeds: the relay never latches."""
    chiller = sim.BY_NAME["chiller-1"]
    for seed in range(10):
        reset_world(seed)
        peak, t = 0.0, 0
        for _ in range(360):
            t = step_world(t, 10)
            peak = max(peak, chiller.overload.heat)
        assert not chiller.overload.latched and not chiller.tripped, f"seed {seed}"
        assert 40.0 < peak < 0.8 * sim.CHILLER_OL_TRIP, f"seed {seed}: peak heat {peak:.1f}"
        assert 108 <= sim.LOOP.flow <= 132                  # the loop keeps its full flow


def test_relay_holds_under_ps1_and_ps5():
    """PS1 loads rail A and PS5 weakens the pump. Neither reaches the chiller's rail B."""
    for fid in ("PS1", "PS5"):
        sim.FAULTS[fid]["apply"]()
    step_world(0, 1800)
    chiller = sim.BY_NAME["chiller-1"]
    assert not chiller.overload.latched and not chiller.tripped


def test_ps2_trips_the_chiller_within_100_s_from_any_phase():
    """Stuck-on keeps rail B low, so the chiller stays above pickup until the relay trips.
    The trip time depends on the heat left from the last window, so it depends on the phase."""
    chiller = sim.BY_NAME["chiller-1"]
    delays = {}
    for phase in range(0, 300, 10):
        reset_world(phase)
        t = step_world(0, 300 + phase)                      # one full cycle, then the phase
        sim.FAULTS["PS2"]["apply"]()
        for s in range(1, 101):
            t = step_world(t, 1)
            if chiller.tripped:
                delays[phase] = s
                break
        assert phase in delays, f"PS2 at phase {phase} did not trip the relay in 100 s"
        assert chiller.overload.latched
        step_world(t, 60)
        assert chiller.current < 1.0                        # the contactor is open
        assert 66 <= sim.LOOP.flow <= 78                    # 120 * 0.60 = 72 (LOG-073)
        assert sim.LOOP.pump_health == 1.0                  # the pump is healthy. The chiller is off.
        sim.FAULTS["PS2"]["clear"]()
    assert min(delays.values()) >= 25 and max(delays.values()) <= 100


def test_cooling_shortfall_zero_at_rest_and_large_after_ps2_trip():
    rest = []
    t = IDLE_T0
    for _ in range(120):
        t = step_world(t, 1)
        rest.append(sim.cooling_shortfall())
    assert max(rest) < 3.0 and sum(rest) / len(rest) < 1.0
    sim.FAULTS["PS2"]["apply"]()
    step_world(t, 180)
    assert sim.BY_NAME["chiller-1"].tripped
    # more than every victim's own heat load (furnace-1 30 W, press-1 about 45 W under PS1)
    assert sim.cooling_shortfall() > 50.0


def test_reset_clears_the_relay_and_restores_flow():
    sim.FAULTS["PS2"]["apply"]()
    sim.ACTIVE.add("PS2")
    step_world(0, 200)
    chiller = sim.BY_NAME["chiller-1"]
    assert chiller.tripped and sim.state_json()["devices"]["chiller-1"]["trip_reason"] == "overload"
    with sim._lock:
        sim.reset_plant()
    assert not chiller.tripped and chiller.overload.heat == 0.0 and not chiller.overload.latched
    assert sim.ACTIVE == set()
    step_world(200, 60)                                     # an idle window: no re-trip
    assert not chiller.tripped and 108 <= sim.LOOP.flow <= 132
    assert sim.state_json()["devices"]["chiller-1"]["trip_reason"] is None


# --------------------------------------------- PS7: the supply above the rails --
def test_supply_at_nominal_costs_no_random_numbers():
    """Supply.step must draw nothing. A new draw would shift every downstream rail sample and
    silently invalidate every calibrated bound in this file."""
    random.seed(4242)
    before = random.getstate()
    for _ in range(500):
        sim.SUPPLY.step(120.0, 1.0)
    assert random.getstate() == before
    assert sim.SUPPLY.voltage == sim.SUPPLY.v_nom       # the target is 1.0, so it never moves


def test_rail_v_src_is_the_fixed_rating_not_the_live_board():
    """The console bands, /state, and Device.step all read v_src. It must stay the RATING."""
    sim.FAULTS["PS7"]["apply"]()
    step_world(IDLE_T0, 60)
    assert sim.SUPPLY.voltage < 0.9 * sim.SUPPLY.v_nom
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        assert r.v_src == r.v_nom == 400.0
        assert abs(r.v_in - sim.SUPPLY.voltage) < 1e-9


def test_ps7_sags_every_rail_together_with_no_machine_leading():
    """An external dip is COMMON MODE. Every rail falls by the board's drop, and no machine
    current leads it. That is what separates PS7 from PS1 and PS2 (SCENARIOS.md 4.5)."""
    step_world(IDLE_T0, 120)
    before = {r.name: r.voltage for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C)}
    amps_before = {d.name: d.current for d in sim.DEVICES}
    sim.FAULTS["PS7"]["apply"]()
    t = step_world(IDLE_T0 + 120, 30)                   # past the 3 s ramp
    drop = sim.SUPPLY.v_nom - sim.SUPPLY.voltage
    assert abs(drop - 0.15 * 400.0) < 1.0               # SUPPLY_DIP_PCT 0.85
    for r in (sim.RAIL_A, sim.RAIL_B, sim.RAIL_C):
        assert before[r.name] - r.voltage >= 0.9 * drop, r.name
    # the idle spare feeder carries no load, so its sag IS the board's sag
    assert abs((before["psu-c"] - sim.RAIL_C.voltage) - drop) < 1.0
    # no machine led the sag: constant-power loads only rose AFTER the board fell, and by
    # the brownout cap at most. PS1 and PS2 raise one machine far past this.
    for d in sim.DEVICES:
        if amps_before[d.name] > 1.0:
            assert d.current <= 1.30 * amps_before[d.name], d.name


def test_ps7_brownout_uses_the_rating_so_victims_degrade_further():
    """If the brownout threshold tracked the dipped board, the branch would nearly stop working.
    With the live board (340 V) cnc-1 would hold about 87 % throughput. With the rating (400 V)
    it falls to about 74 %. The bound below rules out the live-board formula."""
    step_world(IDLE_T0, 120)
    cnc = sim.BY_NAME["cnc-1"]
    steady = cnc.throughput
    assert steady > 87.0                                # the calibrated healthy value
    sim.FAULTS["PS7"]["apply"]()
    step_world(IDLE_T0 + 120, 30)
    assert 60.0 <= cnc.throughput <= 80.0, cnc.throughput


def test_ps7_trips_the_chiller_within_100_s_from_any_phase():
    """PS7 reaches the chiller the same way PS2 does, through its overload relay, but the cause
    sits above the plant instead of on rail B."""
    chiller = sim.BY_NAME["chiller-1"]
    for phase in (0, 61, 150, 250):
        reset_world(7000 + phase)
        step_world(phase, 30)
        sim.FAULTS["PS7"]["apply"]()
        t = phase + 30
        for _ in range(10):
            t = step_world(t, 10)
            if chiller.tripped:
                break
        assert chiller.tripped, f"phase {phase}: no trip in 100 s"
        assert chiller.overload.latched
    step_world(t, 60)
    assert sim.LOOP.flow < 0.8 * sim.LOOP.flow_nominal  # the residual, 0.60 nominal (LOG-073)
    assert sim.LOOP.pump_health == 1.0                  # PS7 never touches the pump


def test_ps7_reset_restores_the_board_and_the_rails():
    sim.FAULTS["PS7"]["apply"]()
    sim.ACTIVE.add("PS7")
    step_world(IDLE_T0, 120)
    assert sim.SUPPLY.dipped() and sim.state_json()["supply"]["dipped"] is True
    with sim._lock:
        sim.reset_plant()
    assert sim.SUPPLY.target == 1.0 and sim.ACTIVE == set()
    step_world(IDLE_T0 + 120, 60)
    assert abs(sim.SUPPLY.voltage - sim.SUPPLY.v_nom) < 1e-9
    assert sim.RAIL_C.voltage > 398.0                   # the idle feeder is back at the board


# ------------------------------------ LOG-075: the review before the sim code freeze --
def test_throughput_recovers_after_a_trip_under_the_brownout_line():
    """press-1 runs here with no PLC command (fail-open, no field thread) on rail A, and rail A
    idles under the 368 V brownout line. After its trip clears, press-1 must climb back to 100 %.
    The old voltage gate kept it near 0 % while it drew full current."""
    step_world(IDLE_T0, 120)
    press = sim.BY_NAME["press-1"]
    assert not press.commanded() and press.running()
    press.tripped = True
    t = step_world(IDLE_T0 + 120, 40)
    assert press.throughput == 0.0
    press.tripped = False
    step_world(t, 60)
    assert sim.RAIL_A.voltage < 0.92 * sim.RAIL_A.v_src      # still under the line
    assert press.current > 40.0 and press.throughput == 100.0


def test_current_is_never_negative():
    """The 0.05 A noise alone would put a small idle load below 0 A on about 4 in 10 ticks."""
    d = sim.Device("idle-probe", sim.RAIL_C, i_base=0.01)
    lowest = float("inf")
    for t in range(500):
        d.step(float(t), 1.0)
        lowest = min(lowest, d.current)
    assert lowest >= 0.0


def test_a_stall_is_never_one_giant_step():
    """loop() takes its step from the wall clock. A stalled process must not integrate the
    whole stall at once: forward Euler overshoots when dt passes tau."""
    assert sim.tick_dt(100.0, 0.0) == sim.MAX_DT_S
    assert sim.tick_dt(10.0, 9.0) == pytest.approx(1.0)
    assert sim.tick_dt(5.0, 5.0) == pytest.approx(1e-3)
    taus = [d.tau for d in sim.DEVICES if d.loop is not None] + [sim.MIN_TAU_S]
    assert sim.MAX_DT_S / min(taus) <= 0.5                     # stable, and no overshoot
    assert sim.MAX_DT_S * sim.CHILLER_OL_UP_PER_S < 0.1 * sim.CHILLER_OL_TRIP


def test_cooled_machines_start_warm():
    """A plant-sim restart restarts the model, not the plant. A cooled machine starts at its
    healthy steady temperature, which is where it settles anyway, so a restart shows no
    20-minute warm-up ramp to the engine."""
    fresh = sim.Device("press-probe", sim.RAIL_A, i_base=42.0, loop=sim.LOOP, tau=120.0)
    assert fresh.temp == pytest.approx(35.0 + 0.55 * 42.0)
    assert sim.Device("conveyor-probe", sim.RAIL_B, i_base=18.0).temp == 35.0   # not cooled
    warm = {d.name: 35.0 + d.heat_k * d.i_base for d in sim.DEVICES if d.loop is not None}
    step_world(IDLE_T0, 1500)                                  # the fixture starts them at 35 C
    for name, start in warm.items():
        assert abs(sim.BY_NAME[name].temp - start) < 1.5, f"{name}: settles far from its start"
