"""Verification of the induction motor part model (plant/model/motor.py) against the ABB M3BP
180MLB 4 22 kW IE3 datasheet and published motor behaviour.

Acceptance bands are the IEC 60034-1 tolerances (as reproduced in manufacturer catalogues) unless a
test says otherwise: efficiency -15 % of (1 - eta) for P <= 150 kW, power factor -1/6 (1 - cos phi)
within 0.02 .. 0.07, slip +-20 %, locked-rotor current +20 %, locked-rotor torque -15 % .. +25 %,
breakdown torque -10 %.

Held out of the fit (independent checks): the 50 % load point, the part-load currents, the effect
of 90 % voltage (IEEE 141-1993 via US DOE Motor Tip Sheet 9), the V/f behaviour, the rotor loss of a
no-load start (0.5 J w^2, a textbook result), the energy balance, the starting time limit.

Run:  python -m pytest plant/tests/test_part_motor.py -q      (from the repo root)
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import motor as M                                   # noqa: E402
from model.catalog import ABB_M3BP_180MLB4_22KW as R, ABB_M3BP_180MLB4_22KW_CHECKS as CHK  # noqa: E402

W_N = R.n_rpm * math.pi / 30.0
T_N = R.t_n


def const(t):
    return lambda w: t


def settle(m, v=R.v_ll, f=R.f_hz, load=None, dt=0.02, secs=4.0, hot=True):
    """Run the motor to electrical and mechanical steady state at a fixed winding temperature."""
    load = load or const(T_N)
    if hot:
        m.t_wind = m.t_body = R.t_ref_c
    for _ in range(int(secs / dt)):
        if hot:
            m.t_wind = m.t_body = R.t_ref_c      # hold the datasheet temperature
        m.step(dt, v, f, load)
    return m


def running(load_frac=1.0, **kw):
    m = M.InductionMotor("m", R)
    m.w = W_N
    return settle(m, load=const(load_frac * T_N), **kw)


def eta(m):
    return m.t_shaft * m.w / m.p_in if m.p_in else 0.0


def pf_tol(pf):
    return min(0.07, max(0.02, (1.0 - pf) / 6.0))


# ------------------------------------------------------------------ fitted points --
def test_rated_point_matches_datasheet():
    m = running(1.0)
    assert m.check() == []
    assert m.amps == pytest.approx(R.i_a, rel=0.03)
    assert eta(m) >= R.eta - 0.15 * (1.0 - R.eta)
    assert eta(m) == pytest.approx(R.eta, abs=0.005)
    assert m.pf == pytest.approx(R.pf, abs=pf_tol(R.pf))
    slip = 1.0 - m.rpm / 1500.0
    assert slip == pytest.approx(R.s_n, rel=0.20)
    assert m.t_shaft * m.w == pytest.approx(R.p_kw * 1000.0, rel=0.01)


def test_no_load_current():
    m = running(0.0)
    assert m.amps == pytest.approx(R.i0_a, rel=0.05)
    assert m.rpm > 1495.0


def test_starting_and_breakdown_within_iec_tolerance():
    c = M.InductionMotor("m", R).c
    st = M.solve(c, R, 1.0, R.v_ll, R.f_hz)
    i_s = R.is_in * R.i_a
    assert 0.90 * i_s <= st.amps <= 1.20 * i_s
    assert 0.85 * R.ts_tn * T_N <= st.t_em <= 1.25 * R.ts_tn * T_N
    _, tmax = M.breakdown(c, R)
    assert tmax >= 0.90 * R.tmax_tn * T_N


# ------------------------------------------------------------- held-out checks --
@pytest.mark.parametrize("frac,eta_ds,pf_ds,i_ds", [
    (0.75, R.eta_75, R.pf_75, CHK["i_75_a"]),
    (0.50, R.eta_50, R.pf_50, CHK["i_50_a"]),
])
def test_part_load_matches_datasheet(frac, eta_ds, pf_ds, i_ds):
    m = running(frac)
    assert eta(m) >= eta_ds - 0.15 * (1.0 - eta_ds)
    assert eta(m) <= eta_ds + 0.01
    assert m.pf == pytest.approx(pf_ds, abs=pf_tol(pf_ds))
    assert m.amps == pytest.approx(i_ds, rel=0.05)


def test_low_voltage_effect_matches_ieee141():
    """IEEE 141-1993 (DOE Motor Tip Sheet 9) at 90 % voltage and rated load: full-load current
    +5 to +10 % (EASA gives +11 %), slip +22 % (EASA +23 %), efficiency -1 to -3 %, power factor
    +3 to +7 %, starting current -10 %. The table is for standard-efficiency motors, so the bands
    here are a little wider (project choice: +-3 points)."""
    a, b = running(1.0), running(1.0, v=0.9 * R.v_ll)
    d_i = b.amps / a.amps - 1.0
    d_s = (1.0 - b.rpm / 1500.0) / (1.0 - a.rpm / 1500.0) - 1.0
    assert 0.03 <= d_i <= 0.14
    assert 0.17 <= d_s <= 0.30
    assert -0.03 <= eta(b) - eta(a) <= 0.0
    assert b.pf > a.pf
    c = a.c
    st_a = M.solve(c, R, 1.0, R.v_ll, R.f_hz).amps
    st_b = M.solve(c, R, 1.0, 0.9 * R.v_ll, R.f_hz).amps
    assert -0.15 <= st_b / st_a - 1.0 <= -0.07


def test_constant_v_per_f_keeps_slip_speed_and_current():
    """At constant V/f the air-gap flux stays near rated, so the motor gives rated torque at about
    the same slip speed (rpm) and about rated current (the basis of V/f drives)."""
    a = running(1.0)
    b = running(1.0, v=0.5 * R.v_ll, f=0.5 * R.f_hz)
    slip_a = 1500.0 - a.rpm
    slip_b = 750.0 - b.rpm
    assert slip_b == pytest.approx(slip_a, rel=0.25)
    assert b.amps == pytest.approx(a.amps, rel=0.10)


# ------------------------------------------------------------------ dynamics --
def run_start(j_load, load, secs, dt=0.005, hot=False):
    m = M.InductionMotor("m", R, j_load=j_load)
    if hot:
        m.t_wind = m.t_body = R.t_ref_c
    log = []
    t = 0.0
    while t < secs:
        m.step(dt, R.v_ll, R.f_hz, load)
        t += dt
        log.append((t, m.amps, m.rpm, dict(m.losses), m.p_in))
        assert m.check() == []
    return m, log


def test_no_load_start_rotor_loss_equals_stored_energy():
    """Textbook result: accelerating an unloaded inertia from rest to synchronous speed dissipates
    in the rotor exactly the kinetic energy it stores, 0.5 J w_sync^2 (to within the small slip
    left at the end and the drag losses)."""
    j_load = 2.0
    m, log = run_start(j_load, const(0.0), secs=4.0, dt=0.002)
    e_rotor = sum(L["cu2"] for _, _, _, L, _ in log) * 0.002
    e_kin = 0.5 * m.j * (2.0 * math.pi * R.f_hz / (R.poles / 2.0)) ** 2
    assert e_rotor == pytest.approx(e_kin, rel=0.05)


def test_dol_start_current_and_run_up_time():
    """A DOL start: current near the locked-rotor value at first, then down to the load current.
    The run-up of a press-sized flywheel load stays inside ABB's maximum starting time from hot."""
    j_load = 3.0                                   # a 22 kW press flywheel, project choice
    load = lambda w: 0.3 * T_N * (w / W_N) ** 2     # light quadratic load during run-up
    m, log = run_start(j_load, load, secs=10.0, hot=True)
    i0 = log[0][1]
    assert i0 == pytest.approx(R.is_in * R.i_a, rel=0.15)
    t_up = next(t for t, i, rpm, _, _ in log if rpm > 0.98 * R.n_rpm)
    assert t_up < CHK["t_start_max_hot_s"]
    assert log[-1][1] < 0.6 * R.i_a


def test_energy_balance_over_a_duty_cycle():
    """Electrical energy in = energy to the load + kinetic energy change + heat stored + heat to
    the air, over a start, a load step, and a coast down."""
    dt = 0.01
    m = M.InductionMotor("m", R, j_load=1.0)
    e_in = e_load = e_air = 0.0
    ke0, heat0 = m.kinetic_energy(), m.heat_stored()
    for k in range(int(30.0 / dt)):
        t = k * dt
        v = R.v_ll if t < 25.0 else 0.0
        tl = T_N if 8.0 <= t < 25.0 else 0.0
        load = const(tl)
        m.step(dt, v, R.f_hz, load)
        e_in += m.p_in * dt
        e_load += tl * m.w * dt
        e_air += m.heat_to_air() * dt
        assert m.check() == []
    lhs = e_in
    rhs = e_load + (m.kinetic_energy() - ke0) + (m.heat_stored() - heat0) + e_air
    assert rhs == pytest.approx(lhs, rel=0.01)


def test_coast_down_stops_without_reversing():
    m = running(0.0, hot=False)
    for _ in range(int(120.0 / 0.05)):
        m.step(0.05, 0.0, R.f_hz, const(0.0))
        assert m.w >= 0.0 and m.amps == 0.0
    assert m.rpm < 300.0


# ------------------------------------------------------------------- thermal --
def test_rated_load_reaches_rated_temperature_rise():
    m = M.InductionMotor("m", R)
    m.w = W_N
    for _ in range(int(8 * 3600 / 10.0)):
        m.step(10.0, R.v_ll, R.f_hz, const(T_N))
    assert m.t_wind - R.t_amb_c == pytest.approx(R.rise_k, abs=3.0)
    assert m.t_body < m.t_wind


def test_locked_rotor_heating_matches_datasheet_starting_times():
    """With the rotor locked the windings heat fast. ABB's maximum starting times (27 s from cold,
    15 s from hot) calibrate the winding heat capacity and the limit temperature, so this checks the
    calibration through the full two-mass model (body coupling included), not an independent fact.
    The implied limit must be a sane short-time winding temperature (above class F 155 C, below
    300 C)."""
    def time_to_limit(t0):
        m = M.InductionMotor("m", R)
        m.t_wind, m.t_body = t0, min(t0, R.t_amb_c + 0.6 * R.rise_k)
        m.locked = True
        t = 0.0
        while m.t_wind < m.t_limit_c and t < 200.0:
            m.step(0.05, R.v_ll, R.f_hz, const(0.0))
            assert m.w == 0.0 and m.check() == []
            t += 0.05
        return t, m.t_limit_c
    (cold, lim), (hot, _) = time_to_limit(R.t_amb_c), time_to_limit(R.t_ref_c)
    print(f"locked rotor to {lim:.0f} C: cold {cold:.1f} s (ABB 27 s), hot {hot:.1f} s (ABB 15 s)")
    assert 155.0 < lim < 300.0
    assert cold == pytest.approx(CHK["t_start_max_cold_s"], rel=0.10)
    assert hot == pytest.approx(CHK["t_start_max_hot_s"], rel=0.10)


# -------------------------------------------------------------------- faults --
def test_bearing_friction_raises_current_and_lowers_speed():
    a = running(0.8)
    b = M.InductionMotor("m", R)
    b.w, b.friction_nm = W_N, 0.15 * T_N
    settle(b, load=const(0.8 * T_N))
    assert b.amps > a.amps * 1.08
    assert b.rpm < a.rpm


def test_blocked_cooling_raises_winding_temperature_only():
    def soak(cooling):
        m = M.InductionMotor("m", R)
        m.w, m.cooling = W_N, cooling
        for _ in range(int(8 * 3600 / 10.0)):
            m.step(10.0, R.v_ll, R.f_hz, const(0.8 * T_N))
        return m
    a, b = soak(1.0), soak(0.6)
    assert b.t_wind > a.t_wind + 15.0
    assert b.amps == pytest.approx(a.amps, rel=0.03)    # hotter windings: a little more slip only


def test_broken_rotor_bars_raise_slip():
    a = running(1.0)
    b = M.InductionMotor("m", R)
    b.w, b.rotor_r_factor = W_N, 1.3
    settle(b)
    slip_a, slip_b = 1500.0 - a.rpm, 1500.0 - b.rpm
    assert slip_b / slip_a == pytest.approx(1.3, rel=0.08)
    assert b.amps == pytest.approx(a.amps, rel=0.03)


def test_star_delta_start_with_large_step_stays_stable():
    """Regression (found by the CP-1 concept run): a star-delta start with a 0.1 s step once made
    the speed overshoot synchronous speed and then diverge. The speed must stay below synchronous
    speed while motoring, settle at the load speed, and the checks must stay clean."""
    m = M.InductionMotor("m", R)
    sync = 2.0 * math.pi * R.f_hz / (R.poles / 2.0)
    load = lambda w: 0.6 * T_N * min(1.0, max(w, 0.0) / (0.05 * W_N))
    for k in range(int(120.0 / 0.1)):
        t = k * 0.1
        v = R.v_ll / math.sqrt(3.0) if t < 6.0 else R.v_ll
        m.step(0.1, v, R.f_hz, load)
        assert m.w <= sync * 1.001 and math.isfinite(m.w)
        assert m.check() == []
    assert 1480.0 < m.rpm < 1500.0
    assert m.amps == pytest.approx(running(0.6).amps, rel=0.05)
