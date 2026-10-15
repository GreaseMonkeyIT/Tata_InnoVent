"""Unit checks of the machine detector (eval/machines.py): the gate on residuals and the episodes."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from eval.machines import K_DEV, WIN_S, episodes, gate_alarm     # noqa: E402


def test_gate_ignores_a_short_spike_and_catches_a_step():
    s = 1.0
    z = np.zeros(600)
    z[100:130] = 10.0                     # 30 s spike: under 65 % of the 120 s window
    z[300:] = K_DEV + 1.0                 # a lasting step
    a = gate_alarm(z, s)
    assert not a[:299].any()
    first = int(np.flatnonzero(a)[0])
    # the 35th percentile crosses once 65 % of the window is above the band: about 0.65 x 120 s
    assert 300 + 0.6 * WIN_S <= first <= 300 + 0.7 * WIN_S


def test_gate_is_two_sided():
    z = np.zeros(400)
    z[100:] = -(K_DEV + 2.0)              # a residual that falls (a clogged filter: less current)
    assert gate_alarm(z, 1.0)[-1]


def test_episodes_count_each_alarm_once():
    a = np.array([0, 1, 1, 0, 0, 1, 0, 1, 1], bool)
    assert episodes(a, 2.0, 100.0) == [(102.0, 106.0), (110.0, 112.0), (114.0, 118.0)]
