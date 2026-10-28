"""The plant's devices in time (task 23, 2026-10-28): who measures each tag, on which clock, over
which link, and what the SCADA holds after each poll.

`UtilitiesPlant(devices=DeviceConfig())` puts a `DeviceLayer` on the plant. It only reads the plant
(its tags after each step) and draws from its own random streams, so the plant's tags stay the same,
value for value. Off by default (`devices=None`): the plant does not build it.

The devices follow the plant documents (wiring.py) and the instrument sheet (ideas.md 17.3), one rule:

| Device | Tags | Link | Clock and sync | Time stamp |
|---|---|---|---|---|
| `<asset>-pmd`, a power metering device on the feeder (IEC 61557-12) | the asset's current, voltage, power, kVAr and power factor | Modbus RTU on RS-485 | none (a meter on Modbus has no time stamp) | the SCADA's clock at the poll |
| `<asset>-plc`, the machine's PLC | every other tag of the asset (states, process values) | plant Ethernet | NTP | the SCADA's clock at the poll |
| `incomer-1-pqa`, the Class A analyzer (IEC 61000-4-30) | `incomer-1.*` | plant Ethernet | PTP (IEEE C37.238) | its own clock, at the measurement |
| `ser-1`, a sequence of events recorder | the state tags of the documents (text states, on/off outputs): every change | plant Ethernet | PTP | its own clock, 1 ms resolution |
| `scada` | polls every device every `poll_s` | plant Ethernet | NTP | - |

Values in the research check of 2026-10-15: PLC values read by SCADA every 1 s, stamped at the poll; a
feeder meter's register refresh of 1 s (a project choice); the Class A analyzer with PTP sync; an SER
with 1 ms resolution on a GPS-referenced clock (Cyber Sciences CyTime: PTP or IRIG-B sync, 1 ms). The
PLC scan of `timing.ScanCycle` sets when a PLC's image was last written.

The plant steps every 0.1 s, so the layer sees the plant at the end of each step: a scan or a meter
refresh between two steps reads the state of the step before it, and an SER stamps a change at the
step in which the plant made it (the plant's own resolution). Project choices: the device rule above,
the device temperatures (the shop air for the PLCs and meters in the bays, 25 C in the air-conditioned
electrical and control rooms), the meters' refresh phases, the request and response sizes.
"""
import math
import random
from dataclasses import dataclass, field

from . import timing as T

ELECTRICAL = ("_a", "_v", "_kw", "_kvar")


@dataclass
class DeviceConfig:
    """The switches of the device layer, each on when the layer is on. clocks: device clocks drift and
    sync (off: every clock is true time); links: links delay and lose (off: instant, lossless); scan:
    PLC scans (off: a PLC answers with the plant's value of the last step). sync: device kind -> sync
    profile name (timing.SYNCS) or None. outages: device name or link kind ("modbus-rtu", "ethernet")
    -> [(start, end)] in plant seconds; an outage stops the data and the sync of the devices behind it.
    spike_p: the share of PLC scans that grow under communication load."""
    clocks: bool = True
    links: bool = True
    scan: bool = True
    poll_s: float = 1.0
    meter_refresh_s: float = 1.0           # a feeder meter's registers (research check: 1 s, a project choice)
    pqa_refresh_s: float = 0.2             # the Class A analyzer's basic interval: 10 cycles at 50 Hz (IEC 61000-4-30)
    sync: dict = field(default_factory=lambda: {"plc": "ntp", "pqa": "ptp", "ser": "ptp", "scada": "ntp",
                                                "pmd": None})
    outages: dict = field(default_factory=dict)
    scan_profile: T.ScanProfile = T.ScanProfile()
    spike_p: float = 0.0
    seed: int = 0
    keep_log: bool = False


def device_of(tag):
    """(device name, kind) of a plant tag, by the rule of the module docstring."""
    asset, q = tag.split(".", 1)
    if asset == "incomer-1":
        return "incomer-1-pqa", "pqa"
    if q.endswith(ELECTRICAL) or q == "pf":
        return f"{asset}-pmd", "pmd"
    return f"{asset}-plc", "plc"


def state_tags(w):
    """The tags an SER watches: the commands of the documents that are states or on/off outputs (the
    status contacts of the contactors, valves, heaters and fans)."""
    return sorted({t for d in w.values() for t in d["commands"]})


class Device:
    def __init__(self, name, kind, cfg, t_c, link_kind, seed):
        self.name, self.kind, self.tags = name, kind, []
        self.clock = T.Clock(name, seed=seed, t_c=t_c) if cfg.clocks else None
        outages = list(cfg.outages.get(name, ())) + list(cfg.outages.get(link_kind, ()))
        self.link = T.Link(T.LINKS[link_kind], seed=seed, name=name, outages=outages) if cfg.links else None
        self.link_kind = link_kind
        prof = cfg.sync.get(kind)
        self.sync = (T.SyncClient(self.clock, T.SYNCS[prof], self.link, seed=seed)
                     if (cfg.clocks and prof) else None)
        self.scan = (T.ScanCycle(name, cfg.scan_profile if not cfg.spike_p else
                                 T.ScanProfile(**{**cfg.scan_profile.__dict__, "spike_p": cfg.spike_p}), seed=seed)
                     if (cfg.scan and kind == "plc") else None)
        self.images = []                     # the last (true time written, {tag: value}) of its scans or refreshes
        # a meter refreshes on its own free-running timer; the analyzer's intervals start on its clock's
        # second (IEC 61000-4-30 aligns them to the time reference)
        self.refresh_s = cfg.pqa_refresh_s if kind == "pqa" else cfg.meter_refresh_s
        self.phase = 0.0 if kind == "pqa" else random.Random(f"refresh:{name}:{seed}").random() * self.refresh_s

    def now(self, t):
        return t if self.clock is None else self.clock.now(t)

    def write(self, t, vals):
        self.images = self.images[-2:] + [(t, vals)]

    def image_at(self, t):
        """The image the device holds at true time t (None before its first one)."""
        got = [im for im in self.images if im[0] <= t]
        return got[-1] if got else None

    def outage(self, t):
        return self.link is not None and not self.link.up(t)


class DeviceLayer:
    """The devices of a plant and the SCADA's view of them. `step()` after each plant step; the
    readings after the last poll in `readings` ({tag: (value, stamp, quality)}), the SER records in
    `events` ([(stamp, tag, old, new)]). quality: "good"; "bad" when no answer came (the value is then
    the last good one, as a SCADA holds it); "uncertain" from a PLC whose CPU stopped on its watchdog."""

    def __init__(self, plant, cfg=None):
        from .wiring import wiring
        self.p, self.cfg = plant, cfg or DeviceConfig()
        cfg = self.cfg
        shop = lambda t: plant.t_shop                       # noqa: E731  the bays
        room = None                                         # 25 C: the air-conditioned rooms
        tags = plant.tags()
        w = wiring(plant)
        self.devices = {}
        for t in sorted(tags):
            name, kind = device_of(t)
            if name not in self.devices:
                link = "modbus-rtu" if kind == "pmd" else "ethernet"
                self.devices[name] = Device(name, kind, cfg, room if kind == "pqa" else shop, link, cfg.seed)
            self.devices[name].tags.append(t)
        self.scada = Device("scada", "scada", cfg, room, "ethernet", cfg.seed)
        self.ser = Device("ser-1", "ser", cfg, room, "ethernet", cfg.seed)
        self.ser.tags = [t for t in state_tags(w) if t in tags]
        self.readings = {}
        self.events = []
        self.polls = []                                     # with keep_log: (true time, {device: (sent, back)})
        self._prev = tags                                   # the plant's tags at the end of the last step
        self._prev_t = plant.t
        self._ser_last = {t: tags.get(t) for t in self.ser.tags}
        self._next_poll = plant.t + cfg.poll_s
        self._next_refresh = {n: plant.t + d.phase for n, d in self.devices.items() if d.kind != "plc"}

    def _clocks(self, t):
        for d in list(self.devices.values()) + [self.scada, self.ser]:
            if d.sync is not None:
                d.sync.step(t)

    def step(self, dt):
        p, cfg = self.p, self.cfg
        t0, t1 = self._prev_t, p.t
        prev = self._prev
        # scans and refreshes that start in [t0, t1) read the plant as it was at t0
        for name, d in self.devices.items():
            if d.kind == "plc":
                if d.scan is not None:
                    if d.scan.state == "run":
                        vals = {x: prev.get(x) for x in d.tags}
                        d.scan.run(t1, lambda t, vals=vals: vals, lambda inputs: inputs, keep_log=False)
                        d.write(d.scan.t, d.scan.outputs)
                else:
                    d.write(t0, {x: prev.get(x) for x in d.tags})
            else:
                while self._next_refresh[name] < t1:
                    d.write(self._next_refresh[name], {x: prev.get(x) for x in d.tags})
                    self._next_refresh[name] += d.refresh_s
        self._clocks(t1)
        cur = p.tags()
        # the SER: every change of a watched state, stamped on its own clock to 1 ms. It keeps its log
        # through an outage (the SCADA reads it later); the outage only stops its sync
        for t in self.ser.tags:
            v = cur.get(t)
            if v != self._ser_last.get(t):
                self.events.append((round(self.ser.now(t1), 3), t, self._ser_last.get(t), v))
                self._ser_last[t] = v
        self._prev, self._prev_t = cur, t1
        while self._next_poll <= t1:
            self._poll(self._next_poll)
            self._next_poll += cfg.poll_s

    def _poll(self, t):
        """The SCADA polls every device at true time t (stamped on its own clock at the poll)."""
        stamp = self.scada.now(t)
        log = {}
        for name, d in self.devices.items():
            back = None
            if d.link is None:
                at = back = t
            else:
                at = d.link.send(t, 8)                                  # a read request
                if at is not None:
                    back = d.link.send(at, 5 + 2 * 2 * len(d.tags))     # two registers a value
            im = None if back is None else d.image_at(at)
            if im is None:
                for x in d.tags:
                    old = self.readings.get(x)
                    self.readings[x] = (old[0] if old else None, old[1] if old else None, "bad")
            else:
                written, vals = im
                ts = d.now(written) if d.kind == "pqa" else stamp
                # a PLC in STOP (its watchdog) still answers, with the image of its last scan
                q = "uncertain" if (d.scan is not None and d.scan.state == "stop") else "good"
                for x in d.tags:
                    self.readings[x] = (vals.get(x), ts, q)
            if self.cfg.keep_log:
                log[name] = (at, back)
        if self.cfg.keep_log:
            self.polls.append((t, log))
