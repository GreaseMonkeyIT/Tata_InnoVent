"""The instrument layer finished (task 24, 2026-10-28): the three phases and the negative sequence
(unbalance.py), the waveform bursts (bursts.py), the per-part records, and the instrument front end
(instruments.py). Each is off by default and leaves every existing tag as it was."""
import cmath
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import instruments as I                       # noqa: E402
from model import unbalance as U                         # noqa: E402
from model.assembly import ANGLE_SIGMA_DEG, ANGLE_TARGET_DEG  # noqa: E402
from model.bursts import BurstSpec, snapshot, waveform   # noqa: E402
from model.plant import UtilitiesPlant                   # noqa: E402


def _run(n, **kw):
    a, b = UtilitiesPlant(seed=5), UtilitiesPlant(seed=5, **kw)
    same = True
    for k in range(n):
        a.step(0.1)
        b.step(0.1)
        if k % 50 == 49:
            ta, tb = a.tags(), b.tags()
            same &= all(repr(ta[x]) == repr(tb[x]) for x in ta)
    return a, b, same


@pytest.fixture(scope="module")
def balanced():
    return _run(1200, three_phase=True)


@pytest.fixture(scope="module")
def unbalanced():
    return _run(1200, three_phase=True, supply_unbalance=0.01, supply_unbalance_deg=30.0)


def test_balanced_supply_gives_todays_values_and_symmetric_phases(balanced):
    a, b, same = balanced
    assert same
    t = b.tags()
    for bus in ("incomer-1", "psu-a", "psu-b", "psu-c", "psu-d"):
        v = [t[f"{bus}.v{p}_v"] for p in "abc"]
        ang = [t[f"{bus}.v{p}_deg"] for p in "abc"]
        assert max(v) - min(v) < 1e-9 * max(v) and t[f"{bus}.v2_v"] == 0.0 and t[f"{bus}.unbalance_pct"] == 0.0
        assert ((ang[0] - ang[1]) % 360.0) == pytest.approx(120.0) and ((ang[1] - ang[2]) % 360.0) == pytest.approx(120.0)
    assert t["psu-b.va_v"] * math.sqrt(3.0) == pytest.approx(t["psu-b.voltage_v"])
    assert t["compressor-1.i2_a"] == 0.0 and t["compressor-1.ia_a"] == pytest.approx(t["compressor-1.current_a"])


def test_supply_unbalance_reaches_the_buses_and_the_motors(unbalanced):
    """1 % at the source. The 11 kV bus keeps about it, the MCC buses less (the motors draw the negative
    sequence: their impedance there is near the locked-rotor one). A motor's current unbalance is 6 to
    10 times the voltage unbalance at its terminals (the rule behind DOE tip sheet 7). Every existing tag
    stays the same."""
    a, b, same = unbalanced
    assert same
    t = b.tags()
    assert 0.95 < t["incomer-1.unbalance_pct"] <= 1.0 and t["psu-b.unbalance_pct"] < t["incomer-1.unbalance_pct"]
    i1, i2 = t["compressor-1.current_a"], t["compressor-1.i2_a"]
    assert 6.0 <= (i2 / i1) / (t["psu-b.unbalance_pct"] / 100.0) <= 10.0
    r = b.comp.motor.rating
    z_lr = r.v_ll / math.sqrt(3.0) / (r.is_in * r.i_a)
    assert t["compressor-1.z2_ohm"] == pytest.approx(z_lr, rel=0.3) and 60.0 < t["compressor-1.z2_deg"] < 85.0
    # Fortescue back from the phase tags: V1 = (Va + a Vb + a^2 Vc) / 3, V2 = (Va + a^2 Vb + a Vc) / 3
    va, vb, vc = (cmath.rect(t[f"psu-b.v{p}_v"], math.radians(t[f"psu-b.v{p}_deg"])) for p in "abc")
    assert abs((va + U.A2 * vb + U.A * vc) / 3.0) == pytest.approx(t["psu-b.v2_v"], rel=1e-9)
    assert abs((va + U.A * vb + U.A2 * vc) / 3.0) * math.sqrt(3.0) == pytest.approx(t["psu-b.voltage_v"], rel=1e-9)
    assert max(t[f"compressor-1.i{p}_a"] for p in "abc") > i1 > min(t[f"compressor-1.i{p}_a"] for p in "abc")


def test_burst_holds_the_phases_and_the_rotor_sidebands(unbalanced):
    """A 10 s burst at 10 kHz: the RMS of each phase is its line current, the sidebands sit at
    (1 -+ 2s) f and the set level under the fundamental, 0.1 Hz apart is resolved."""
    _, b, _ = unbalanced
    snap = snapshot(b, "compressor-1")
    w = waveform(snap, BurstSpec(), seed=1)
    t = b.tags()
    assert len(w["ia"]) == 100000 and w["t"][1] == pytest.approx(1e-4)
    for p in "abc":
        assert math.sqrt(np.mean(w[f"i{p}"] ** 2)) == pytest.approx(t[f"compressor-1.i{p}_a"], rel=2e-3)
    for level in (-60.0, -40.0):
        x = w["ia"] if level == -60.0 else waveform(dict(snap, sideband_db=level), BurstSpec(), seed=1)["ia"]
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        f = np.fft.rfftfreq(len(x), 1e-4)
        fund = spec[np.argmin(abs(f - 50.0))]
        for fq in (50.0 * (1.0 - 2.0 * snap["slip"]), 50.0 * (1.0 + 2.0 * snap["slip"])):
            j = np.argmin(abs(f - fq))
            assert 20.0 * math.log10(spec[j - 1:j + 2].max() / fund) == pytest.approx(level, abs=1.0)


def test_burst_recorder_keeps_steady_bursts_only():
    p = UtilitiesPlant(seed=5, bursts={"period_s": 30.0})
    for _ in range(9000):
        p.step(0.1)
    bs = p.bursts.bursts
    assert any(x["valid"] for x in bs) and all(x["valid"] in (True, False) for x in bs[:-1])
    assert all(p.bursts.get(k) is None for k, x in enumerate(bs) if x["valid"] is False)
    assert {x["state"] for x in bs} <= {"loaded", "unloaded"}


def test_per_part_records_leave_the_plant_as_it_was():
    """One record per bolt (torque and angle), leak test (decay, verdict), cold test (torque, verdict)
    and weld seam (time, current, ripple, voltage, energy). The torques are the station's own; the angle
    scatters about its target with the tool's capability; a seam's energy is its current x voltage x time."""
    a, b, same = _run(30000, records=True)
    assert same and a.tight[0].torques == b.tight[0].torques and a.leak.results == b.leak.results
    rec = b.part_records
    bolts = [r for r in rec if r["kind"] == "bolt"]
    assert [r["torque_nm"] for r in bolts if r["station"] == "tight-1"] == b.tight[0].torques
    ang = np.array([r["angle_deg"] for r in bolts])
    assert len(ang) > 40 and abs(ang.mean() - ANGLE_TARGET_DEG) < 1.0 and 0.6 < ang.std() / ANGLE_SIGMA_DEG < 1.4
    leaks = [(r["decay_pa"], r["ok"]) for r in rec if r["kind"] == "leak"]
    assert leaks == b.leak.results
    colds = [(r["torque_nm"], r["ok"]) for r in rec if r["kind"] == "cold"]
    assert colds == b.cold.results
    seams = [r for r in rec if r["kind"] == "seam"]
    assert seams and all(r["energy_kj"] * 1000.0 == pytest.approx(r["current_a"] * r["voltage_v"] * r["duration_s"], rel=0.01)
                         for r in seams)
    assert not any(r["station"].startswith("qa-scanner") for r in rec)
    assert UtilitiesPlant(seed=5).part_records is None


def test_front_end_steps():
    """Damping (63 % after one time constant), the averaging window, the register refresh, the deadband
    with the maximum time between records, the failures and the quality."""
    v, q = I.front_end([0.0] + [1.0] * 40, I.FrontEnd(damping_s=5.0))
    assert v[5] == pytest.approx(1.0 - math.exp(-5.0 / 5.0)) and q == ["Good"] * 41
    v, _ = I.front_end([0.0, 0.0, 0.0, 3.0, 3.0, 3.0], I.FrontEnd(avg_s=3.0))
    assert v == [0.0, 0.0, 0.0, 1.0, 2.0, 3.0]
    v, _ = I.front_end([float(k) for k in range(6)], I.FrontEnd(refresh_s=2.0))
    assert v == [0.0, 0.0, 2.0, 2.0, 4.0, 4.0]
    v, _ = I.front_end([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6], I.FrontEnd(deadband=1.0, max_hold_s=4.0))
    assert v == [0.0, 0.0, 0.0, 0.0, 0.4, 0.4, 0.4]
    v, _ = I.front_end([10.0] * 6, I.FrontEnd(deadband_pct=5.0, range_hi=100.0))
    assert v == [10.0] * 6
    v, q = I.front_end([1.0] * 10, I.FrontEnd(drift_per_h=36.0, drift_from_s=5.0, stuck=(7.0, 9.0), dropout=(2.0, 3.0)))
    assert v[2] is None and q[2] == I.BAD_SENSOR and v[6] == pytest.approx(1.01) and v[7] == v[8] == v[6] + 0.01
    v, q = I.front_end([50.0, 130.0, "run", None], I.FrontEnd(range_hi=100.0))
    assert q == ["Good", I.UNCERTAIN_EGU, "Good", "Good"] and v[2:] == ["run", None]


def test_default_front_ends_and_the_new_accuracies():
    assert I.default_front_end("washer-1.tank_c").damping_s == 5.0
    assert I.default_front_end("compressor-1.pressure_barg").damping_s == 0.4
    assert I.default_front_end("cool-1.flow_m3h").damping_s == 2.0
    assert I.default_front_end("compressor-1.current_a").refresh_s == 1.0
    assert I.default_front_end("compressor-1.state") == I.FrontEnd()
    assert I._accuracy("incomer-1.unbalance_pct", 0.7) == (0.15, 0.01)
    assert I._accuracy("psu-a.va_deg", -120.0) == (1.0, 0.1)
    inst = I.Instruments(0)
    col = inst.read_column("psu-a.va_deg", [-120.0] * 1000, 1)
    assert abs(np.mean(col) + 120.0) <= 1.0 + 0.1
