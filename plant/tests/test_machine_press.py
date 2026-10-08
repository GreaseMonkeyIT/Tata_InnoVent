"""Verification of press-1 (plant/model/press_brake.py): an LVD PPEB 320/40 class hydraulic press
brake on the 37 kW motor, bending S355 plate.

  1. Speeds: approach 120, bend at most 14, return 130 mm/s (LVD brochure).
  2. Bend pressure = air-bending force (1.42 UTS t^2 L / V, Durma) plus friction less the ram weight,
     over the cylinder area; under the 285 bar maximum.
  3. Oil viscosity: ISO VG 46 is 46 cSt at 40 C and 6.8 at 100 C, and falls in between.
  4. Hot oil leaks more: a bend at 65 C oil takes longer than at 40 C.
  5. Heat balance: the heat into the oil = the change of stored heat + the heat to the water.
  6. With the cooler the oil stays inside 35 to 60 C (Durma); without cooling water it rises and the
     press trips at 70 C (latched).
  7. Motor: idle current near the no-load current (the pump circulates at low pressure); the bend
     current under 1.2 x rated.
  8. Faults: ram guide friction (F1) raises the bend and return pressures; pump wear (F2) slows the
     bend and heats the oil; a harder plate (F15) raises the bend pressure in proportion to UTS with
     a healthy machine.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                  # noqa: E402
from model.press_brake import PressBrake, nu_vg46                 # noqa: E402

R = C.PPEB_320_40
DT = 0.05


def make(**kw):
    p = PressBrake("press-1", R, C.ABB_37KW, **kw)
    p.motor.w = p.w_n
    return p


def run(p, secs, t_water=27.0, m_water=R.water_kgs, log=False):
    out = []
    for _ in range(int(secs / DT)):
        x0 = p.x_mm
        p.step(DT, 400.0, 50.0, t_water, m_water)
        assert p.check() == []
        if log:
            out.append((p.phase, (p.x_mm - x0) / DT, p.p_bar, p.motor.amps))
    return out


def phase_rows(log, ph):
    return [r for r in log if r[0] == ph]


def test_speeds_match_brochure():
    log = run(make(), 120.0, log=True)
    v_app = max(v for ph, v, *_ in phase_rows(log, "approach"))
    vb = sorted(v for ph, v, *_ in phase_rows(log, "bend"))
    v_bend = vb[len(vb) // 2]          # median: the first bend row still carries the approach step
    v_ret = min(v for ph, v, *_ in phase_rows(log, "return"))
    assert v_app == pytest.approx(120.0, rel=0.02)
    assert v_bend <= 14.0 + 1e-6 and v_bend > 12.0
    assert -v_ret == pytest.approx(130.0, rel=0.02)


def test_bend_pressure_from_air_bending_force():
    p = make()
    log = run(p, 120.0, log=True)
    p_bend = max(pb for ph, v, pb, i in phase_rows(log, "bend"))
    f = p.plate.force_kn() * 1e3 + p.friction_n - R.ram_mass_kg * 9.81
    assert p_bend == pytest.approx(f / R.area_m2 / 1e5, rel=0.01)
    assert p_bend < R.p_max_bar


def test_viscosity_vg46():
    assert nu_vg46(40.0) == pytest.approx(46.0, rel=1e-6)
    assert nu_vg46(100.0) == pytest.approx(6.8, rel=1e-6)
    assert nu_vg46(40.0) > nu_vg46(60.0) > nu_vg46(70.0) > nu_vg46(100.0)


def bend_time(t_oil):
    p = make(t_oil0=t_oil)
    p.c_oil *= 1e6                    # hold the oil temperature for the comparison
    log = run(p, 80.0, log=True)
    return len(phase_rows(log, "bend")) * DT


def test_hot_oil_bends_slower():
    assert bend_time(65.0) > bend_time(40.0) * 1.02


def test_heat_balance():
    p = make()
    e0 = p.c_oil * p.t_oil
    heat = water = 0.0
    for _ in range(int(1800.0 / DT)):
        p.step(DT, 400.0, 50.0, 27.0, R.water_kgs)
        heat += p.heat_w * DT
        water += p.q_water * DT
    assert heat - water == pytest.approx(p.c_oil * p.t_oil - e0, rel=0.01, abs=2e4)


def test_oil_range_and_trip_without_water():
    p = make()
    run(p, 3 * 3600.0)
    assert 35.0 <= p.t_oil <= 60.0
    q = make(t_oil0=55.0)
    run(q, 6 * 3600.0, m_water=0.0)
    assert q.phase == q.TRIPPED
    assert q.t_oil >= R.trip_oil_c - 0.5
    assert q.motor.amps == 0.0


def test_motor_idle_and_bend_currents():
    log = run(make(), 120.0, log=True)
    i_idle = min(i for ph, v, pb, i in phase_rows(log, "idle"))
    i_bend = max(i for ph, v, pb, i in phase_rows(log, "bend"))
    assert i_idle < 0.40 * C.ABB_37KW.i_a
    assert i_bend < 1.20 * C.ABB_37KW.i_a


def test_faults_friction_wear_and_harder_plate():
    def bend_p(press):
        log = run(press, 120.0, log=True)
        return (max(pb for ph, v, pb, i in phase_rows(log, "bend")),
                max(pb for ph, v, pb, i in phase_rows(log, "return")),
                len(phase_rows(log, "bend")) * DT)
    b0, r0, t0 = bend_p(make())
    f1 = make()
    f1.friction_extra_n = 0.05 * R.force_kn * 1e3
    b1, r1, _ = bend_p(f1)
    assert b1 > b0 and r1 > r0
    f2 = make()
    f2.leak_factor = 4.0
    _, _, t2 = bend_p(f2)
    assert t2 > t0 * 1.05
    f15 = make()
    f15.plate.uts_mpa = 630.0
    b15, r15, _ = bend_p(f15)
    assert (b15 - b0) / b0 == pytest.approx((630.0 - 510.0) / 510.0 * (b0 - 2.0) / b0, rel=0.25)
    assert r15 == pytest.approx(r0, rel=1e-6)
