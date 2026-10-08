"""Verification of furnace-1 (plant/model/furnace.py): a gas-fired car-bottom stress-relieving
furnace on the plant gas header (gas.py), running an AWS D1.1 postweld heat treatment.

  1. Steel heat capacity: EN 1993-1-2 values (about 440 J/kg K at 20 C, 760 at 600 C).
  2. Available heat: the DOE chart points (63 % at 600 C, 61 % at 650 C, 55.6 % at 760 C).
  3. Program: above 315 C the load heats at most at the AWS rate (220 C/h for 25 mm), holds inside
     595 to 650 C for at least 1 h per 25 mm with a zone spread under 85 K (AWS), and cools at
     most at 260 C/h above 315 C.
  4. Energy balance: gas heat = flue (with the cooling air) + lining + load + chamber store.
  5. Faults: a flame failure in one zone makes that zone lag and lengthens the cycle; a low gas
     supply (F10) locks the burners out and stops the heating; a leaking door seal costs gas; a
     zone thermocouple that reads 20 K high leaves a cold spot at its load on the hold (smaller
     than 20 K: the neighbouring zones of the same chamber heat it).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                    # noqa: E402
from model.furnace import Furnace, available_heat, cp_steel        # noqa: E402
from model.gas import GasSupply                                    # noqa: E402

DT = 5.0


def make():
    g = GasSupply("gas", C.GAS_SUPPLY)
    f = Furnace("furnace-1", C.CAR_BOTTOM_SR, g)
    return g, f


def cycle(g, f, max_h=30.0, hook=None):
    f.start()
    log = []
    while f.state != f.DONE and f.t < max_h * 3600:
        if hook:
            hook(g, f)
        g.step(DT)
        f.step(DT)
        assert f.check() == []
        log.append((f.t, f.state, list(f.t_f), list(f.t_l)))
    return log


def test_steel_cp_and_available_heat():
    assert cp_steel(20.0) == pytest.approx(439.8, abs=1.0)
    assert cp_steel(600.0) == pytest.approx(760.2, abs=1.0)
    assert available_heat(600.0) == pytest.approx(0.63)
    assert available_heat(760.0) == pytest.approx(0.556)


def rates(log, lo=315.0):
    """Max heating and cooling rate of the mean load above lo, C/h, over 30 min windows."""
    n = int(1800 / DT)
    up = down = 0.0
    for a, b in zip(log, log[n:]):
        la, lb = sum(a[3]) / 4, sum(b[3]) / 4
        if min(la, lb) >= lo:
            r = (lb - la) / ((b[0] - a[0]) / 3600.0)
            up, down = max(up, r), max(down, -r)
    return up, down


def test_program_follows_aws_d11():
    g, f = make()
    log = cycle(g, f)
    assert f.state == f.DONE
    up, down = rates(log)
    assert up <= f.prog.heat_rate_c_h() * 1.02
    assert down <= f.prog.cool_rate_c_h() * 1.02
    hold = [r for r in log if r[1] == "hold"]
    assert len(hold) * DT >= f.prog.hold_s() - DT
    for _, _, tf, tl in hold[int(1800 / DT):]:
        assert 595.0 <= min(tl) and max(tl) <= 650.0
        assert max(tf) - min(tf) < 85.0


def test_energy_balance():
    g, f = make()
    h0 = f.stored_j()
    cycle(g, f)
    assert f.e_flue + f.e_wall + f.e_load + (f.stored_j() - h0) == pytest.approx(f.e_in, rel=1e-6)


def test_flame_failure_in_one_zone():
    g, f = make()
    base = cycle(g, f)
    g2, f2 = make()
    f2.flame_fail[0] = True
    log = cycle(g2, f2, max_h=40.0)
    mid = [r for r in log if r[1] == "heat" and 4 * 3600 < r[0] < 5 * 3600]
    assert all(r[2][0] < r[2][2] - 5.0 for r in mid)
    assert log[-1][0] > base[-1][0]


def test_low_gas_supply_locks_out_burners():
    g, f = make()
    def hook(g, f):
        if 3 * 3600 <= f.t < 3 * 3600 + DT:
            g.p_supply = 40.0                     # the supply falls under set + minimum differential
    log = cycle(g, f, max_h=6.0, hook=hook)
    assert all(b.locked_out for b in f.burners)
    t3 = [r for r in log if r[0] >= 3 * 3600 + 600]
    assert max(sum(r[3]) for r in t3) <= sum(t3[0][3]) + 4 * 5.0


def test_door_leak_costs_gas():
    g, f = make()
    cycle(g, f)
    g2, f2 = make()
    f2.door_leak = 3.0
    cycle(g2, f2)
    assert f2.gas_m3 > f.gas_m3 * 1.02


def test_thermocouple_drift_leaves_its_load_cold():
    g, f = make()
    f.tc_bias[1] = 20.0
    log = cycle(g, f)
    hold = [r for r in log if r[1] == "hold"][-1]
    assert hold[2][1] < hold[2][0] - 3.0            # the chamber in zone 2 runs cold
    assert hold[3][1] < min(hold[3][0], hold[3][2]) - 3.0
    assert hold[3][0] - hold[3][1] < 20.0
