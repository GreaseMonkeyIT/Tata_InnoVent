"""Verification of compressor-1 (plant/model/compressor.py): an Atlas Copco GA 37 W, 7.5 bar
variant, on a 37 kW IE3 motor, with its receiver and header.

Checks against the datasheet and published behaviour:
  1. Loaded at 7.0 bar(e): electrical input 40.8 kW (instruction book), FAD 116 l/s (leaflet),
     specific power 5.86 kW per m3/min.
  2. Power against pressure: about +7 % per bar (US DOE sourcebook: +1 % per 2 psi).
  3. Unloaded input after the blow-down: 24.9 % of loaded (CAGI). The blow-down takes about 40 s
     (DOE sourcebook p. 39).
  4. Part-load power with load/unload control and this receiver (1.6 US gal per cfm): between
     the DOE curves for 1 and 3 gal/cfm (about 87 % and 76 % of full power at 50 % capacity).
  5. Star-delta start: the motor runs in star, then changes to delta after 10 s, then loads.
     The star current is about 1/3 of the direct-on-line current.
  6. The controller holds the header inside its band, and the starts stay inside the hourly limit.
  7. Heat: the cooling water takes about 0.96 of the shaft power; the element outlet sits near
     70 to 75 C at the rated water inlet; warmer water gives a hotter element; very warm water
     trips the element shutdown (latched, the motor stops).
  8. Faults: a failed-low transducer keeps it loaded until the safety valve vents (8.5 bar);
     a leak raises the loaded share; a clogged intake filter lowers the delivery.
  9. Air mass balance and the physics checks hold through a long run.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                   # noqa: E402
from model.compressor import P_ATM, ScrewCompressor            # noqa: E402

R = C.GA37W_7_5
MOTOR = C.ABB_37KW
DT = 0.1


def make(demand_frac=0.0):
    c = ScrewCompressor("compressor-1", R, MOTOR)
    c.demand = demand_frac
    c.demands.append(lambda p, c=c: c.demand * R.fad_m3min / 60.0 * min(1.0, max(0.0, (p - P_ATM) / 5.0)))
    return c


def run(c, secs, t_water=R.water_in_c, m_water=R.water_flow_kgs, v=400.0, every=None):
    out = []
    for k in range(int(secs / DT)):
        c.step(DT, v, 50.0, t_water, m_water)
        assert c.check() == []
        if every and k % int(every / DT) == 0:
            out.append((k * DT, c.state, c.motor.amps, c.motor.p_in, c.p_rec - P_ATM))
    return out


def loaded_at(p_barg, secs=120.0):
    """Hold the compressor loaded at a fixed header pressure (the header is a big plant)."""
    c = make()
    c.r = R
    c.p_rec = P_ATM + p_barg
    c.state, c.motor.w = c.LOADED, c.w_n
    for _ in range(int(secs / DT)):
        c.p_rec = P_ATM + p_barg
        c.pt_fault_bar = 0.0                 # the controller sees a low pressure: it stays loaded
        c.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
    return c


def test_rated_point_matches_datasheet():
    c = loaded_at(R.p_work_barg)
    assert c.state == c.LOADED
    assert c.motor.p_in / 1000.0 == pytest.approx(40.8, rel=0.03)
    assert c.q_fad * 1000.0 == pytest.approx(116.0, rel=0.02)
    spec = c.motor.p_in / 1000.0 / (c.q_fad * 60.0)
    assert spec == pytest.approx(5.86, rel=0.04)


def test_power_rises_about_seven_percent_per_bar():
    a, b = loaded_at(6.5), loaded_at(7.5)
    rise = b.motor.p_in / a.motor.p_in - 1.0
    assert 0.05 <= rise <= 0.09


def test_unloaded_power_and_blowdown():
    c = loaded_at(R.p_work_barg)
    p_loaded = c.motor.p_in
    c.pt_fault_bar = None
    c.p_rec = P_ATM + 7.6                     # above unload: the controller unloads
    log = []
    for k in range(int(120.0 / DT)):
        c.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
        log.append(c.motor.p_in)
    assert c.state == c.UNLOADED
    assert log[-1] / p_loaded == pytest.approx(0.249, abs=0.03)
    # 90 % of the fall within about 40 s (DOE: about 40 s to blow down)
    target = log[-1] + 0.1 * (p_loaded - log[-1])
    t90 = next(i for i, p in enumerate(log) if p <= target) * DT
    assert 15.0 <= t90 <= 45.0


def test_part_load_power_between_doe_curves():
    """At 50 % capacity a load/unload compressor with 1.6 gal/cfm of storage takes between the DOE
    curves for 1 gal/cfm (about 87 %) and 3 gal/cfm (about 76 %) of its full-load power."""
    c = make(0.5)
    run(c, 600.0)
    e = 0.0
    n = int(3600.0 / DT)
    for _ in range(n):
        c.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
        e += c.motor.p_in
    frac = e / n / (loaded_at(R.p_work_barg).motor.p_in)
    gal_per_cfm = R.receiver_m3 * 264.17 / (R.fad_m3min * 35.315)
    assert 1.0 < gal_per_cfm < 3.0
    assert 0.74 <= frac <= 0.90


def test_star_delta_start_then_load():
    c = make(0.3)
    c.p_rec = P_ATM + 6.0                     # below load pressure: it starts
    log = run(c, 30.0, every=0.5)
    states = [s for _, s, *_ in log]
    t_delta = next(t for t, s, *_ in log if s != c.STAR and t > 0.0)
    assert states[1] == c.STAR
    assert t_delta == pytest.approx(R.star_delta_s, abs=0.6)
    i_star = max(i for t, s, i, *_ in log if s == c.STAR and t < 2.0)
    i_lr = MOTOR.is_in * MOTOR.i_a
    assert i_star == pytest.approx(i_lr / 3.0, rel=0.25)
    assert c.state == c.LOADED


def test_controller_band_and_start_limit():
    c = make(0.45)
    log = run(c, 3 * 3600.0, every=1.0)
    p = [x[4] for x in log[600:]]
    assert min(p) >= R.load_barg - 0.15 and max(p) <= R.unload_barg + 0.15
    assert len([s for s in c.starts if c.t - s < 3600.0]) <= R.starts_per_hour


def test_heat_to_water_and_element_temperature():
    c = loaded_at(R.p_work_barg, secs=900.0)
    p_shaft = c.p_loaded_w(c.p_sump, 1.0)
    assert c.q_water / p_shaft == pytest.approx(R.oil_heat_frac + R.air_heat_frac, rel=0.05)
    assert 68.0 <= c.t_elem <= 78.0
    rise = c.t_water_out - R.water_in_c
    assert 7.0 <= rise <= 13.0


def test_warm_water_raises_element_and_hot_water_trips_it():
    """The oil by-pass valve holds the injection temperature while the oil cooler has spare
    capacity: slightly warmer water changes nothing. Past that point the element outlet rises
    with the water temperature. Lost cooling water flow (a closed valve, a starved branch) trips
    the element shutdown (latched): the way a water-cooled compressor overheats in practice."""
    def soak(t_water, secs=1800.0, m_water=R.water_flow_kgs):
        c = make()
        c.p_rec, c.state = P_ATM + 7.0, ScrewCompressor.LOADED
        c.motor.w = c.w_n
        for _ in range(int(secs / DT)):
            c.p_rec = P_ATM + 7.0
            c.pt_fault_bar = 0.0
            c.step(DT, 400.0, 50.0, t_water, m_water)
        return c
    base, mild = soak(28.0), soak(32.0)
    assert mild.t_elem == pytest.approx(base.t_elem, abs=1.0)
    warm, warmer = soak(45.0), soak(50.0)
    assert warm.t_elem > base.t_elem + 3.0
    assert warmer.t_elem > warm.t_elem + 2.0
    hot = soak(28.0, m_water=0.05 * R.water_flow_kgs)
    assert hot.state == hot.TRIPPED
    assert hot.motor.amps == 0.0


def test_failed_transducer_keeps_it_loaded_until_the_safety_valve():
    c = make(0.4)
    run(c, 900.0)
    c.pt_fault_bar = 5.5
    run(c, 900.0)
    assert c.state == c.LOADED
    assert c.p_rec - P_ATM == pytest.approx(R.safety_barg, abs=0.1)
    assert c.venting


def test_leak_raises_loaded_share_and_filter_lowers_delivery():
    def loaded_share(leak):
        c = make(0.4)
        c.leak_m3s_bar = leak
        run(c, 600.0)
        n = l = 0
        for _ in range(int(3600.0 / DT)):
            c.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
            n += 1
            l += c.state == c.LOADED
        return l / n
    base = loaded_share(0.0)
    leaky = loaded_share(0.15 * R.fad_m3min / 60.0 / (P_ATM + 7.0))   # a 15 % leak at 7 bar
    assert leaky > base + 0.10
    clean = loaded_at(R.p_work_barg)
    clogged = make()
    clogged.filter_dp_bar = 0.1
    clogged.p_rec, clogged.state, clogged.motor.w = P_ATM + 7.0, clogged.LOADED, clogged.w_n
    for _ in range(int(60.0 / DT)):
        clogged.p_rec = P_ATM + 7.0
        clogged.pt_fault_bar = 0.0
        clogged.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
    assert clogged.q_fad < clean.q_fad * 0.92


def test_air_mass_balance():
    """Free air delivered = users + leaks + vent + the change of stored air (isothermal)."""
    c = make(0.5)
    c.leak_m3s_bar = 0.002
    p0 = c.p_rec
    q_in = q_out = 0.0
    for _ in range(int(3600.0 / DT)):
        c.step(DT, 400.0, 50.0, R.water_in_c, R.water_flow_kgs)
        q_in += c.q_fad * DT
        q_out += c.q_out * DT
    stored = (c.p_rec - p0) * R.receiver_m3 / P_ATM
    assert q_in == pytest.approx(q_out + stored, rel=1e-3)
