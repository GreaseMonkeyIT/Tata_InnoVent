"""Verification of chiller-1 (plant/model/chiller.py): a Daikin EWAD190AJYNN air-cooled screw
chiller behind a tempering valve on the process loop cool-1.

  1. The model returns the Daikin table at table points, and between them a value between the
     neighbours (7 C LWT at 35 C ambient: 184.0 kW cooling, 76.7 kW compressor input).
  2. A hotter day costs capacity and power (44 C ambient against 35 C), as the table says.
  3. On the loop, the tempering valve holds the process supply at 27 C while the load is under
     the chiller's capacity at its 14 C leaving water.
  4. Above capacity the valve opens fully, the chiller's leaving water rises, and the process
     supply rises: the loop warms (F7, F14 paths).
  5. Anti-recycle: after a stop (an undervoltage trip) the unit waits its start-to-start time.
  6. Condenser fouling (F9) acts as a hotter day: less capacity, more power at the same load.
  7. The COP stays physical (2 to 6 for this unit across the table).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from model import catalog as C                                    # noqa: E402
from model.chiller import AirCooledChiller                        # noqa: E402
from model.cooling import Branch, CoolingLoop                     # noqa: E402
from model.pump import Pump, PumpRating                           # noqa: E402

R = C.EWAD190AJYNN
PUMP = PumpRating(q_m3h=60.0, h_m=25.0, eta=0.72, n_rpm=2900.0, h0_m=32.0, source="test")


def test_table_points_and_interpolation():
    ch = AirCooledChiller("chiller-1", R, t_amb_c=35.0)
    assert ch.capacity_w(7.0) == pytest.approx(184.0e3, rel=1e-9)
    assert ch.full_power_w(7.0) == pytest.approx(76.7e3, rel=1e-9)
    mid = ch.capacity_w(7.5)
    assert ch.capacity_w(7.0) < mid < ch.capacity_w(8.0)


def test_hot_day_costs_capacity():
    a = AirCooledChiller("a", R, t_amb_c=35.0)
    b = AirCooledChiller("b", R, t_amb_c=44.0)
    assert b.capacity_w(14.0) < 0.80 * a.capacity_w(14.0)


def plant(load_w, t_amb=35.0):
    lp = CoolingLoop("cool-1", Pump("p", PUMP), volume_m3=4.0, k_series=1.0e4, t0=27.0)
    lp.add(Branch("users", 2.0e4, heat=lambda t, m: load_w))
    ch = AirCooledChiller("chiller-1", R, t_amb_c=t_amb)
    lp.chiller = ch
    return lp, ch


def run(lp, secs, dt=1.0):
    for _ in range(int(secs / dt)):
        lp.step(dt)
        assert lp.check() == [] and lp.chiller.check() == []


def test_tempering_holds_process_supply():
    lp, ch = plant(150e3)
    run(lp, 3600.0)
    assert lp.t_supply == pytest.approx(27.0, abs=0.3)
    assert ch.lwt == pytest.approx(14.0)
    cop = ch.q_w / ch.p_in
    assert 2.0 < cop < 6.0


def test_above_capacity_the_loop_warms():
    lp, ch = plant(300e3)               # more than the 222.5 kW at 14 C / 35 C
    run(lp, 3600.0)
    assert lp.t_supply > 29.0
    assert ch.lwt > 14.0
    assert ch.load == pytest.approx(1.0, abs=0.01)


def test_undervoltage_stop_and_anti_recycle():
    lp, ch = plant(150e3)
    run(lp, 900.0)
    ch.v = 0.80 * R.v_nom               # under the 85 % stage for more than 2 s
    run(lp, 5.0)
    assert not ch.running
    ch.v = R.v_nom
    t_stop = ch.t
    while not ch.running:
        lp.step(1.0)
    assert ch.t - ch.last_start == 0.0
    assert ch.last_start - t_stop >= R.anti_recycle_s - 900.0 - 6.0   # 600 s from the last start


def test_condenser_fouling_acts_as_a_hotter_day():
    a, b = AirCooledChiller("a", R, t_amb_c=35.0), AirCooledChiller("b", R, t_amb_c=35.0)
    b.fouling_k = 6.0
    assert b.capacity_w(14.0) < a.capacity_w(14.0)
    la, ca = plant(150e3)
    lb, cb = plant(150e3)
    cb.fouling_k = 6.0
    run(la, 1800.0)
    run(lb, 1800.0)
    assert cb.p_in > ca.p_in * 1.05
