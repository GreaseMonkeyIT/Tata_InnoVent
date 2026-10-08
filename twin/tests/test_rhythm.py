"""Tests of rhythm discovery v0 (twin/rhythm.py).

  1. A clean periodic signal: the period comes back exactly.
  2. Noise alone: no rhythm.
  3. press-1's motor current alone (1 s samples, no state signal): the period equals the press's
     true mean cycle time (from its bend counter), and the cycle starts land at the bends.
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "plant"))

from twin import rhythm                                              # noqa: E402


def test_clean_period():
    t = np.arange(2000)
    x = np.where((t % 37) < 8, 1.0, 0.0)
    assert rhythm.period(x) == pytest.approx(37.0, abs=0.3)


def test_noise_has_no_rhythm():
    rng = np.random.default_rng(0)
    assert rhythm.period(rng.normal(size=3000)) is None


def test_press_current_reveals_the_bend_cycle():
    from model import catalog as C
    from model.press_brake import PressBrake
    p = PressBrake("press-1", C.PPEB_320_40, C.ABB_37KW)
    p.motor.w = p.w_n
    dt, cur = 0.05, []
    for k in range(int(3600 / dt)):
        p.step(dt, 400.0, 50.0, 27.0, C.PPEB_320_40.water_kgs)
        if k % 20 == 0:
            cur.append(p.motor.amps)
    x = np.array(cur[60:])                       # drop the first minute
    per = rhythm.period(x)
    true = 3540.0 / max(p.bends - 1, 1)          # mean cycle over the hour, s
    assert per == pytest.approx(true, rel=0.05)
    ph, _ = rhythm.phase(x, per)
    starts = rhythm.cycle_starts(x, per)
    assert len(starts) == pytest.approx(p.bends - 1, abs=2)
    assert np.nanmax(ph) < 1.0 and np.nanmin(ph) >= 0.0
