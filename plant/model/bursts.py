"""Waveform bursts of motor current and voltage (task 24, 2026-10-28).

A condition monitor records a burst of the motor's phase currents and voltages, on a timer or a
trigger, and the analyst reads its spectrum (motor current signature analysis). Here the burst is not
simulated sample by sample: the plant steps every 0.1 s and a motor's electrical state is quasi-static.
So a burst is built on demand from the motor's state when it was recorded (its current and voltage
phasors of each phase, its slip), which is right only while the load holds steady over the burst. The
recorder therefore takes bursts only of motors on a steady load, and drops a burst whose state changed
before its end.

A burst is 10 s at 10 kHz per channel (the research check of 2026-10-15): 0.1 Hz of frequency resolution,
the reciprocal of its length (Reliability Magazine, load effects on ESA/MCSA). It holds:
  - the fundamental of each phase from the three-phase state (unbalance.py when the plant runs with
    three phases, else the balanced positive sequence);
  - the rotor sidebands at (1 - 2s) f and (1 + 2s) f (Iris Power, PCIC 2015, signature analysis for
    on-line motor diagnostics), at `sideband_db` below the fundamental: a motor parameter, -60 dB for a
    healthy cage (project choice, under the -54 dB of the healthiest band of the industry guideline as
    the Fabrico blog gives it, a secondary source); a cage with broken bars sits higher (the same
    guideline: -45 to -35 dB for one or two broken bars). The plant's quasi-static circuit does not
    change with it;
  - the acquisition noise: 1e-4 of the channel's range, 1 sigma (project choice: a 16-bit converter is
    about 3e-5 of its range per bit).

Steady load: compressor-1 while it stays loaded or unloaded. The cool-1 pump, the tower fans and the roof
fans are power loads without a motor model in the plant (PumpDrive, TowerLoads, RoofExtractors), so
they give no burst yet.
"""
import math
import random
from dataclasses import dataclass

import numpy as np

from .unbalance import SQRT3, phases


@dataclass(frozen=True)
class BurstSpec:
    duration_s: float = 10.0
    fs_hz: float = 10000.0
    noise_frac: float = 1e-4            # of the channel's range, 1 sigma (project choice)


def steady_motors(plant):
    """{motor tag prefix: (motor, the bus load of it, a function that says the load is steady)}."""
    out = {}
    comp = plant.comp
    for b in plant.psu.values():
        for ld in b.loads:
            if getattr(ld, "model", None) is comp.motor:
                out["compressor-1"] = (comp.motor, ld, b, lambda: comp.state in ("loaded", "unloaded"),
                                       lambda: comp.state)
    return out


def snapshot(plant, name):
    """The state a burst of motor `name` is built from: the phase phasors of its current and of its bus
    voltage (phase to neutral), the slip, the frequency."""
    motor, ld, bus, _, _ = steady_motors(plant)[name]
    v1 = bus.v
    i1 = (ld.s_va() / (3.0 * v1)).conjugate() if abs(v1) > 1e-6 else 0j
    v2 = i2 = 0j
    if plant.neg is not None and plant.neg.v2:
        v2, i2 = plant.neg.v2[id(bus)], plant.neg.i2_load[id(ld)]
    r = motor.rating
    return {"motor": name, "t": plant.t, "f_hz": plant.grid.f_hz, "slip": motor.slip,
            "v": phases(v1, v2), "i": phases(i1, i2), "sideband_db": getattr(motor, "sideband_db", -60.0),
            "i_range_a": 2.0 * math.sqrt(2.0) * r.i_a, "v_range_v": 1.5 * math.sqrt(2.0) * r.v_ll / SQRT3}


def waveform(snap, spec=BurstSpec(), seed=0):
    """The burst: {"t", "ia", "ib", "ic", "va", "vb", "vc"} as arrays of duration x fs samples."""
    n = int(round(spec.duration_s * spec.fs_hz))
    t = np.arange(n) / spec.fs_hz
    w = 2.0 * math.pi * snap["f_hz"]
    s = snap["slip"]
    k = 10.0 ** (snap["sideband_db"] / 20.0)
    rng = np.random.default_rng(random.Random(f"burst:{snap['motor']}:{snap['t']!r}:{seed}").getrandbits(64))
    out = {"t": t}
    for ph, i, v in zip("abc", snap["i"], snap["v"]):
        a, phi = math.sqrt(2.0) * abs(i), math.atan2(i.imag, i.real)
        x = a * np.cos(w * t + phi)
        x += k * a * (np.cos((1.0 - 2.0 * s) * w * t + phi) + np.cos((1.0 + 2.0 * s) * w * t + phi))
        out[f"i{ph}"] = x + rng.normal(0.0, spec.noise_frac * snap["i_range_a"], n)
        out[f"v{ph}"] = (math.sqrt(2.0) * abs(v) * np.cos(w * t + math.atan2(v.imag, v.real))
                         + rng.normal(0.0, spec.noise_frac * snap["v_range_v"], n))
    return out


class BurstRecorder:
    """Records bursts of the steady-load motors: every `period_s` (a timer) and whenever `trigger(plant)`
    turns true. A burst keeps the snapshot at its start; it is valid when the motor's load state did not
    change before its end. `get(k)` builds burst k's waveform on demand."""

    def __init__(self, plant, motors=None, period_s=None, trigger=None, spec=BurstSpec(), seed=0):
        self.p, self.spec, self.seed = plant, spec, seed
        self.motors = list(motors) if motors is not None else list(steady_motors(plant))
        self.period_s, self.trigger = period_s, trigger
        self.next_t = plant.t + period_s if period_s else None
        self.bursts = []                     # {"snap", "valid": None (recording), True or False}
        self._armed = True

    def step(self):
        p = self.p
        fire = False
        if self.next_t is not None and p.t >= self.next_t:
            fire, self.next_t = True, self.next_t + self.period_s
        if self.trigger is not None:
            on = bool(self.trigger(p))
            fire |= on and self._armed
            self._armed = not on
        motors = steady_motors(p)
        for b in self.bursts:                # close the bursts whose time is up
            if b["valid"] is None:
                _, _, _, steady, state = motors[b["snap"]["motor"]]
                if state() != b["state"] or not steady():
                    b["valid"] = False
                elif p.t >= b["snap"]["t"] + self.spec.duration_s:
                    b["valid"] = True
        if fire:
            for name in self.motors:
                _, _, _, steady, state = motors[name]
                if steady():
                    self.bursts.append({"snap": snapshot(p, name), "state": state(), "valid": None})

    def get(self, k):
        b = self.bursts[k]
        return waveform(b["snap"], self.spec, self.seed) if b["valid"] else None
