"""The truck frame line (frame_line.py), the 160 kW motor, the site weather and the material flow.

  1. The 160 kW motor fit meets its catalogue points (ABB 9AKK106128: 284 A, PF 0.85, 95.8 %).
  2. pierce-1: punch force F = pi d t tau; a worn punch needs more force (F4); the punch is changed
     at its life.
  3. lmp-1: a U channel of 8 mm S500MC over 9 m needs about 1,300 t, inside the 5,000 t press, and
     the stroke stays inside the press.
  4. ced-1: the deposited charge is film mass / coulomb yield; the bath stays near its setpoint with
     chiller-1; the heated tanks hold their setpoints.
  5. rivet-1: 120 fasteners per frame with two tools; install forces scatter.
  6. Weather: the wet bulb from dry bulb, humidity and pressure matches the psychrometric chart.
  7. Flow: the handling times have the stated mean and CV; the plant shows no exact machine period
     in the stations that people or arrivals drive.
"""
import math
import statistics
import random

import pytest

from plant.model import catalog as C
from plant.model.motor import InductionMotor, solve
from plant.model import frame_line as FL
from plant.model.chiller import AirCooledChiller
from plant.model.gas import GasSupply
from plant.model.press_brake import Plate, PressBrake
from plant.model.weather import wet_bulb
from plant.model.flow import HANDLING_CV, handling_s


def test_160kw_motor_meets_its_catalogue_point():
    r = C.ABB_160KW
    m = InductionMotor("m", r)
    w = r.n_rpm * math.pi / 30.0
    t_n = r.p_kw * 1000.0 / w
    s_n = 1.0 - r.n_rpm / (120.0 * r.f_hz / r.poles)
    op = solve(m.c, r, s_n, r.v_ll, r.f_hz)
    assert op.amps == pytest.approx(r.i_a, rel=0.05)
    assert op.pf == pytest.approx(r.pf, abs=0.03)
    assert t_n == pytest.approx(1026.0, rel=0.01)


def _punch():
    return FL.PunchLine("pierce-1", C.PUNCH_450, C.ABB_M3BP_180MLB4_22KW, C.SIDE_MEMBER)


def test_punch_force_from_shear_and_wear():
    p = _punch()
    f_new = p.force_kn()
    expect = math.pi * 17.0 * 8.0 * 0.8 * 625.0 / 1000.0
    assert f_new == pytest.approx(expect, rel=1e-6)
    assert f_new < C.PUNCH_450.force_kn
    p.wear = 1.0
    assert p.force_kn() == pytest.approx(f_new * (1.0 + C.PUNCH_450.wear_force_gain), rel=1e-6)


def test_punch_pierces_a_member_and_changes_its_punch():
    p = _punch()
    p.motor.w = p.w_n
    p.hits = C.PUNCH_450.life_hits - 5
    p.begin(20)
    for _ in range(int(900 / 0.05)):
        p.step(0.05, 400.0, 50.0)
        assert not p.check()
    assert p.members == 1 and p.holes == 20 and p.punches == 1
    assert p.state == p.IDLE


def test_punch_wears_faster_under_f4():
    a, b = _punch(), _punch()
    b.wear_rate = 3.0
    for p in (a, b):
        p.motor.w = p.w_n
        p.begin(200)
        for _ in range(int(300 / 0.05)):
            p.step(0.05, 400.0, 50.0)
    assert b.wear == pytest.approx(3.0 * a.wear, rel=0.02)
    assert b.last_force_kn > a.last_force_kn


def test_long_member_press_forms_a_u_channel():
    plate = Plate(**vars(C.SIDE_MEMBER_U))
    assert 1200.0 < plate.force_kn() / 9.81 < 1400.0                  # about 1,300 t
    lmp = PressBrake("lmp-1", C.LMP_5000, C.ABB_160KW, n_motors=2, plate=plate)
    lmp.motor.w = lmp.w_n
    p_max = 0.0
    for _ in range(int(120 / 0.05)):
        lmp.step(0.05, 400.0, 50.0, 30.0, 1.2)
        assert not lmp.check()
        if lmp.phase == lmp.BEND:
            p_max = max(p_max, lmp.p_bar)
        if lmp.bends >= 1:
            lmp.auto = False
    assert lmp.bends >= 1
    assert p_max == pytest.approx(plate.force_kn() * 1e3 / C.LMP_5000.area_m2 / 1e5, rel=0.25)


def _ced():
    gas = GasSupply("gas-1", C.GAS_SUPPLY)
    ch = AirCooledChiller("chiller-1", C.EWAD190AJYNN, process_setpoint_c=29.0, t_amb_c=35.0)
    return FL.CedLine("ced-1", C.CED_LINE, gas, ch, t_shop=30.0), gas, ch


def test_ced_charge_bath_and_tanks():
    ced, gas, ch = _ced()
    ch.v = 400.0
    area = 2 * C.SIDE_MEMBER.area_m2 + 6 * 0.8
    for _ in range(4):
        assert ced.begin(842.0, area)
        while ced.busy:
            gas.step(0.5)
            ced.step(0.5)
            assert not ced.check()
    r = C.CED_LINE
    q_expect = area * 1e4 * r.film_um * 1e-4 * r.film_density * 1000.0 / r.yield_mg_c
    # three time constants in the deposition time: 95 % of the charge per load
    assert ced.charge_c == pytest.approx(4 * 0.95 * q_expect, rel=0.03)
    assert abs(ced.t_bath - r.bath_c) < 2.0
    assert ced.tanks[0].t == pytest.approx(r.degrease_c, abs=2.0)
    assert ced.tanks[1].t == pytest.approx(r.phosphate_c, abs=2.0)
    for _ in range(int(1500 / 0.5)):
        gas.step(0.5)
        ced.step(0.5)
    assert ced.loads == 4


def test_rivet_station_two_tools():
    rv = FL.RivetStation("rivet-1", C.RIVET)
    rv.begin()
    t, forces = 0.0, []
    while rv.busy:
        for k in range(C.RIVET.tools):
            rv.swage(k)
        rv.step(0.1)
        t += 0.1
        forces.append(rv.last_kn)
    assert rv.frames == 1 and rv.fasteners == C.RIVET.fasteners
    assert t == pytest.approx(C.RIVET.fasteners / C.RIVET.tools * C.RIVET.swage_s, rel=0.05)
    assert statistics.pstdev(set(forces) - {0.0}) > 0.0


def test_wet_bulb_matches_the_chart():
    assert wet_bulb(35.0, 40.0, 101.325) == pytest.approx(24.0, abs=0.3)
    assert wet_bulb(30.0, 100.0, 101.325) == pytest.approx(30.0, abs=0.05)
    assert wet_bulb(25.0, 50.0, 101.325) == pytest.approx(18.0, abs=0.3)


def test_handling_times_have_the_stated_mean_and_cv():
    rng = random.Random(1)
    x = [handling_s(rng, 120.0) for _ in range(20000)]
    m = statistics.mean(x)
    assert m == pytest.approx(120.0, rel=0.02)
    assert statistics.pstdev(x) / m == pytest.approx(HANDLING_CV, rel=0.05)
    assert min(x) > 0.0
