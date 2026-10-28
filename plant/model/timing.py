"""Device time, network links and the PLC scan (task 23, 2026-10-28).

A real plant has no single clock. Each device that stamps data keeps its own time on a quartz crystal,
which runs fast or slow by some parts per million (ppm) and more so away from 25 C. A sync client (NTP,
PTP or IRIG-B) pulls the clock back to the reference at each sync message that gets through. A device
with no sync drifts. Data and sync messages travel over links with a delay, jitter, losses, a bandwidth
and outages; an outage stops both. A PLC runs its program in scans: it reads its inputs once at the
start of a scan, runs the logic and writes its outputs at the end.

These are models only. `devices.py` puts them on the plant's devices, behind a switch that is off by
default (`UtilitiesPlant(devices=...)`).

| Item | Value | Source |
|---|---|---|
| Quartz crystal at 25 C | +-20 ppm, about 1.7 s per day | Silicon Labs, RTC drift management (32.768 kHz crystal); Schneider Electric FAQ FA12099 (a PLC's quartz) |
| Crystal over 0 to 70 C | -101 to +20 ppm (-8.7 to +1.7 s per day) | Schneider Electric FAQ FA12099. As a parabola about 25 C: -0.04 ppm/K^2 (the -101 ppm at 70 C less the -20 ppm tolerance, over 45 K squared) |
| NTP | millisecond range; sub-millisecond on a LAN at best; poll 64 s (minpoll 6) to 1024 s (maxpoll 10) | OMICRON (Baumgartner, Riesch, Rudigier, IMEKO TC4 2014); Wikipedia "Network Time Protocol" (RFC 5905); ntp.conf defaults as quoted by Red Hat |
| PTP, power profile | 1 us across up to 16 switches; sync, announce and peer delay once per second | IEEE C37.238, as summarized by Trystar/Cyber Sciences and Nokia; the research check of 2026-10-15: under 100 us with hardware stamps |
| IRIG-B | many devices reach 1 ms | OMICRON paper on IEEE 1588 (research check of 2026-10-15) |
| Modbus RTU | 11 bits per character; 3.5 character times between frames (2.0 ms at 19,200 baud) | Modbus over Serial Line v1.02, as summarized by Industrial Monitor Direct |
| PROFINET RT | send clock 1 ms times a reduction ratio (for example 4 ms); a drive at 4 ms | ABB PLC help (PROFINET IO device); Nanotec PROFINET drive manual |
| PLC scan | 8 to 12 ms for a modest program; a scan of 7 ms grew to 12 to 18 ms under online polling | research check of 2026-10-15; Industrial Monitor Direct (S7-1500 scan time spikes, a secondary source) |
| PLC watchdog | maximum cycle time 150 ms (the CPU goes to STOP past it) | S7-1500 default as given by Industrial Monitor Direct (secondary; to confirm in the Siemens manual) |

Project choices: the shape of the scan-time spread (uniform), the spike rate and size, the residual
of a sync as a normal draw, the link delays, jitters and losses of the profiles (`LINKS`).
"""
import math
import random
from dataclasses import dataclass, field

PPM = 1e-6
XTAL_TOL_PPM = 20.0          # tolerance at 25 C (Silicon Labs; Schneider FA12099)
XTAL_PARABOLA = 0.04         # ppm / K^2 below the turnover (Schneider FA12099: -101 ppm at 70 C)
XTAL_TURNOVER_C = 25.0


# --------------------------------------------------------------------------------- sync --
@dataclass(frozen=True)
class SyncProfile:
    """A time sync method: the client gets a sync every `interval_s`, and after it the clock is off
    the reference by a normal draw of 1 sigma `residual_s`."""
    name: str
    interval_s: float
    residual_s: float
    source: str = ""


NTP = SyncProfile("ntp", 64.0, 1e-3, "NTP: millisecond range (OMICRON); minpoll 64 s (ntp.conf)")
PTP = SyncProfile("ptp", 1.0, 1e-6, "IEEE C37.238 power profile: 1 us, sync once per second")
IRIG_B = SyncProfile("irig-b", 1.0, 1e-3, "IRIG-B: many devices reach 1 ms (OMICRON)")
SYNCS = {s.name: s for s in (NTP, PTP, IRIG_B)}


# -------------------------------------------------------------------------------- links --
@dataclass(frozen=True)
class LinkProfile:
    """A network link: a fixed delay, a jitter (the mean of an exponential extra delay, as in a
    queue), a loss share per message, a bandwidth (bit/s) and the bits each byte takes on the wire."""
    name: str
    delay_s: float
    jitter_s: float
    loss: float
    bandwidth_bps: float
    bits_per_byte: float = 8.0
    source: str = ""

    def wire_s(self, n_bytes):
        return n_bytes * self.bits_per_byte / self.bandwidth_bps


LINKS = {
    # a feeder meter on RS-485: 19,200 baud, 11 bits per character (Modbus serial line), the
    # 3.5-character gap and a meter answer time of 20 ms (project choice)
    "modbus-rtu": LinkProfile("modbus-rtu", 0.020 + 3.5 * 11.0 / 19200.0, 0.002, 0.0, 19200.0, 11.0,
                              "Modbus over Serial Line v1.02; answer time project choice"),
    # the plant LAN: 100 Mbit/s switched Ethernet, 0.5 ms through the stack and the switches, 0.1 ms
    # jitter (project choices)
    "ethernet": LinkProfile("ethernet", 0.5e-3, 0.1e-3, 0.0, 100e6, 8.0, "project choice"),
    # drive data to the PLC over PROFINET RT at a 4 ms update (Nanotec; ABB send clock x reduction)
    "profinet-rt": LinkProfile("profinet-rt", 4e-3, 0.2e-3, 0.0, 100e6, 8.0, "Nanotec PROFINET manual: 4 ms"),
}


class Link:
    """One link with its own random stream. `send(t, n_bytes)` gives the arrival time of a message
    sent at t, or None when it is lost or the link is out. `outages`: [(start, end)] in true time."""

    def __init__(self, profile, seed=0, name="", outages=()):
        self.p = profile
        self.rng = random.Random(f"link:{name}:{seed}")
        self.outages = [tuple(o) for o in outages]
        self.sent = self.lost = 0

    def up(self, t):
        return not any(a <= t < b for a, b in self.outages)

    def send(self, t, n_bytes=64):
        self.sent += 1
        if not self.up(t):
            self.lost += 1
            return None
        p = self.p
        # draw both numbers every time, so a loss does not shift the stream of the delays
        u, e = self.rng.random(), self.rng.expovariate(1.0 / p.jitter_s) if p.jitter_s > 0.0 else 0.0
        if u < p.loss:
            self.lost += 1
            return None
        arrive = t + p.delay_s + p.wire_s(n_bytes) + e
        if not self.up(arrive):                        # the link went down while it travelled
            self.lost += 1
            return None
        return arrive


# -------------------------------------------------------------------------------- clock --
class Clock:
    """A device clock on a quartz crystal. Its offset from true time grows with the drift: the
    crystal's error at 25 C (a draw inside the tolerance) and the parabola below the turnover with
    the device temperature. A sync (`SyncClient`) sets the offset back to a small residual. Each time
    stamp adds the clock's read jitter.

    offset_s, drift_ppm: the start offset and the error at 25 C (None: drawn inside the tolerance).
    t_c: a function of true time that gives the device temperature (None: 25 C)."""

    def __init__(self, name, seed=0, offset_s=None, drift_ppm=None, jitter_s=0.0, t_c=None):
        self.name = name
        self.rng = random.Random(f"clock:{name}:{seed}")
        self.drift25_ppm = self.rng.uniform(-XTAL_TOL_PPM, XTAL_TOL_PPM) if drift_ppm is None else drift_ppm
        self.offset = self.rng.uniform(-1.0, 1.0) if offset_s is None else offset_s   # set by hand once (project choice)
        self.jitter_s = jitter_s
        self.t_c = t_c
        self.t = 0.0                                   # true time of the last advance

    def drift_ppm(self, t_c=None):
        t = XTAL_TURNOVER_C if t_c is None else t_c
        return self.drift25_ppm - XTAL_PARABOLA * (t - XTAL_TURNOVER_C) ** 2

    def advance(self, t):
        """Run the clock to true time t (the drift at the temperature of the interval's start)."""
        dt = t - self.t
        if dt > 0.0:
            self.offset += self.drift_ppm(self.t_c(self.t) if self.t_c else None) * PPM * dt
            self.t = t

    def now(self, t):
        """The device's time stamp at true time t."""
        self.advance(t)
        j = self.rng.gauss(0.0, self.jitter_s) if self.jitter_s > 0.0 else 0.0
        return t + self.offset + j

    def set_offset(self, offset):
        self.offset = offset


class SyncClient:
    """The sync client of one device. A sync message leaves the reference every interval; when it
    arrives (the link is up and it is not lost), the clock steps to a residual offset. With no link,
    every sync arrives."""

    def __init__(self, clock, profile, link=None, seed=0):
        self.clock, self.p, self.link = clock, profile, link
        self.rng = random.Random(f"sync:{clock.name}:{seed}")
        self.next_t = 0.0
        self.pending = []                              # arrival times of syncs on their way
        self.synced = self.missed = 0
        self.last_sync = None                          # true time of the last sync that got through

    def step(self, t):
        """Send every sync due up to true time t, and apply each one that has arrived by t."""
        while self.next_t <= t:
            ts = self.next_t
            self.next_t += self.p.interval_s
            arrive = ts if self.link is None else self.link.send(ts, 90)   # a PTP or NTP packet, about 90 bytes
            if arrive is None:
                self.missed += 1
            else:
                self.pending.append(arrive)
        self.pending.sort()
        while self.pending and self.pending[0] <= t:
            self._apply(self.pending.pop(0))

    def _apply(self, t):
        self.clock.advance(t)
        self.clock.set_offset(self.rng.gauss(0.0, self.p.residual_s))
        self.synced += 1
        self.last_sync = t


# ---------------------------------------------------------------------------- PLC scan --
@dataclass(frozen=True)
class ScanProfile:
    """A PLC's cyclic program: each scan takes a time uniform in [scan_min_s, scan_max_s]; under
    communication load a scan grows by a spike (with `spike_p` per scan, uniform in [spike_min_s,
    spike_max_s]); past `watchdog_s` the CPU goes to STOP."""
    scan_min_s: float = 8e-3
    scan_max_s: float = 12e-3
    spike_p: float = 0.0
    spike_min_s: float = 5e-3
    spike_max_s: float = 10e-3
    watchdog_s: float = 0.150
    source: str = ("research check 2026-10-15 (8 to 12 ms); Industrial Monitor Direct (7 ms to 12-18 ms under "
                   "polling; watchdog 150 ms, S7-1500 default)")


class ScanCycle:
    """The scan of one PLC in true time. `run(t_end, read, logic)` runs every scan that starts before
    t_end: `read(t)` gives the inputs at the scan start (the process image, read once per scan),
    `logic(inputs)` gives the outputs, which take effect at the end of the scan. A scan longer than
    the watchdog stops the CPU (state "stop"): no more scans, the outputs keep their last values (a
    real CPU sets its outputs to their substitute values; project choice to keep them).
    `comm_load`: a function of true time, 0 or 1, that turns the spikes on."""

    def __init__(self, name, profile=ScanProfile(), seed=0, comm_load=None):
        self.name, self.p = name, profile
        self.rng = random.Random(f"scan:{name}:{seed}")
        self.comm_load = comm_load
        self.t = 0.0                                   # start of the next scan
        self.state = "run"
        self.outputs = None
        self.log = []                                  # (scan start, scan time, outputs at the end)
        self.overruns = 0

    def scan_time(self, t):
        p = self.p
        s = self.rng.uniform(p.scan_min_s, p.scan_max_s)
        spike = self.rng.random() < p.spike_p if (self.comm_load is None or self.comm_load(t)) else False
        x = self.rng.uniform(p.spike_min_s, p.spike_max_s)          # drawn always: a stable stream
        return s + (x if spike else 0.0)

    def run(self, t_end, read, logic, keep_log=True):
        while self.state == "run" and self.t < t_end:
            t0 = self.t
            inputs = read(t0)
            d = self.scan_time(t0)
            if d > self.p.watchdog_s:
                self.overruns += 1
                self.state = "stop"
                self.t = t0 + self.p.watchdog_s
                if keep_log:
                    self.log.append((t0, d, self.outputs))
                break
            self.outputs = logic(inputs)
            self.t = t0 + d
            if keep_log:
                self.log.append((t0, d, self.outputs))
        return self.outputs
