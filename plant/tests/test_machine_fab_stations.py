"""Verification of the fabrication line stations (plant/model/fab_stations.py).

  1. Plasma: input while cutting = 300 A x 210 V / 0.9175 + the fan; a low air supply stops it;
     electrode wear raises the arc voltage over the pierces.
  2. Welding: the arc follows IEC 60974-1 (U2 = 14 + 0.05 I2); a worn tip widens the current spread.
  3. Shot blast: an abrasive jam on one turbine drops its power to its idle share; the filter drop
     builds and the pulse cleaning recovers it; clogged cartridges keep it high and cut the air.
  4. Paint: the oven reaches 80 C on its gas burner; loaded filters cut the booth air.
  5. Scanner: a dip shorter than the 16 ms hold-up rides through (SEMI F47: 0 % for 1 cycle is
     only recommended); a longer deep dip resets it and it reboots.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                    # noqa: E402
from model import fab_stations as F                               # noqa: E402
from model.gas import GasSupply                                    # noqa: E402


def test_plasma_power_air_and_wear():
    p = F.PlasmaCutter("plasma-1", C.XPR300, load_s=5.0)
    cut = []
    for _ in range(int(1800 / 0.5)):
        p.step(0.5)
        if p.state == "cut":
            cut.append((p.p_el, p.u_arc))
    first, last = cut[0], cut[-1]
    assert first[0] == pytest.approx(300.0 * (210.0 + 0.01) / 0.9175 + 2200.0, rel=1e-3)
    assert last[1] > first[1]
    q = F.PlasmaCutter("plasma-1", C.XPR300, load_s=5.0)
    q.air_bar = 6.0
    for _ in range(100):
        q.step(0.5)
    assert q.state == "gas_fault"


def test_welding_load_line_and_tip_wear():
    def spread(wear):
        w = F.WeldingCell("weld-1", C.TPS500I, change_s=1.0)
        w.tip_wear = wear
        cur = []
        for _ in range(int(600 / 0.1)):
            w.step(0.1)
            if w.state == "weld":
                cur.append(w.i)
                assert w.u == pytest.approx(14.0 + 0.05 * w.i)
        m = sum(cur) / len(cur)
        return (sum((c - m) ** 2 for c in cur) / len(cur)) ** 0.5 / m
    assert spread(1.0) > 3.0 * spread(0.0)


def test_shot_blast_jam_filter_and_clog():
    s = F.ShotBlast("blast-1", C.RRB_16_5)
    s.step(1.0)
    p0 = s.p_el
    s.feed[0] = 0.0
    s.step(1.0)
    assert p0 - s.p_el == pytest.approx(11e3 * (1 - 0.35), rel=0.05)
    dps = []
    for _ in range(4 * 3600):
        s.step(1.0)
        dps.append(s.dp_filter)
    assert max(dps) <= C.RRB_16_5.dp_clean_trigger_pa + 1.0 and min(dps[100:]) < 900.0
    c = F.ShotBlast("blast-2", C.RRB_16_5)
    c.clog = 0.4
    for _ in range(4 * 3600):
        c.step(1.0)
    assert c.flow_m3h < s.flow_m3h or c.dp_filter > 1000.0


def test_paint_oven_and_filters():
    g = GasSupply("gas", C.GAS_SUPPLY)
    b = F.PaintBooth("paint-1", C.HEAVY_BOOTH, g)
    for _ in range(int(3 * 3600 / 5.0)):
        g.step(5.0)
        b.step(5.0)
    assert b.t_oven == pytest.approx(80.0, abs=3.0)
    share_clean = b.flow_share
    b.dp = 240.0
    b.step(1.0)
    assert b.flow_share < share_clean


def test_scanner_holdup_and_reset():
    s = F.QaScanner("qa-1", C.SCANNER)
    s.step(0.01, 0.0)                      # a 10 ms interruption: inside the 16 ms hold-up
    s.step(0.01, 1.0)
    assert s.state == "run" and s.resets == 0
    for _ in range(5):                     # a 50 ms interruption: it resets
        s.step(0.01, 0.0)
    assert s.state == "reboot" and s.resets == 1
    for _ in range(int(70 / 0.5)):
        s.step(0.5, 1.0)
    assert s.state == "run"
