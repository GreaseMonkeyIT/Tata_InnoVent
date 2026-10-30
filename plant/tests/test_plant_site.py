"""Verification of the site as one shop (2026-10-15, ideas.md 17.5): the building services, the
transformer sizes, no export, and the realism fixes found by the first full-plant run.

  1. The building services follow their formulas: fan counts from the air change rates, lighting
     from the power densities, office loads in and out of office hours.
  2. The site never exports, also with the hot test at full regeneration (reverse-power check).
  3. The transformers are properly loaded for the shop: the 15 min maximum demand at most 80 % of
     the rating, the mean load inside 30 to 75 % (IS 1180 states its losses at 50 %), and no
     overload even for a second.
  4. The furnace zones differ in a heat-up (end walls, door, heavier ends of the boom) and meet the
     setpoint within the +-8 C of API stress-relief qualification at the hold.
  5. Signals that were exactly constant now move as real ones do: the washer thermostat band, the
     press-2 brake stopping time, the material batches, the leak test and the cold test per part.
"""
import math
import statistics

import pytest

from plant.model import building as B
from plant.model import catalog as C
from plant.model.assembly import ColdTest, LeakTester, PartsWasher
from plant.model.furnace import Furnace
from plant.model.gas import GasSupply
from plant.model.mech_press import MechanicalPress
from plant.model.plant import UtilitiesPlant


def test_building_services_follow_their_formulas():
    p = UtilitiesPlant(t_day0_h=10.0)
    p.step(0.1)
    # roof fans: bay volume x air changes / (0.8 x 56,700 m3/h per fan), rounded up
    need = C.FAB_BAY_M2 * C.FAB_BAY_H_M * C.ACH_FAB
    assert p.vent_fab.n == math.ceil(need / (0.8 * 56700.0))
    assert p.vent_fab.q_m3h >= need
    assert p.vent_fab.p_el == pytest.approx(p.vent_fab.n * 7.5e3 * 0.9)
    # lighting: power density x area
    assert p.light_fab.p_el == pytest.approx(0.82 * 10.764 * C.FAB_BAY_M2)
    # the air washers run on a 35 C day: fan Q dp / eta + the pumps
    w = p.washers_fab
    assert w.on and w.p_el == pytest.approx(w.q_m3h / 3600 * 145.0 / 0.6 + w.n * 370.0)
    # office in office hours, then at night
    day = p.aircon.p_el + p.plug.p_el + p.light_office.p_el
    q = UtilitiesPlant(t_day0_h=23.0)
    q.step(0.1)
    night = q.aircon.p_el + q.plug.p_el + q.light_office.p_el
    assert day > 2.5 * night > 0.0
    # every building load is in the tags
    tags = p.tags()
    for ld in p.building:
        assert f"{ld.name}.power_kw" in tags


def test_cranes_lift_and_air_washers_switch_with_the_weather():
    cr = B.EotCrane("crane-x", C.EOT_15T, seed=3)
    peak = 0.0
    for _ in range(int(400 / 0.5)):
        cr.step(0.5, 10.0)
        peak = max(peak, cr.p_el)
    assert cr.lifts == 2
    assert 32.5e3 * 1.5 / 15.5 <= peak <= 32.5e3 * 4.5 / 15.5 + 1.0     # 1 to 4 t on a 15 t hoist
    aw = B.AirWashers("aw", C.AIR_WASHER, 100000.0)
    aw.step(1.0, 12.0, 25.0)
    assert aw.p_el == 0.0
    aw.step(1.0, 12.0, 31.0)
    assert aw.p_el > 0.0


def test_site_never_exports_also_at_full_regeneration():
    p = UtilitiesPlant(t_day0_h=2.0)        # night: no office load, the lowest site load
    lowest = math.inf
    for _ in range(int(3600 / 0.2)):
        p.step(0.2)
        assert p.check() == []
        lowest = min(lowest, p.grid.bus.s_down.real)
    assert lowest > 0.0
    assert lowest >= 0.5 * UtilitiesPlant.EXPORT_MARGIN_W   # the limiter keeps the import (one tick lag)


def test_export_limiter_curtails_regen_on_a_cool_night():
    # 25 C: the air washers stay off, the site load is at its lowest; the dyno at its rated stage
    # would export without the limiter, so its braking resistor takes the rest
    p = UtilitiesPlant(t_day0_h=2.0, t_amb_c=25.0)
    lowest, burnt = math.inf, 0.0
    for _ in range(int(3600 / 0.2)):
        p.step(0.2)
        assert p.check() == []
        lowest = min(lowest, p.grid.bus.s_down.real)
        burnt = max(burnt, p.hot.p_resistor)
    assert lowest > 0.0
    assert "hot-1.resistor_kw" in p.tags()


def _md15(xs, per_s=1):
    w = 900 * per_s
    return max(sum(xs[i:i + w]) / w for i in range(0, len(xs) - w, 60))


def test_transformers_are_loaded_for_the_shop():
    p = UtilitiesPlant(t_day0_h=8.0)
    for _ in range(int(1800 / 0.2)):
        p.step(0.2)
    k1, k2 = [], []
    for k in range(int(3 * 3600 / 0.2)):
        p.step(0.2)
        if k % 5 == 0:
            k1.append(p.tr1.k)
            k2.append(p.tr2.k)
    for ks in (k1, k2):
        assert _md15(ks) <= 0.80
        assert 0.30 <= statistics.mean(ks) <= 0.75
        assert max(ks) <= 1.0


def test_furnace_zones_differ_in_heatup_and_meet_api_at_hold():
    gas = GasSupply("gas", C.GAS_SUPPLY)
    f = Furnace("f", C.CAR_BOTTOM_SR, gas)
    f.start()
    spread_heat, hold_dev = 0.0, []
    t = 0.0
    while t < 30 * 3600 and f.state != f.DONE:
        gas.step(5.0)
        f.step(5.0)
        t += 5.0
        if f.state == f.HEAT:
            spread_heat = max(spread_heat, max(f.t_l) - min(f.t_l))
        if f.state == f.HOLD and f.t_state > 600.0:
            hold_dev += [abs(f.zone_reading(i) - f.setpoint) for i in range(f.r.zones)]
    assert f.a_wall[0] > f.a_wall[1] and f.a_wall[-1] > f.a_wall[-2]
    assert sum(f.a_wall) == pytest.approx(C.CAR_BOTTOM_SR.wall_area_m2, rel=0.01)
    assert spread_heat > 10.0                # the heavy, walled ends lag in the heat-up
    tags = f.tags()
    assert "fire1_pct" in tags and "fire4_pct" in tags
    assert hold_dev and max(hold_dev) <= 8.0  # API stress relief: +-8 C of the setpoint


def test_washer_thermostat_band():
    w = PartsWasher("w", C.MECWASH)
    temps = []
    for k in range(int(4 * 3600 / 1.0)):
        w.step(1.0)
        if k > 1800:
            temps.append(w.t_tank)
    assert min(temps) >= 80.0 - 2.0 - 1.0 and max(temps) <= 80.0 + 0.5
    assert max(temps) - min(temps) > 1.0     # a real on/off band, not a fixed value


def test_press2_brake_time_moves_with_friction_and_speed():
    pr = MechanicalPress("p2", C.AIDA_NC1_2000, C.YH2_160L_4, seed=1)
    pr.motor.w = pr.w_m_n
    stops = []
    n = 0
    for _ in range(int(600 / 0.02)):
        pr.step(0.02, 400.0, 50.0)
        if pr.strokes != n:
            n = pr.strokes
            stops.append(pr.last_stop_ms)
    assert len(stops) > 20
    assert 165.0 <= statistics.mean(stops) <= 195.0
    assert statistics.pstdev(stops) > 0.5
    assert max(stops) < C.AIDA_NC1_2000.brake_limit_ms   # a healthy brake never trips the monitor


def test_material_batches_change_plate_and_blank():
    p = UtilitiesPlant(seed=2, with_assembly=False, flow=False)
    seen_uts, seen_t = set(), set()
    for _ in range(int(3 * 3600 / 0.2)):
        p.step(0.2)
        seen_uts.add(round(p.press.plate.uts_mpa))
        seen_t.add(round(p.press2.blank.t_mm, 2))
    assert len(seen_uts) >= 2 and len(seen_t) >= 2
    assert all(470 <= u <= 630 for u in seen_uts)
    assert all(9.5 <= t <= 11.2 for t in seen_t)


def test_leak_and_cold_test_per_part():
    lt = LeakTester("l", C.LEAK_TEST, t_room=35.0, seed=4)
    decays = [lt.test(35.0)[0] for _ in range(2000)]
    rejects = sum(1 for d in decays if d >= C.LEAK_TEST.reject_pa)
    assert 5 <= rejects <= 30                # 0.5 % porosity leakers, the rest pass at room temperature
    assert statistics.pstdev(decays[:200]) > 0.0
    assert "last_decay_pa" in lt.tags()
    ct = ColdTest("c", C.QSL9_F85, seed=5)
    for _ in range(int(20 * 180 / 1.0)):
        ct.step(1.0)
    torques = [m for m, _ in ct.results]
    assert len(torques) >= 15
    assert 0.01 < statistics.pstdev(torques) / statistics.mean(torques) < 0.06
    assert all(ok for _, ok in ct.results)   # a 3 % spread stays inside the +-20 % band
    assert "last_torque_nm" in ct.tags()


def test_apfc_corrects_the_site_power_factor():
    """KSEB and MERC tariffs penalise a power factor under 0.95 or 0.90, so each LV main has an APFC
    panel (target 0.98 lagging). Without it the site ran at a median PF of 0.84 (0.55 at the
    lowest). Tariffs bill the average power factor, kWh / kVAh over the period (MSEDCL
    methodology). With the panels the billing PF is at least 0.95. The instantaneous PF still dips
    while hot-1 feeds back: the real power at the incomer falls and the reactive power of the
    transformers and motors stays, as on a real site."""
    p = UtilitiesPlant(t_day0_h=8.0)
    kwh = kvah = 0.0
    pfs, switches, last = [], 0, [0, 0]
    for k in range(int(3600 / 0.2)):
        p.step(0.2)
        if k * 0.2 > 600 and k % 5 == 0:
            s = p.grid.bus.s_down
            kwh += s.real
            kvah += abs(s)
            pfs.append(s.real / abs(s))
            for j, cb in enumerate(p.apfc):
                # a step controller removes one step per delay, so a sudden load drop can leave a
                # main briefly leading, by less than two steps
                assert cb.bus.s_down.imag > -2.0 * cb.r.step_kvar * 1e3
                switches += cb.n != last[j]
                last[j] = cb.n
    assert kwh / kvah >= 0.95
    assert statistics.median(pfs) >= 0.95
    assert switches <= 60                    # a step controller, not a chattering one
    assert "apfc-1.kvar" in p.tags()
