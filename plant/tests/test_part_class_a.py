"""The Class A analyzer at incomer-1 (2026-10-22, ideas.md 17.3): the lowest 11 kV voltage of the last
second shows a dip of one cycle between two 1 s polls, which the present value misses, and reading it
changes nothing in the plant."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import faults as FR                   # noqa: E402
from model.plant import UtilitiesPlant          # noqa: E402


def test_dip_between_polls_shows_in_the_lowest_voltage():
    p = UtilitiesPlant(seed=3, t_day0_h=4.0)
    polls = []
    for k in range(400):                          # 40 s at 0.1 s, a poll every 1 s
        if k == 205:
            FR.inject(p, "F11", 0.0)              # a dip to 0 pu, cleared at the next step (one cycle)
        if k == 206:
            FR.clear(p, "F11")
        p.step(0.1)
        if k % 10 == 0:
            polls.append(p.tags())
    v = [t["incomer-1.voltage_v"] for t in polls]
    vmin = [t["incomer-1.v_min_v"] for t in polls]
    assert min(v) > 10_000.0                      # the present value never sees the dip
    assert vmin[21] < 100.0 and min(vmin[:21] + vmin[22:]) > 10_000.0   # the poll after the dip does
    assert all(a <= b + 1e-9 for a, b in zip(vmin, v))
