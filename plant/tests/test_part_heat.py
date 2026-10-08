"""Verification of the shared thermal parts (plant/model/heat.py).

  1. The counterflow effectiveness formula meets its textbook limits: Cr = 0 gives 1 - exp(-NTU),
     Cr = 1 gives NTU / (1 + NTU), and a large NTU gives eps -> 1 (Incropera, table 11.3).
  2. The exchanger reproduces its rated duty, and the heat that leaves the hot stream enters the
     cold stream (energy balance).
  3. Less water flow: less duty and a hotter water outlet. Fouling: less duty. A warmer water inlet:
     a hotter hot outlet. No exchanger moves heat from cold to hot (second law).
  4. The thermal mass step is exact for a linear heat flow.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import heat as H                                     # noqa: E402


def test_effectiveness_limits():
    for ntu in (0.1, 0.5, 1.0, 3.0):
        assert H.eps_counterflow(ntu, 0.0) == pytest.approx(1 - math.exp(-ntu), rel=1e-9)
        assert H.eps_counterflow(ntu, 1.0) == pytest.approx(ntu / (1 + ntu), rel=1e-9)
    assert H.eps_counterflow(40.0, 0.5) == pytest.approx(1.0, abs=1e-6)


def oil_cooler():
    # oil 1.2 kg/s from 85 C to 60 C, water 0.8 kg/s in at 28 C
    return H.HeatExchanger("oc", 1.2, H.CP_OIL, 85.0, 60.0, 0.8, H.CP_WATER, 28.0)


def test_rated_duty_and_energy_balance():
    hx = oil_cooler()
    q = hx.step(85.0, 1.2, 28.0, 0.8)
    assert q == pytest.approx(1.2 * H.CP_OIL * 25.0, rel=1e-6)
    assert hx.t_hot_out == pytest.approx(60.0, abs=1e-6)
    assert 1.2 * H.CP_OIL * (85.0 - hx.t_hot_out) == pytest.approx(0.8 * H.CP_WATER * (hx.t_cold_out - 28.0))


def test_flow_fouling_and_inlet_effects():
    base = oil_cooler()
    q0 = base.step(85.0, 1.2, 28.0, 0.8)
    w0 = base.t_cold_out
    low = oil_cooler()
    q1 = low.step(85.0, 1.2, 28.0, 0.4)
    assert q1 < q0 and low.t_cold_out > w0
    foul = oil_cooler()
    foul.fouling = 0.5
    assert foul.step(85.0, 1.2, 28.0, 0.8) < q0
    warm = oil_cooler()
    warm.step(85.0, 1.2, 34.0, 0.8)
    assert warm.t_hot_out > base.t_hot_out
    cold_side_hotter = oil_cooler()
    assert cold_side_hotter.step(30.0, 1.2, 40.0, 0.8) < 0.0        # heat flows to the oil
    assert cold_side_hotter.t_hot_out > 30.0


def test_thermal_mass_exact_step():
    m = H.ThermalMass("m", 1000.0, 20.0)
    m.step(100.0, q0=500.0, g=10.0, t_ref=20.0)
    t_inf = 20.0 + 50.0
    assert m.t == pytest.approx(t_inf + (20.0 - t_inf) * math.exp(-1.0), rel=1e-12)
