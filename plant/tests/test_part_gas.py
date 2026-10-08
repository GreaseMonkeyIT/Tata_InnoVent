"""Verification of the fuel gas part (plant/model/gas.py) with test values (the catalog holds the
sourced plant values).

  1. A burner at full fire and its rated pressure takes its rated heat input.
  2. Orifice flow: the flow goes with the square root of the pressure.
  3. The regulator holds its outlet (minus its droop) while the supply stays above set + its
     minimum differential; below that the outlet follows the supply and every burner fires less.
  4. The low gas pressure switch locks the burner out below its set point and keeps it out until
     a reset.
  5. Two users on one header see the same supply fault together (common mode, F10).
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.gas import Burner, BurnerRating, GasSupply, GasSupplyRating   # noqa: E402

SUP = GasSupplyRating(p_supply_kpa=400.0, p_set_kpa=10.0, droop_kpa_per_m3h=0.002,
                      dp_min_kpa=50.0, k_open=0.001, lhv_mj_m3=36.0, source="test")
BUR = BurnerRating(q_rated_kw=300.0, p_rated_kpa=8.0, eff=1.0, p_low_trip_kpa=4.0, source="test")


def plant(n=2):
    s = GasSupply("gas", SUP)
    for k in range(n):
        b = Burner(f"b{k}", BUR, SUP.lhv_mj_m3)
        b.firing = 1.0
        s.burners.append(b)
    return s


def test_rated_input_at_rated_pressure():
    b = Burner("b", BUR, SUP.lhv_mj_m3)
    b.firing = 1.0
    b.update(BUR.p_rated_kpa, 1.0)
    assert b.heat_w == pytest.approx(300e3, rel=1e-9)


def test_orifice_square_root():
    b = Burner("b", BUR, SUP.lhv_mj_m3)
    b.firing = 1.0
    assert b.flow_at(2.0) / b.flow_at(8.0) == pytest.approx(0.5, rel=1e-9)


def test_regulator_holds_then_follows_supply():
    s = plant()
    s.step(1.0)
    assert s.p_out == pytest.approx(SUP.p_set_kpa - SUP.droop_kpa_per_m3h * s.flow_m3h, rel=1e-6)
    q_ok = s.heat_w()
    s.p_supply = 55.0                 # under set + dp_min: the regulator is wide open
    s.step(1.0)
    assert s.p_out < SUP.p_set_kpa - 1.0
    assert s.heat_w() < q_ok


def test_low_gas_pressure_lockout_and_reset():
    s = plant()
    s.p_supply = 52.0                 # outlet about 2 kPa: under the 4 kPa switch
    s.step(1.0)
    assert all(b.locked_out for b in s.burners)
    assert s.heat_w() == 0.0
    s.p_supply = SUP.p_supply_kpa
    s.step(1.0)
    assert all(b.locked_out for b in s.burners)        # it stays out until a reset
    for b in s.burners:
        b.reset()
    s.step(1.0)
    assert s.heat_w() > 0.0


def test_common_mode_on_two_users():
    s = plant()
    s.step(1.0)
    before = [b.heat_w for b in s.burners]
    s.p_supply = 57.0
    s.step(1.0)
    after = [b.heat_w for b in s.burners]
    ratios = [a / b for a, b in zip(after, before)]
    assert ratios[0] < 1.0 and ratios[0] == pytest.approx(ratios[1], rel=1e-9)
