"""Verification of the first engine assembly stations (plant/model/assembly.py).

  1. Leak test: a 12 scc/min leak on a part at room temperature decays about P_atm Q t / V and is
     near the reject line; a tight part at room temperature passes; a tight part 30 K warm from the
     washer fails (the F13 false reject, ideal gas dP/P = dT/T); low plant air means no fill.
  2. Washer: the tank holds 80 C; a heater control fault (F13) holds it hotter and the block leaves
     warmer.
  3. Nutrunner: final torques scatter with 3 sigma under 4 %; a worn socket widens it; a dip under
     0.7 pu resets the controller.
  4. Pallet conveyor: one engine per takt; a jam stops the transfers and keeps the drives loaded.
"""
import os
import statistics
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import assembly as A                                     # noqa: E402
from model import catalog as C                                      # noqa: E402


def test_leak_test_leak_thermal_and_air():
    lt = A.LeakTester("leak-1", C.LEAK_TEST)
    d0, ok0, _ = lt.test(35.0)
    assert ok0 and d0 == pytest.approx(0.0, abs=1e-9)
    lt.leak_sccm = 12.0
    d, ok, _ = lt.test(35.0)
    assert d == pytest.approx(101325.0 * 12e-6 / 60.0 / 0.012 * 25.0, rel=1e-9)
    lt.leak_sccm = 0.0
    dw, okw, _ = lt.test(65.0)
    assert not okw and dw > C.LEAK_TEST.reject_pa
    lt.air_bar = 2.0
    _, _, fill = lt.test(35.0)
    assert not fill


def test_washer_temperature_and_f13():
    w = A.PartsWasher("washer-1", C.MECWASH)
    for _ in range(int(4 * 3600 / 5.0)):
        w.step(5.0)
    assert w.t_tank == pytest.approx(80.0, abs=2.0)
    out = w.t_part_out
    f = A.PartsWasher("washer-1", C.MECWASH)
    f.offset_k = 10.0
    for _ in range(int(4 * 3600 / 5.0)):
        f.step(5.0)
    assert f.t_part_out > out + 7.0


def test_nutrunner_scatter_wear_and_dip():
    def scatter(wear):
        s = A.TighteningStation("tight-1", C.NUTRUNNER, seed=1)
        s.socket_wear = wear
        for _ in range(int(3600 / 0.5)):
            s.step(0.5, 1.0)
        return statistics.pstdev(s.torques) / statistics.mean(s.torques)
    assert 3.0 * scatter(0.0) < 0.04
    assert scatter(1.0) > 2.5 * scatter(0.0)
    s = A.TighteningStation("tight-1", C.NUTRUNNER)
    for _ in range(5):
        s.step(0.5, 1.0)
    s.step(0.1, 0.5)
    assert s.resets == 1


def test_pallet_conveyor_takt_and_jam():
    c = A.PalletConveyor("pallet-1", C.TS5_LINE, takt_s=290.0)
    for _ in range(int(3 * 3600 / 1.0)):
        c.step(1.0)
    assert c.moved == pytest.approx(3 * 3600 / 290.0, abs=1.0)
    n = c.moved
    c.jam = True
    for _ in range(1200):
        c.step(1.0)
    assert c.moved == n and c.p_el > 0.85 * 1000 * 0.37 * 8


def test_cold_test_reference_and_tight_bearing():
    c = A.ColdTest("cold-1", C.QSL9_F85)
    ref = c.reference_nm()
    assert ref == pytest.approx(A.fmep_motored_bar(500.0) * 1e5 * 8.9e-3 / (4 * 3.141592653589793), rel=1e-9)
    for _ in range(int(900 / 0.5)):
        c.step(0.5)
    assert all(ok for _, ok in c.results)
    c.tight = 0.35                      # F16: a tight bearing, a product fault
    for _ in range(int(900 / 0.5)):
        c.step(0.5)
    assert not c.results[-1][1]


def test_hot_test_balance_regen_and_dyno_trip():
    h = A.HotTest("hot-1", C.QSL9_F85)
    rows = []
    for _ in range(int(2400 / 1.0)):
        h.step(1.0)
        rows.append((h.load, h.p_brake, h.q_coolant, h.p_el))
    rated = [r for r in rows if r[0] == 1.0]
    assert rated and rated[0][1] == pytest.approx(306e3)
    assert rated[0][2] == pytest.approx(145e3, rel=0.01)       # the spec-sheet coolant heat at rated
    assert rated[0][3] < -250e3                                  # the dyno feeds power back
    low = [r for r in rows if r[0] == 0.25][0]
    assert low[2] / (low[1] / h.efficiency(0.25)) > rated[0][2] / (rated[0][1] / h.efficiency(1.0))
    h.dyno_trip = True                                           # F12: the regenerated power stops
    h.step(1.0)
    assert h.p_el > 0.0 and h.q_coolant == 0.0
