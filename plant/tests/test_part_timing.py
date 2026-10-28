"""Device time, links and the PLC scan (timing.py, devices.py; task 23, 2026-10-28): the crystal drift and
its temperature curve, the sync profiles and an outage that stops them, the link delay, loss and wire
time, the PLC scan, its input image and its watchdog, and the device layer on the plant, which leaves
every plant tag as it was."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import timing as T                          # noqa: E402
from model.devices import DeviceConfig, device_of      # noqa: E402
from model.plant import UtilitiesPlant                 # noqa: E402

DAY = 86400.0


def test_crystal_drift_and_its_temperature_curve():
    """20 ppm is 1.7 s a day (Silicon Labs); over 0 to 70 C a crystal of +-20 ppm at 25 C stays inside
    -101 to +20 ppm (Schneider FA12099), its worst point at 70 C."""
    c = T.Clock("x", offset_s=0.0, drift_ppm=20.0)
    c.advance(DAY)
    assert c.offset == pytest.approx(1.728, rel=1e-9)
    drifts = [T.Clock(f"d{k}", seed=k).drift_ppm(t) for k in range(200) for t in (0.0, 25.0, 45.0, 70.0)]
    assert min(drifts) >= -101.0 - 1e-9 and max(drifts) <= 20.0 + 1e-9
    worst = T.Clock("w", drift_ppm=-20.0)
    assert worst.drift_ppm(70.0) == pytest.approx(-101.0) and worst.drift_ppm(25.0) == -20.0
    hot = T.Clock("h", offset_s=0.0, drift_ppm=0.0, t_c=lambda t: 70.0)
    hot.advance(DAY)
    assert hot.offset == pytest.approx(-81e-6 * DAY)                  # -7.0 s a day from the curve alone


@pytest.mark.parametrize("prof,bound", [(T.NTP, 6e-3), (T.PTP, 30e-6), (T.IRIG_B, 5e-3)])
def test_sync_holds_the_clock_and_an_outage_lets_it_drift(prof, bound):
    """A synced clock stays near true time: the residual of a sync plus the drift until the next one
    (NTP every 64 s: about 1 ms + 1.3 ms; PTP every 1 s: 1 us + 20 us). With no sync it drifts 1.7 s a
    day. An outage of its link stops the sync: the offset grows at the drift until the link is back."""
    def run(sync, outage=()):
        c = T.Clock("c", seed=1, offset_s=0.5, drift_ppm=20.0)
        link = T.Link(T.LINKS["ethernet"], seed=1, name="c", outages=outage)
        s = T.SyncClient(c, prof, link, seed=1) if sync else None
        worst, offs = 0.0, []
        for k in range(1, 3601):
            t = k * 10.0
            if s:
                s.step(t)
            off = c.now(t) - t
            offs.append(off)
            if k > 10:
                worst = max(worst, abs(off))
        return worst, offs, s
    worst, _, s = run(True)
    assert worst < bound and s.synced > 0 and s.missed == 0
    free, offs, _ = run(False)
    assert offs[-1] == pytest.approx(0.5 + 20e-6 * 36000.0)
    _, offs, s = run(True, outage=[(10000.0, 20000.0)])
    during = offs[int(19990 / 10) - 1]
    assert abs(during) > 0.15 and s.missed > 0                         # 10,000 s at 20 ppm: 0.2 s
    assert abs(offs[int(21000 / 10) - 1]) < bound                      # back after the outage


def test_link_delay_loss_wire_time_and_outage():
    """A Modbus RTU frame takes 11 bits a byte at 19,200 baud; the delay is the fixed delay, the wire
    time and an exponential jitter; losses come at the set share; nothing passes an outage."""
    p = T.LINKS["modbus-rtu"]
    assert p.wire_s(8) == pytest.approx(8 * 11 / 19200.0)
    assert p.delay_s == pytest.approx(0.020 + 3.5 * 11 / 19200.0)    # the 3.5-character gap: 2.0 ms
    link = T.Link(p, seed=3)
    d = [link.send(0.0, 8) for _ in range(20000)]
    extra = [x - p.delay_s - p.wire_s(8) for x in d]
    assert min(extra) >= 0.0 and sum(extra) / len(extra) == pytest.approx(p.jitter_s, rel=0.05)
    lossy = T.Link(T.LinkProfile("l", 1e-3, 0.0, 0.1, 1e6), seed=4)
    lost = sum(lossy.send(0.0) is None for _ in range(20000)) / 20000
    assert lost == pytest.approx(0.1, abs=0.01)
    out = T.Link(p, seed=5, outages=[(10.0, 20.0)])
    assert out.send(15.0) is None and out.send(25.0) is not None and not out.up(10.0) and out.up(20.0)


def test_plc_scan_reads_inputs_once_per_scan_and_its_watchdog_stops_it():
    """Scans of 8 to 12 ms (mean 10 ms). The input image is read once at the start of a scan, so a
    pulse shorter than a scan can fall between two reads; an output follows an input within two scans.
    Under communication load a scan grows; past the 150 ms watchdog the CPU stops."""
    sc = T.ScanCycle("plc", seed=1)
    reads = []
    sc.run(10.0, lambda t: reads.append(t) or False, lambda x: x)
    times = [d for _, d, _ in sc.log]
    assert 0.008 <= min(times) and max(times) <= 0.012 and sum(times) / len(times) == pytest.approx(0.010, rel=0.02)
    assert len(reads) == len(sc.log) and all(b - a == pytest.approx(d) for (a, d, _), b in zip(sc.log, reads[1:]))
    a0 = max(r for r in reads if r < 5.0)                              # a pulse of 4 ms just after a read
    for lo, hi, seen in ((a0 + 1e-4, a0 + 4.1e-3, False), (a0 - 1e-3, a0 + 3e-3, True)):
        again = T.ScanCycle("plc", seed=1)                             # the same scans
        again.run(10.0, lambda t, lo=lo, hi=hi: lo <= t < hi, lambda x: x)
        assert any(o for _, _, o in again.log) is seen                 # missed between two reads, seen over one
    sc2 = T.ScanCycle("plc2", seed=2)
    sc2.run(10.0, lambda t: t >= 5.0, lambda x: x)
    first_in = next(t for t in (a for a, _, _ in sc2.log) if t >= 5.0)
    first_out = next(a + d for a, d, o in sc2.log if o)
    assert first_out - 5.0 <= 2 * 0.012 and first_out >= first_in
    load = T.ScanCycle("plc3", T.ScanProfile(spike_p=0.2), seed=3)
    load.run(10.0, lambda t: 0, lambda x: x)
    assert max(d for _, d, _ in load.log) > 0.012 and load.state == "run"
    stop = T.ScanCycle("plc4", T.ScanProfile(spike_p=0.01, spike_min_s=0.2, spike_max_s=0.3), seed=4)
    stop.run(100.0, lambda t: 1, lambda x: x)
    assert stop.state == "stop" and stop.overruns == 1 and stop.t < 100.0


def test_device_rule():
    assert device_of("incomer-1.v_min_v") == ("incomer-1-pqa", "pqa")
    assert device_of("compressor-1.current_a") == ("compressor-1-pmd", "pmd")
    assert device_of("compressor-1.state") == ("compressor-1-plc", "plc")
    assert device_of("press-1.oil_c") == ("press-1-plc", "plc")


@pytest.fixture(scope="module")
def plants():
    """The same plant with and without the device layer, 10 min at 0.1 s, with an outage of the plant
    Ethernet from 300 to 360 s and a scan spike share that stops one PLC."""
    cfg = DeviceConfig(outages={"ethernet": [(300.0, 360.0)]}, keep_log=True)
    a, b = UtilitiesPlant(seed=3), UtilitiesPlant(seed=3, devices=cfg)
    ta, tb = [], []
    for k in range(6000):
        a.step(0.1)
        b.step(0.1)
        if k % 10 == 9:
            ta.append(repr(a.tags()))
            tb.append(repr(b.tags()))
            if k == 3299:                                               # inside the outage
                b.mid = dict(b.devices.readings)
    return ta, tb, b


def test_device_layer_leaves_the_plant_as_it_was(plants):
    ta, tb, _ = plants
    assert ta == tb


def test_scada_view_stamps_quality_and_the_ser(plants):
    """Meters and PLCs are stamped at the poll on the SCADA clock, the Class A analyzer on its own
    clock (PTP); the Ethernet outage gives the PLC and analyzer readings quality bad with the last good
    value, the meters on RS-485 stay good, and after it everything is good again; the SER logs the
    compressor's state changes to 1 ms."""
    _, _, b = plants
    d = b.devices
    mid, end = b.mid, d.readings
    assert mid["compressor-1.state"][2] == "bad" and mid["incomer-1.p_kw"][2] == "bad"
    assert mid["compressor-1.current_a"][2] == "good"                  # its meter is on RS-485
    assert all(q == "good" for _, _, q in end.values())
    stamp = end["compressor-1.current_a"][1]
    assert stamp == end["compressor-1.state"][1] and abs(stamp - 600.0) < 0.01
    assert end["incomer-1.p_kw"][1] != stamp and abs(end["incomer-1.p_kw"][1] - 600.0) < 1.0
    comp = [e for e in d.events if e[1] == "compressor-1.state"]
    assert comp and all(round(e[0], 3) == e[0] for e in comp) and comp[0][2] == "stopped"
    assert len(d.polls) == 600


def test_switches_off_give_true_time_and_no_loss():
    """With clocks and links off, every stamp is the true poll time and every reading is good."""
    p = UtilitiesPlant(seed=4, devices=DeviceConfig(clocks=False, links=False, scan=False))
    for _ in range(50):
        p.step(0.1)
    r = p.devices.readings
    assert all(q == "good" for _, _, q in r.values())
    poll = p.devices._next_poll - 1.0
    assert {round(s, 9) for t, (_, s, _) in r.items() if not t.startswith("incomer-1.")} == {round(poll, 9)}
    pqa = {s for t, (_, s, _) in r.items() if t.startswith("incomer-1.")}
    assert len(pqa) == 1 and 0.0 <= poll - pqa.pop() < 0.2 + 1e-9      # its last 200 ms interval, on true time
