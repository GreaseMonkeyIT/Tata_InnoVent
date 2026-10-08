"""Verification of press-2 (plant/model/mech_press.py): an Aida NC1-2000 class mechanical press on
a high-slip motor (Wannan YH2-160L-4).

  1. A normal stroke slows the flywheel 10 to 15 % (The Fabricator, "Stamping 101"), the flywheel
     is back at speed before the next stroke, and the high-slip motor's peak current stays near
     2 x rated (the flywheel, not the motor, does the work).
  2. Low clutch air (under the 4.5 bar pressure switch: a plant air leak, F6) blocks the strokes.
  3. Brake wear past the monitor limit stops the press (OSHA 1910.217 (b)(14) brake monitor).
  4. Flywheel bearing friction raises the idle current; belt slip lowers the flywheel speed.
  5. A thicker blank is a load: a deeper slowdown and more peak current, the same idle current.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                    # noqa: E402
from model.mech_press import MechanicalPress                       # noqa: E402

DT = 0.005


def run(secs=120.0, setup=None):
    p = MechanicalPress("press-2", C.AIDA_NC1_2000, C.YH2_160L_4)
    p.motor.w = p.w_m_n
    if setup:
        setup(p)
    peak, slows, before = 0.0, [], []
    for _ in range(int(secs / DT)):
        st = p.state
        p.step(DT, 400.0, 50.0)
        assert p.check() == []
        if p.state == "stroke":
            peak = max(peak, p.motor.amps)
        if st == "idle" and p.state == "stroke":
            before.append(p.fw_start)
        if st == "stroke" and p.state == "braking":
            slows.append(p.slowdown())
    return p, peak, slows, before


def test_normal_stroke():
    p, peak, slows, before = run()
    assert p.strokes >= 10
    assert all(0.10 <= s <= 0.15 for s in slows[2:])
    assert max(before[2:]) - min(before[2:]) < 0.01 * max(before)     # recovered before each stroke
    assert peak / C.YH2_160L_4.i_a < 3.0


def test_low_clutch_air_blocks_strokes():
    p, *_ = run(60.0, setup=lambda p: setattr(p, "air_bar", 4.0))
    assert p.strokes == 0


def test_brake_monitor_stops_a_worn_brake():
    p, *_ = run(60.0, setup=lambda p: setattr(p, "brake_wear", 0.3))
    assert p.state == p.LOCKED and p.strokes == 1


def test_friction_and_belt_slip():
    base, *_ = run(30.0, setup=lambda p: setattr(p, "auto", False))
    fric, *_ = run(30.0, setup=lambda p: (setattr(p, "auto", False),
                                          setattr(p.motor, "friction_nm", 0.15 * p.motor.rating.t_n)))
    assert fric.motor.amps > base.motor.amps * 1.05
    slip, *_ = run(30.0, setup=lambda p: (setattr(p, "auto", False), setattr(p, "belt_slip", 0.05)))
    assert slip.fw_rpm() < base.fw_rpm() * 0.96


def test_thicker_blank_is_a_load():
    a, pa, sa, _ = run()
    b, pb, sb, _ = run(setup=lambda p: setattr(p.blank, "t_mm", 12.0))
    assert sb[-1] > sa[-1] and pb > pa
    idle_a, *_ = run(20.0, setup=lambda p: setattr(p, "auto", False))
    idle_b, *_ = run(20.0, setup=lambda p: (setattr(p, "auto", False), setattr(p.blank, "t_mm", 12.0)))
    assert idle_b.motor.amps == pytest.approx(idle_a.motor.amps, rel=1e-6)
