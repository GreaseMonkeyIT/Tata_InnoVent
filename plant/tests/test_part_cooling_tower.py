"""Verification of the cooling tower and plate exchanger (plant/model/cooling_tower.py).

  1. Saturated air enthalpy: about 100 kJ/kg at 30 C (standard psychrometric tables: 100.0).
  2. The calibrated tower reproduces its rating (EVAPCO AT 14-99: 35.0 -> 29.44 C at 25.6 C WB).
  3. Approach grows with load at a fixed wet bulb (BEE guidebook table 7.2) and the cold water
     follows the wet bulb; it never goes under the wet bulb.
  4. Fill fouling (F9) and a stopped fan (natural draft only) give warmer cold water.
  5. The plate exchanger meets its design duty (250 kW, approach 1.4 K).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                          # noqa: E402
from model.cooling_tower import CoolingTower, TowerSystem, h_sat        # noqa: E402

R = C.EVAPCO_AT_14_99
M = R.flow_m3h / 3600.0 * 997.0


def test_saturated_enthalpy():
    assert h_sat(30.0) == pytest.approx(100.0, rel=0.01)


def test_rating_reproduced():
    t = CoolingTower("t", R, t_wb=R.t_wb)
    assert t.cold_water(R.t_hot, M, R.air_kgs) == pytest.approx(R.t_cold, abs=0.02)


def test_approach_grows_with_load_and_follows_wet_bulb():
    t = CoolingTower("t", R, t_wb=28.1)
    appr = []
    for rng in (2.0, 4.0, 6.0):
        th = 33.0 + rng
        appr.append(t.cold_water(th, M, R.air_kgs) - 28.1)
    assert appr[0] < appr[1] < appr[2]
    assert all(a > 0 for a in appr)
    wet = CoolingTower("t", R, t_wb=29.5)
    assert wet.cold_water(36.0, M, R.air_kgs) > t.cold_water(36.0, M, R.air_kgs)


def test_fouling_and_stopped_fan():
    t = CoolingTower("t", R, t_wb=28.1)
    base = t.cold_water(35.0, M, R.air_kgs)
    t.fouling = 0.18
    assert t.cold_water(35.0, M, R.air_kgs) > base + 0.1
    t.fouling = 0.0
    assert t.cold_water(35.0, M, R.air_kgs * R.natural_draft) > base + 1.0


def test_plate_exchanger_design_duty():
    ts = TowerSystem("cool", R, C.PHE_COOL1, t_wb=28.1)
    d = C.PHE_COOL1
    q = ts.phe.step(d["t_process_in"], d["m_process"], d["t_tower_in"], ts.m_tower)
    assert q == pytest.approx(d["m_process"] * 4186.0 * (d["t_process_in"] - d["t_process_out"]), rel=1e-6)
    assert ts.phe.t_hot_out - d["t_tower_in"] == pytest.approx(1.4, abs=0.01)
