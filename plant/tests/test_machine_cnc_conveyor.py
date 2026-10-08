"""Verification of cnc-1 (plant/model/cnc.py) and conveyor-1 (plant/model/conveyor.py).

cnc-1:
  1. Cutting power equals the Sandvik formula with the Kienzle kc at a fresh tool.
  2. Tool wear raises the cutting power by 0.11 % per um of flank wear (Kovalcik 2020), and the
     tool is changed at VB 0.3 mm (ISO 3685): the power drops back.
  3. The roughing pass stays inside the spindle's continuous rating.
  4. A broken tool drops the power to the air-cut level; a sag under 75 % trips the drive (F11).
conveyor-1:
  5. Moving a 5 t weldment takes F = m g mu_r through the gear and motor efficiencies.
  6. A jam (F3) draws locked-rotor current and the class 10 relay trips in seconds.
  7. A seized roller (more drag) raises the power.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                 # noqa: E402
from model.cnc import BoringMill                                # noqa: E402
from model.conveyor import Conveyor                             # noqa: E402

DT = 0.5


def run_cnc(m, secs, v=400.0):
    log = []
    for _ in range(int(secs / DT)):
        m.step(DT, v)
        assert m.check() == []
        log.append((m.state, m.p_cut, m.vb_um, m.p_el))
    return log


def test_cutting_power_formula():
    m = BoringMill("cnc-1", C.DBC_130)
    op = m.program[0]
    m.vb_um = 0.0
    kc = 1700.0 * op.fn ** -0.25
    assert m.cut_power_kw(op) == pytest.approx(op.vc * op.ap * op.fn * kc / 60000.0, rel=1e-9)
    assert m.cut_power_kw(op) < C.DBC_130.p_s1_kw


def test_tool_wear_raises_power_and_tool_change_resets():
    m = BoringMill("cnc-1", C.DBC_130, load_s=10.0)
    op = m.program[0]
    m.vb_um = 244.0
    p244 = m.cut_power_kw(op)
    m.vb_um = 0.0
    assert p244 / m.cut_power_kw(op) == pytest.approx(1.27, abs=0.01)
    mill = BoringMill("cnc-1", C.DBC_130, load_s=10.0)
    log = run_cnc(mill, 4 * 3600.0)
    changes = sum(1 for a, b in zip(log, log[1:]) if a[0] != "tool_change" and b[0] == "tool_change")
    assert changes >= 1
    # the tool changes at the end of the pass that crosses VB 0.3 mm: at most one pass of wear over
    longest = max(mill._op_time(o) for o in mill.program)
    assert max(r[2] for r in log) <= 300.0 + mill.wear_um_per_min * longest / 60.0 + 1e-6


def test_broken_tool_and_undervoltage():
    m = BoringMill("cnc-1", C.DBC_130, load_s=5.0)
    run_cnc(m, 30.0)
    p = m.p_cut
    m.broken = True
    run_cnc(m, 2.0)
    assert m.state != "cut" or m.p_cut < 0.1 * p
    q = BoringMill("cnc-1", C.DBC_130, load_s=5.0)
    run_cnc(q, 30.0)
    run_cnc(q, 1.0, v=0.70 * 400.0)
    assert q.state == q.TRIPPED and q.p_el == 0.0


def run_conv(c, secs, dt=0.1):
    out = []
    for _ in range(int(secs / dt)):
        c.step(dt, 400.0)
        assert c.check() == []
        out.append((c.state, c.p_el, c.amps, c.v))
    return out


def test_conveyor_power_for_a_weldment():
    c = Conveyor("conveyor-1", C.ROLLER_CONVEYOR)
    c.send(5000.0)
    log = run_conv(c, 70.0)
    cruise = [p for s, p, i, v in log if s == "moving" and v >= C.ROLLER_CONVEYOR.v_mps - 1e-9]
    f = 300.0 + 5000.0 * 9.81 * 0.015
    shaft = f * 0.2 / 0.96
    assert cruise[-1] == pytest.approx(shaft / c.eta_motor(shaft / 5500.0), rel=1e-6)
    assert c.moves == 1


def test_jam_trips_overload_and_seized_roller_costs_power():
    c = Conveyor("conveyor-1", C.ROLLER_CONVEYOR)
    c.send(4000.0)
    run_conv(c, 5.0)
    c.jammed = True
    log = run_conv(c, 30.0)
    t_trip = next(i for i, r in enumerate(log) if r[0] == "tripped") * 0.1
    assert 2.0 <= t_trip <= 15.0
    a, b = Conveyor("a", C.ROLLER_CONVEYOR), Conveyor("b", C.ROLLER_CONVEYOR)
    b.mu_extra = 0.03
    a.send(4000.0)
    b.send(4000.0)
    pa, pb = run_conv(a, 20.0)[-1][1], run_conv(b, 20.0)[-1][1]
    assert pb > 1.5 * pa
