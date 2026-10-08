"""Verification of the centrifugal pump part (plant/model/pump.py).

  1. The rated point and the shut-off head are on the curve.
  2. Affinity laws on a pure friction system (no static head): Q ~ n, H ~ n^2, P ~ n^3.
  3. The operating point is on both the pump curve and the system curve.
  4. A worn impeller gives less flow and less head on the same system; a throttled system gives
     less flow and more head; a pump never gives flow above its shut-off head.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.pump import Pump, PumpRating                         # noqa: E402

R = PumpRating(q_m3h=60.0, h_m=32.0, eta=0.75, n_rpm=2900.0, h0_m=40.0, source="test")
QN = R.q_m3h / 3600.0


def test_curve_points():
    p = Pump("p", R)
    assert p.head(0.0, 1.0) == pytest.approx(R.h0_m)
    assert p.head(QN, 1.0) == pytest.approx(R.h_m)
    assert p.efficiency(QN, 1.0) == pytest.approx(R.eta)


def test_affinity_laws_on_friction_system():
    k = R.h_m / QN ** 2                      # the system passes through the rated point
    p = Pump("p", R)
    p.operate(1.0, 0.0, k)
    q1, h1, w1 = p.q, p.h, p.p_shaft
    assert q1 == pytest.approx(QN, rel=1e-9)
    assert w1 == pytest.approx(R.p_shaft_w, rel=1e-9)
    p.operate(0.7, 0.0, k)
    assert p.q == pytest.approx(0.7 * q1, rel=1e-9)
    assert p.h == pytest.approx(0.49 * h1, rel=1e-9)
    assert p.p_shaft == pytest.approx(0.343 * w1, rel=1e-6)


def test_operating_point_on_both_curves():
    p = Pump("p", R)
    q = p.operate(1.0, 10.0, 6.0e4)
    assert p.head(q, 1.0) == pytest.approx(10.0 + 6.0e4 * q ** 2, rel=1e-9)


def test_wear_throttle_and_static_head():
    k = R.h_m / QN ** 2
    base = Pump("p", R)
    base.operate(1.0, 5.0, k)
    worn = Pump("p", R)
    worn.wear = 0.2
    worn.operate(1.0, 5.0, k)
    assert worn.q < base.q and worn.h < base.h
    thr = Pump("p", R)
    thr.operate(1.0, 5.0, 2.0 * k)
    assert thr.q < base.q and thr.h > base.h
    high = Pump("p", R)
    assert high.operate(1.0, R.h0_m + 1.0, k) == 0.0
    assert high.p_shaft > 0.0                 # it still takes power against a closed system
