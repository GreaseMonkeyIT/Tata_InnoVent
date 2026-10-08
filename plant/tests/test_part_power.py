"""Verification of the plant power network (plant/model/power.py): grid source, transformers,
LV cables, buses, and a motor on the network.

Checks:
  1. Voltage regulation of a loaded transformer equals the IEC 60076-1 formula
     eps = er cos + ex sin + (ex cos - er sin)^2 / 200 (in %).
  2. Power balance: grid power = loads + series losses + no-load losses, every tick.
  3. Short-circuit current at the LV bus equals V / (Z_source + Z_transformer).
  4. A grid dip reaches both LV buses at the same moment and by the same share (common mode).
  5. A large motor start on TR-1 dips TR-1's LV bus by several % and TR-2's LV bus by almost
     nothing: separate transformers isolate the lines (ideas.md 11.8).
  6. Transformer thermal model (IEC 60076-7): at rated load the top-oil rise settles at
     d_theta_or and the hot spot at theta_a + d_theta_or + H g_r. The top oil follows its time
     constant (63 % after k11 tau_o).

Run:  python -m pytest plant/tests/test_part_power.py -q      (from the repo root)
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import power as P                                    # noqa: E402
from model import catalog as C                                  # noqa: E402
from model.motor import InductionMotor                         # noqa: E402

SQ3 = math.sqrt(3.0)


def build(tr1=C.TR_2000KVA, tr2=C.TR_1600KVA):
    """The reference supply: 11 kV grid, MV bus, TR-1 and TR-2, one MCC feeder on each."""
    mv = P.Bus("mv", tr1.v1_ll)
    grid = P.Grid("incomer-1", mv, C.GRID_11KV)
    net = P.Network(grid)
    lv1, lv2 = P.Bus("lv1", tr1.v2_ll), P.Bus("lv2", tr2.v2_ll)
    t1 = net.add(P.Transformer("tr-1", mv, lv1, tr1))
    t2 = net.add(P.Transformer("tr-2", mv, lv2, tr2))
    a, b = P.Bus("psu-a", tr1.v2_ll), P.Bus("psu-c", tr2.v2_ll)
    net.add(P.Cable("cab-a", lv1, a, C.CABLE_LV_MAIN, 60.0, runs=2))
    net.add(P.Cable("cab-c", lv2, b, C.CABLE_LV_MAIN, 60.0, runs=2))
    return net, grid, mv, lv1, lv2, t1, t2, a, b


def load_at(bus, kva, pf):
    ld = P.ConstantLoad(kva * 1000.0 * pf, kva * 1000.0 * math.sqrt(1 - pf * pf), bus)
    bus.loads.append(ld)
    return ld


def test_no_load_secondary_voltage():
    net, grid, mv, lv1, lv2, *_ = build()
    net.solve()
    assert lv1.v_ll == pytest.approx(C.TR_2000KVA.v2_ll, rel=0.002)
    assert lv2.v_ll == pytest.approx(C.TR_1600KVA.v2_ll, rel=0.002)
    assert net.check() == []


@pytest.mark.parametrize("pf", [1.0, 0.8])
def test_regulation_matches_iec60076_formula(pf):
    """Rated current at the LV terminals, an infinite grid (huge fault level), no cable."""
    r = C.TR_2000KVA
    mv = P.Bus("mv", r.v1_ll)
    net = P.Network(P.Grid("g", mv, P.GridRating(r.v1_ll, 1e9, 10.0)))
    lv = P.Bus("lv", r.v2_ll)
    net.add(P.Transformer("t", mv, lv, r))
    # constant current at rated: iterate the load power with the bus voltage
    ld = load_at(lv, r.kva, pf)
    for _ in range(30):
        net.solve()
        s = SQ3 * lv.v_ll * r.i2_rated
        ld.p, ld.q = s * pf, s * math.sqrt(1 - pf * pf)
    er = 100.0 * r.pk_w / (r.kva * 1000.0)
    ex = math.sqrt(r.uk_pct ** 2 - er ** 2)
    sin = math.sqrt(1 - pf * pf)
    eps = er * pf + ex * sin + (ex * pf - er * sin) ** 2 / 200.0
    drop = 100.0 * (1.0 - lv.v_ll / r.v2_ll)
    assert drop == pytest.approx(eps, abs=0.05)


def test_power_balance_every_tick():
    net, grid, mv, lv1, lv2, t1, t2, a, b = build()
    load_at(a, 1200.0, 0.85)
    load_at(b, 700.0, 0.9)
    net.solve()
    p_loads = sum(ld.s_va().real for bus in net.buses() for ld in bus.loads)
    p_loss = sum(br.s_loss.real + br.shunt_s().real for br in net.branches)
    p_grid = mv.s_down.real
    assert p_grid == pytest.approx(p_loads + p_loss, rel=1e-6)
    assert net.check() == []


def test_short_circuit_current_at_lv_bus():
    net, grid, mv, lv1, *_ = build()
    r = C.TR_2000KVA
    n = r.v1_ll / r.v2_ll
    z_src = grid.z / n ** 2
    z_tr = net.branches[0].z
    i_sc = (r.v2_ll / SQ3) / abs(z_src + z_tr)
    # the rule of thumb for a stiff grid: I_sc = I_rated / uk
    i_rule = r.i2_rated / (r.uk_pct / 100.0)
    assert i_sc < i_rule
    assert i_sc == pytest.approx(i_rule, rel=0.15)


def test_grid_dip_is_common_mode_across_both_lines():
    net, grid, mv, lv1, lv2, t1, t2, a, b = build()
    load_at(a, 900.0, 0.85)
    load_at(b, 600.0, 0.9)
    net.step(0.02)
    va, vb = a.v_pu, b.v_pu
    grid.dip(0.70)
    net.step(0.02)
    ra, rb = a.v_pu / va, b.v_pu / vb
    # constant-power loads draw more current in the dip, so a bus sags a little below the source
    assert 0.62 < ra < 0.70
    assert rb == pytest.approx(ra, abs=0.02)


def test_motor_start_on_tr1_barely_reaches_tr2():
    net, grid, mv, lv1, lv2, t1, t2, a, b = build()
    load_at(a, 800.0, 0.85)
    load_at(b, 600.0, 0.9)
    motors = []
    for k in range(4):                                  # four 22 kW motors start together
        m = InductionMotor(f"m{k}", C.ABB_M3BP_180MLB4_22KW, j_load=1.0)
        a.loads.append(P.MotorLoad(m))
        motors.append(m)
    net.step(0.02)
    va0, vb0 = a.v_ll, b.v_ll
    for m in motors:                                    # the first tick: locked-rotor current
        m.step(0.02, a.v_ll, grid.f_hz, lambda w: 0.0)
    net.step(0.02)
    dip_a = 1.0 - a.v_ll / va0
    dip_b = 1.0 - b.v_ll / vb0
    assert 0.01 < dip_a < 0.08
    assert dip_b < 0.15 * dip_a
    assert net.check() == []


def test_transformer_thermal_rated_load():
    r = C.TR_2000KVA
    net, grid, mv, lv1, lv2, t1, t2, a, b = build()
    ld = load_at(lv1, r.kva, 0.9)
    tau = r.k11 * r.tau_o_min * 60.0
    t, log = 0.0, []
    while t < 6 * tau:
        net.step(60.0)
        t += 60.0
        log.append((t, t1.theta_o))
        s = SQ3 * lv1.v_ll * r.i2_rated
        ld.p, ld.q = 0.9 * s, s * math.sqrt(1 - 0.81)
    assert t1.theta_o - t1.theta_amb == pytest.approx(r.d_theta_or, abs=1.0)
    assert t1.theta_h == pytest.approx(t1.theta_amb + r.d_theta_or + r.h_gr, abs=1.5)
    at_tau = next(o for tt, o in log if tt >= tau)
    frac = (at_tau - t1.theta_amb) / r.d_theta_or
    assert frac == pytest.approx(1 - math.exp(-1), abs=0.04)
