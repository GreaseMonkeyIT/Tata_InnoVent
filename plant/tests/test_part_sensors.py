"""Verification of the measurement part (plant/model/sensors.py).

  1. Every reading of many instruments stays inside its accuracy class (fixed error inside half the
     class, noise 1/10 of the class: the 4-sigma reach stays inside the limit).
  2. Two instruments on the same value disagree a little (their own fixed errors), as real ones do.
  3. Resolution and sample period: values are on the resolution grid and held between samples.
  4. Faults: a drift grows linearly; a stuck sensor freezes.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import sensors as S                                  # noqa: E402


@pytest.mark.parametrize("make,true", [
    (lambda k: S.Sensor(f"rtd{k}", S.rtd_class_b), 72.0),
    (lambda k: S.Sensor(f"ct{k}", S.ct_class_05s(100.0)), 60.0),
    (lambda k: S.Sensor(f"pt{k}", S.transmitter(16.0)), 7.0),
])
def test_readings_inside_class(make, true):
    for k in range(50):
        s = make(k)
        lim = s.limit_fn(true)
        for _ in range(200):
            assert abs(s.read(true, 1.0) - true) <= lim * 1.0001


def test_instruments_disagree_a_little():
    a, b = S.Sensor("a", S.rtd_class_b), S.Sensor("b", S.rtd_class_b)
    ma = sum(a.read(50.0, 1.0) for _ in range(500)) / 500
    mb = sum(b.read(50.0, 1.0) for _ in range(500)) / 500
    assert ma != pytest.approx(mb, abs=1e-6)
    assert abs(ma - mb) <= S.rtd_class_b(50.0)


def test_resolution_and_hold():
    s = S.Sensor("r", S.rtd_class_b, resolution=0.1, period_s=5.0)
    vals = [s.read(40.0 + 0.01 * k, 1.0) for k in range(20)]
    assert all(abs(v * 10 - round(v * 10)) < 1e-9 for v in vals)
    assert vals[0] == vals[1] == vals[2] == vals[3]


def test_drift_and_stuck_faults():
    s = S.Sensor("d", S.transmitter(16.0))
    s.read(7.0, 1.0)
    base = sum(s.read(7.0, 1.0) for _ in range(100)) / 100
    s.drift_per_s = 0.001
    for _ in range(1000):
        s.read(7.0, 1.0)
    assert s.read(7.0, 1.0) - base == pytest.approx(1.0, abs=0.05)
    s.stuck = 5.5
    assert s.read(9.0, 1.0) == 5.5
