"""Verification of the cooling loop (plant/model/cooling.py) with a simple capacity-limited chiller.

  1. Parallel branches share the flow as 1/sqrt(k): k and 4k carry flow 2:1. The total flow is on
     the pump curve and on the system curve.
  2. Closing one branch valve sends more flow through the others, and the total falls.
  3. Energy: heat in from the users and the pump, minus the heat the chiller removes, equals the
     change of the heat stored in the loop water.
  4. Users above the chiller capacity warm the loop at (excess heat) / (heat capacity of the water).
  5. A worn pump gives less flow.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.cooling import Branch, CoolingLoop                      # noqa: E402
from model.heat import CP_WATER, RHO_WATER                          # noqa: E402
from model.pump import Pump, PumpRating                             # noqa: E402

PUMP = PumpRating(q_m3h=60.0, h_m=25.0, eta=0.72, n_rpm=2900.0, h0_m=32.0, source="test")


class IdealChiller:
    """Cools toward the setpoint up to its capacity."""
    def __init__(self, cap_w, setpoint=28.0):
        self.cap, self.sp = cap_w, setpoint

    def remove(self, t_in, m, dt):
        return max(0.0, min(self.cap, m * CP_WATER * (t_in - self.sp)))


def loop(users_w=(30e3, 20e3), cap=80e3, ks=(2.0e4, 8.0e4)):
    lp = CoolingLoop("cool-1", Pump("p", PUMP), volume_m3=3.0, k_series=1.0e4)
    for i, (q, k) in enumerate(zip(users_w, ks)):
        lp.add(Branch(f"u{i}", k, heat=lambda t, m, q=q: q))
    lp.chiller = IdealChiller(cap)
    return lp


def test_parallel_split_and_operating_point():
    lp = loop()
    lp.step(1.0)
    a, b = lp.branches
    assert a.q_m3s / b.q_m3s == pytest.approx(2.0, rel=1e-9)
    assert a.q_m3s + b.q_m3s == pytest.approx(lp.q_m3s, rel=1e-9)
    h_sys = (lp.k_ser + lp.k_parallel()) * lp.q_m3s ** 2
    assert lp.pump.head(lp.q_m3s, 1.0) == pytest.approx(h_sys, rel=1e-9)


def test_closing_a_valve_redistributes_flow():
    lp = loop()
    lp.step(1.0)
    qa, q_tot = lp.branches[0].q_m3s, lp.q_m3s
    lp.branches[1].valve = 0.0
    lp.step(1.0)
    assert lp.branches[0].q_m3s > qa
    assert lp.q_m3s < q_tot
    assert lp.branches[1].q_m3s == 0.0


def test_energy_balance():
    lp = loop(cap=40e3)                 # users 50 kW against a 40 kW chiller: the loop warms
    h0 = lp.heat_content()
    e_in = e_out = 0.0
    dt = 1.0
    for _ in range(1800):
        lp.step(dt)
        e_in += (lp.q_users + lp.pump.p_shaft) * dt
        e_out += lp.q_chiller * dt
        assert lp.check() == []
    assert e_in - e_out == pytest.approx(lp.heat_content() - h0, rel=0.02)


def test_overload_warms_loop_at_expected_rate():
    lp = loop(cap=40e3)
    for _ in range(600):
        lp.step(1.0)
    t0 = 0.5 * (lp.t_supply + lp.t_return)
    for _ in range(1200):
        lp.step(1.0)
    t1 = 0.5 * (lp.t_supply + lp.t_return)
    excess = 50e3 + lp.pump.p_shaft - 40e3
    rate = excess / (lp.volume * RHO_WATER * CP_WATER)
    assert (t1 - t0) / 1200.0 == pytest.approx(rate, rel=0.05)


def test_pump_wear_lowers_flow():
    a, b = loop(), loop()
    b.pump.wear = 0.3
    a.step(1.0)
    b.step(1.0)
    assert b.q_m3s < a.q_m3s
    # closed loop, no static head: Q = sqrt(H0 (1 - w) / (a (1 - w) + k_sys))
    qn = PUMP.q_m3h / 3600.0
    a_c = (PUMP.h0_m - PUMP.h_m) / qn ** 2
    k_sys = b.k_ser + b.k_parallel()
    q = (PUMP.h0_m * 0.7 / (a_c * 0.7 + k_sys)) ** 0.5
    assert b.q_m3s == pytest.approx(q, rel=1e-9)
