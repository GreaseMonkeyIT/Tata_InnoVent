"""Verification of the wired utilities plant (plant/model/plant.py): the supply, compressor-1,
chiller-1 and cool-1 together, with a placeholder for the machines' heat.

  1. Normal running for 2 h: the MCC voltages inside 400 V +6/-10 % (IEC 60038), the loop supply
     at 27 C, the compressor inside its band, the chiller COP plausible, no physics violation,
     and the sim much faster than real time.
  2. Power balance at the incomer: grid power = the loads on psu-b + the network losses.
  3. F7 across the plant: a failed-low transducer keeps compressor-1 loaded at 8.5 bar; it draws
     more power and puts more heat into cool-1 (the chain of Scenario 2, now through the loop).
  4. The chiller rides through every IEC 61000-4-11 class 3 test dip (its ANSI 27 stages need
     85 % for 2 s or 90 % for 10 s of its 400 V rating; the MCC runs at 433 V).
  5. F11: a deeper, longer sag (70 % for 4 s) trips the chiller's undervoltage protection; it
     waits its anti-recycle time, the loop warms meanwhile, and it recovers after the restart.
  5. The pump at its design point and the compressor at its rated water flow.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model.compressor import P_ATM                               # noqa: E402
from model.plant import UtilitiesPlant                           # noqa: E402

DT = 0.1


def run(p, secs, log_every=None):
    log = []
    for k in range(int(secs / DT)):
        p.step(DT)
        assert p.check() == []
        if log_every and k % int(log_every / DT) == 0:
            log.append((p.t, dict(p.tags())))
    return log


def test_normal_running_two_hours():
    p = UtilitiesPlant(cooling="chiller", machine_heat_w=120e3, with_assembly=False)
    t0 = time.time()
    log = run(p, 7200.0, log_every=10.0)
    wall = time.time() - t0
    assert 7200.0 / wall > 100.0
    for _, t in log[60:]:
        for bus in ("psu-a", "psu-b", "psu-c"):
            assert 360.0 <= t[f"{bus}.voltage_v"] <= 424.0 + 9.0      # 400 V -10 % .. 433 V no-load
        assert t["cool-1.supply_c"] == pytest.approx(27.0, abs=0.5)
        assert 6.7 <= t["compressor-1.pressure_barg"] <= 7.7
    cop = p.chiller.q_w / p.chiller.p_in
    assert 1.8 < cop < 4.0


def test_power_balance_at_incomer():
    p = UtilitiesPlant()
    run(p, 600.0)
    net = p.net
    net.solve()                       # the machines changed their loads after the tick's solve
    loads = sum(ld.s_va().real for b in net.buses() for ld in b.loads)
    losses = sum(br.s_loss.real + br.shunt_s().real for br in net.branches)
    assert p.grid.bus.s_down.real == pytest.approx(loads + losses, rel=1e-6)


def test_design_flows():
    p = UtilitiesPlant()
    run(p, 300.0)
    assert p.loop.q_m3s * 3600.0 == pytest.approx(60.0, rel=0.03)
    assert p.b_comp.q_m3s * 997.0 == pytest.approx(0.9, rel=0.05)


def test_f7_transducer_heats_the_loop_through_the_compressor():
    a, b = UtilitiesPlant(), UtilitiesPlant()
    run(a, 1800.0)
    run(b, 1800.0)
    b.comp.pt_fault_bar = 5.5
    run(a, 1800.0)
    run(b, 1800.0)
    assert b.comp.state == b.comp.LOADED
    assert b.comp.p_rec - P_ATM == pytest.approx(8.5, abs=0.1)
    assert b.comp.motor.p_in > a.comp.motor.p_in
    assert b.comp.q_water > a.comp.q_water


def test_chiller_rides_through_class3_dips():
    """Utilities only: with the whole fabrication line on psu-b too, the bus sits lower and an 80 %
    dip takes the chiller just under its 85 % stage (a real effect of a loaded bus)."""
    from model.catalog import DIPS_CLASS3
    p = UtilitiesPlant(cooling="chiller", with_fab=False)
    run(p, 900.0)
    for residual, cycles in DIPS_CLASS3:
        p.grid.dip(residual)
        run(p, max(DT, cycles / 50.0))
        p.grid.clear()
        run(p, 60.0)
        assert p.chiller.running and not p.chiller.tripped


def test_f11_grid_sag_stops_chiller_and_loop_warms():
    p = UtilitiesPlant(machine_heat_w=150e3, cooling="chiller", with_assembly=False)
    run(p, 1200.0)
    t_before = p.loop.t_supply
    p.grid.dip(0.70)
    run(p, 4.0)
    p.grid.clear()
    assert not p.chiller.running
    log = run(p, 900.0, log_every=10.0)
    peak = max(t["cool-1.supply_c"] for _, t in log)
    assert peak > t_before + 1.0
    run(p, 1800.0)
    assert p.chiller.running
    assert p.loop.t_supply == pytest.approx(27.0, abs=0.5)


def test_press_on_psu_a_dips_its_rail_not_tr2():
    """press-1 on psu-a: its bend current dips psu-a; psu-c on TR-2 barely moves (ideas.md 11.8).
    Without the assembly line, so that psu-c has no load swings of its own."""
    p = UtilitiesPlant(with_assembly=False)
    log = run(p, 600.0, log_every=0.5)
    rows = [t for _, t in log[200:]]
    bend = [t for t in rows if t["press-1.phase"] == "bend" and t["press-1.current_a"] > 50.0]
    idle = [t for t in rows if t["press-1.phase"] == "idle"]
    assert bend and idle
    va_b = sum(t["psu-a.voltage_v"] for t in bend) / len(bend)
    va_i = sum(t["psu-a.voltage_v"] for t in idle) / len(idle)
    vc_b = sum(t["psu-c.voltage_v"] for t in bend) / len(bend)
    vc_i = sum(t["psu-c.voltage_v"] for t in idle) / len(idle)
    assert va_i - va_b > 0.3                      # a visible dip on the press's own rail
    assert abs(vc_i - vc_b) < 0.2 * (va_i - va_b)  # TR-2's rail barely sees it
    assert p.press.bends >= 10
    assert 35.0 <= p.press.t_oil <= 60.0


def test_tower_cooling_normal_running():
    """The conventional cooling: at the site's design wet bulb (28.1 C) the process supply sits a few
    kelvin above the wet bulb (tower approach + plate exchanger approach), the fans cycle on their
    thermostat, the compressor and the press stay inside their ranges."""
    p = UtilitiesPlant()
    log = run(p, 3600.0, log_every=10.0)
    sup = [t["cool-1.supply_c"] for _, t in log[120:]]
    assert 28.1 + 1.0 < min(sup) and max(sup) < 28.1 + 7.0
    assert all(28.1 <= t["cool-1-tower.basin_c"] for _, t in log)
    assert 68.0 <= p.comp.t_elem <= 78.0
    assert 35.0 <= p.press.t_oil <= 60.0


def test_tower_follows_the_weather_and_fill_fouling_warms_the_loop():
    """A hotter, more humid day (wet bulb 29.5 C) or fouled fill (F9: 18 % capability lost, the
    Brentwood figure) give warmer process water: the cross-plant path of F9."""
    def supply(**kw):
        p = UtilitiesPlant(machine_heat_w=200e3, **kw)
        run(p, 2400.0)
        return p
    base = supply()
    humid = supply(t_wb_c=29.5)
    fouled = UtilitiesPlant(machine_heat_w=200e3)
    fouled.tower.tower.fouling = 0.18
    run(fouled, 2400.0)
    assert humid.loop.t_supply > base.loop.t_supply + 0.5
    assert fouled.loop.t_supply > base.loop.t_supply + 0.3


def test_furnace_on_the_gas_header_and_f10():
    """furnace-1 heats on the plant gas header; a low gas supply (F10) locks its burners out and its
    zones stop climbing: the gas-side common cause of ideas.md 11.8."""
    p = UtilitiesPlant()
    run(p, 1800.0)
    assert p.furnace.state == p.furnace.HEAT
    assert p.gas.p_out == pytest.approx(30.0, abs=1.0)
    t_before = sum(p.furnace.t_f) / 4
    assert t_before > 60.0
    p.gas.p_supply = 40.0
    run(p, 600.0)
    assert all(b.locked_out for b in p.furnace.burners)
    assert sum(p.furnace.t_f) / 4 < t_before + 10.0


def test_fabrication_line_in_the_plant():
    """The whole fabrication line on TR-1: TR-1 stays inside its rating, psu-a stays inside the
    voltage band while the plasma, the welders and the boring mill work; the welders make psu-a
    flicker more than psu-c (TR-2); the plasma draws plant air; a 50 % grid dip trips the boring mill's
    drive while the scanner's wide-range supply rides through it, and an interruption longer than
    the supply's 16 ms hold-up resets the scanner (F11). Without the assembly line, so that psu-c
    has no load swings of its own."""
    p = UtilitiesPlant(with_assembly=False)
    log = run(p, 3600.0, log_every=1.0)
    rows = [t for _, t in log[300:]]
    assert max(t["tr-1.load_pct"] for t in rows) < 100.0
    assert min(t["psu-a.voltage_v"] for t in rows) > 360.0
    assert p.plasma.plates >= 1 and p.cnc.parts + p.cnc.op_i > 0 and p.conv.moves >= 4
    assert p.press2.strokes >= 100
    import statistics
    sa = statistics.pstdev([t["psu-a.voltage_v"] for t in rows])
    sc = statistics.pstdev([t["psu-c.voltage_v"] for t in rows])
    assert sa > 2.0 * sc
    p.grid.dip(0.50)                      # a 50 % dip: the drive trips, the wide-range supply rides
    run(p, 0.5)
    p.grid.clear()
    run(p, 5.0)
    assert p.cnc.state == p.cnc.TRIPPED
    assert p.scanner.resets == 0
    p.grid.dip(0.0)                       # a 100 ms interruption: longer than the 16 ms hold-up
    run(p, 0.1)
    p.grid.clear()
    run(p, 5.0)
    assert p.scanner.resets == 1


def test_f6_air_leak_reaches_press2_and_plasma():
    """F6 across the plant: a large leak on the air header pulls the pressure down; press-2's clutch
    switch blocks its strokes and the plasma stops on low air."""
    p = UtilitiesPlant()
    run(p, 900.0)
    s0 = p.press2.strokes
    p.comp.leak_m3s_bar = 0.04          # a leak far beyond the compressor's 116 l/s
    run(p, 900.0)
    assert p.comp.p_rec - 1.013 < 4.5
    late = p.press2.strokes
    run(p, 300.0)
    assert p.press2.strokes == late
    assert p.plasma.state in ("gas_fault", "load")


def test_assembly_line_on_tr2_with_f12_and_f13():
    """The engine assembly line on TR-2: the hot test's dyno feeds power back (TR-2 carries less
    while an engine runs at rated), its coolant heat reaches cool-1; a dyno trip (F12) raises TR-2's
    load at once; a washer that runs hot (F13) turns tight blocks into leak-test rejects."""
    p = UtilitiesPlant()
    run(p, 1800.0)
    assert p.leak.results and all(ok for _, ok in p.leak.results)
    p.hot.t = 300.0 + 120 * 4 + 10.0 - (p.t % 1.0)        # jump the hot test into its rated step
    run(p, 2.0)
    s_regen = p.tr2.child.s_down.real
    p.hot.dyno_trip = True
    run(p, 2.0)
    assert p.tr2.child.s_down.real > s_regen + 250e3
    q = UtilitiesPlant()
    q.washer.offset_k = 15.0
    run(q, 3 * 3600.0)
    assert any(not ok for _, ok in q.leak.results)
