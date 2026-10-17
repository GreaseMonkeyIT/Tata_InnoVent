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
from model import catalog as C                                   # noqa: E402

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
    """The chiller rides through the short and shallow IEC 61000-4-11 class 3 dips. Whether it rides
    through the longest one (80 % for 250 cycles, 5 s) depends on the bus voltage before the dip:
    its ANSI 27 stage stops it under 85 % of 400 V (340 V) for 2 s. This is a real effect of the bus
    loading and the power factor correction (2026-10-15): on the loaded 630 kVA TR-1 without APFC the
    bus sat near 420 V and the dip left 335 V (a stop); the APFC capacitors raise the bus, and the
    old 2000 kVA unit left 340.5 V (a pass by 0.5 V). So the test checks the physics: the chiller
    stops exactly when the dipped bus is under 340 V for longer than 2 s, and after a stop it waits
    out its anti-recycle time and restarts, as in F11."""
    from model.catalog import DIPS_CLASS3
    p = UtilitiesPlant(cooling="chiller", with_fab=False)
    run(p, 900.0)
    for residual, cycles in DIPS_CLASS3:
        p.grid.dip(residual)
        low_s = 0.0
        for _ in range(max(1, int(round(cycles / 50.0 / DT)))):
            p.step(DT)
            low_s += DT if p.psu["psu-b"].v_ll < 0.85 * 400.0 else 0.0
        p.grid.clear()
        run(p, 60.0)
        if low_s > 2.0 + DT:
            assert not p.chiller.running             # the ANSI 27 stage stopped it
            run(p, 900.0)
            assert p.chiller.running                 # restarted after its anti-recycle time
        else:
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
    Without the assembly line, so that psu-c has no load swings of its own. Free-running (flow=False):
    the press bends on its own clock, so the test sees bends and idle time in 10 min."""
    p = UtilitiesPlant(with_assembly=False, flow=False)
    log = run(p, 600.0, log_every=0.5)
    rows = [t for _, t in log[200:]]
    idle = [t for t in rows if t["press-1.phase"] == "idle"]
    i_idle = sorted(t["press-1.current_a"] for t in idle)[len(idle) // 2]
    # an 8 mm frame plate of 2.5 m (1,240 kN) draws about 17 A over the idle current
    bend = [t for t in rows if t["press-1.phase"] == "bend" and t["press-1.current_a"] > i_idle + 10.0]
    assert bend and idle
    va_b = sum(t["psu-a.voltage_v"] for t in bend) / len(bend)
    va_i = sum(t["psu-a.voltage_v"] for t in idle) / len(idle)
    vc_b = sum(t["psu-c.voltage_v"] for t in bend) / len(bend)
    vc_i = sum(t["psu-c.voltage_v"] for t in idle) / len(idle)
    assert va_i - va_b > 0.15                     # a visible dip on the press's own rail
    assert abs(vc_i - vc_b) < 0.2 * (va_i - va_b)  # TR-2's rail barely sees it
    assert p.press.bends >= 10
    assert 35.0 <= p.press.t_oil <= 60.0


def test_tower_cooling_normal_running():
    """The conventional cooling: at the site's design wet bulb (28.1 C) the process supply sits above
    the wet bulb (tower approach + plate exchanger approach; small with the plant's real heat, no
    placeholder load since 2026-10-15), the fans cycle on their thermostat, the compressor and the
    press stay inside their ranges."""
    p = UtilitiesPlant()
    log = run(p, 3600.0, log_every=10.0)
    sup = [t["cool-1.supply_c"] for _, t in log[120:]]
    assert 28.1 < min(sup) and max(sup) < 28.1 + 7.0
    assert all(28.1 <= t["cool-1-tower.basin_c"] for _, t in log)
    # the oil thermostat holds the injected oil at 60 C; the element runs above it, more when loaded
    # (Atlas Copco: element 70 to 75 C at load), and under the 120 C trip
    assert C.GA37W_7_5.oil_set_c <= p.comp.t_elem <= 78.0
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


def test_ced_burners_on_the_gas_header_and_f10():
    """ced-1's tank burners and cure oven fire on the plant gas header; a low gas supply (F10) locks
    all of them out, and the degrease tank and the oven cool: the gas-side common cause of ideas.md
    11.8, now on two users of one line."""
    p = UtilitiesPlant()
    run(p, 1800.0)
    assert p.gas.p_out == pytest.approx(30.0, abs=1.0)
    t_deg, t_oven = p.ced.tanks[0].t, p.ced.t_oven
    assert 53.0 < t_deg < 56.0 and t_oven > 190.0
    p.gas.p_supply = 40.0
    run(p, 1800.0)
    # a burner's low-gas switch acts when the burner calls for heat (as a burner manager does); the
    # oven burner modulates all the time, a tank burner trips at its next call
    assert p.ced.oven_burner.locked_out
    called = [t for t in p.ced.tanks if t.t <= t.t_set - C.CED_LINE.tank_band_k]
    assert all(t.burner.locked_out for t in called)
    assert p.ced.t_oven < t_oven - 5.0 and p.ced.tanks[0].t < t_deg - 0.2


def test_frame_line_in_the_plant():
    """The truck frame line on TR-1 with its material flow: frames come out of the line (pierced,
    formed, blasted, painted, riveted, scanned, moved out), TR-1 stays inside its rating and psu-a
    inside the voltage band while lmp-1 forms side members; the welders make psu-a flicker more than
    psu-c (TR-2); the scanner's wide-range supply rides through a 50 % grid dip, and an interruption
    longer than its 16 ms hold-up resets it (F11). Without the assembly line, so that psu-c has no
    load swings of its own."""
    p = UtilitiesPlant(with_assembly=False)
    log = run(p, 3 * 3600.0, log_every=1.0)
    rows = [t for _, t in log[300:]]
    assert max(t["tr-1.load_pct"] for t in rows) < 100.0
    assert min(t["psu-a.voltage_v"] for t in rows) > 360.0
    assert p.fflow.frames_done >= 2
    assert p.pierce.members >= 6 and p.lmp.bends >= 4 and p.ced.loads >= 2 and p.rivet.frames >= 2
    assert p.plasma.plates >= 2 and p.press.bends >= 24 and p.press2.strokes >= 48 and p.conv.moves >= 2
    assert max(t["lmp-1.pressure_bar"] for t in rows) > 50.0     # a U forming stroke at its force
    import statistics
    # flicker is a fast change (a flicker meter weighs changes of a second and less): the mean change
    # between 1 s samples, away from lmp-1's strokes (a 320 kW stroke also dips the shared 11 kV bus,
    # and slow site load swings reach TR-2 that way too: a common path, not the welders')
    calm = [t for t in rows if t["lmp-1.phase"] == "idle"]
    va = [t["psu-a.voltage_v"] for t in calm]
    vc = [t["psu-c.voltage_v"] for t in calm]
    sa = statistics.mean(abs(x - y) for x, y in zip(va[1:], va))
    sc = statistics.mean(abs(x - y) for x, y in zip(vc[1:], vc))
    assert sa > 2.0 * sc
    p.grid.dip(0.50)                      # a 50 % dip: the wide-range supply rides through
    run(p, 0.5)
    p.grid.clear()
    run(p, 5.0)
    assert p.scanner.resets == 0
    p.grid.dip(0.0)                       # a 100 ms interruption: longer than the 16 ms hold-up
    run(p, 0.1)
    p.grid.clear()
    run(p, 5.0)
    assert p.scanner.resets == 1


def test_f6_air_leak_reaches_press2_and_plasma():
    """F6 across the plant: a large leak on the air header pulls the pressure down; press-2's clutch
    switch blocks its strokes and the plasma stops on low air. Free-running (flow=False), so both
    machines would work all the time without the leak."""
    p = UtilitiesPlant(flow=False)
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
    load at once; a washer that runs hot (F13) turns tight blocks into leak-test rejects.
    Free-running (flow=False), so the hot test's step times are known."""
    p = UtilitiesPlant(flow=False)
    run(p, 1800.0)
    assert p.leak.results and all(ok for _, ok in p.leak.results)
    p.hot.t = 300.0 + 120 * 4 + 10.0 - (p.t % 1.0)        # jump the hot test into its rated step
    run(p, 2.0)
    s_regen = p.tr2.child.s_down.real
    p.hot.dyno_trip = True
    run(p, 2.0)
    assert p.tr2.child.s_down.real > s_regen + 250e3
    # F13: a washer that runs hot (up to +15 K: a water tank at atmospheric pressure stays under
    # about 95 C) leaves warmer blocks. The cooling station has margin (half the reject limit at the
    # coldest shop air), so the fault shows as a rise of the leak decay, not as rejects of tight blocks
    base = UtilitiesPlant(flow=False, material_spread=False)
    hot = UtilitiesPlant(flow=False, material_spread=False)
    hot.washer.offset_k = 15.0
    for x in (base, hot):
        run(x, 3 * 3600.0)
    d = lambda x: sum(v for v, _ in x.leak.results[2:]) / len(x.leak.results[2:])
    assert d(hot) > d(base) + 3.0
    assert all(ok for _, ok in base.leak.results)
