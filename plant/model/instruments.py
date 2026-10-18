"""Virtual instruments, version 0 (ideas.md 17.2 and 17.3; 2026-10-16).

The engine must read instruments, never the sim state. A real instrument reads with a calibration
error (a bias inside its accuracy class), noise and a resolution. Before this layer every signal was
exact, so a residual spread could fall to zero and any tiny model error looked like a fault.

The instrument of a tag follows from the quantity it measures (one rule, by the tag's unit):

| Quantity | Instrument | Accuracy (the bias stays inside it) | Source |
|---|---|---|---|
| temperature up to 150 C | Pt100 RTD, class A | +-(0.15 + 0.002 abs(t)) C | IEC 60751 |
| temperature above 150 C | type K thermocouple, class 1 | +-1.5 C or 0.004 abs(t), the larger | IEC 60584-1 |
| current, voltage, power, kVAr | power metering device, class 0.5 | +-0.5 % of reading | IEC 61557-12 class 0.5 (read as 0.5 % of reading: to confirm against the standard's table) |
| pressure | pressure transmitter | +-0.075 % of span | Delval PTI40 datasheet |
| flow | magnetic flowmeter | +-0.5 % of reading | ideas.md 17.3 |
| other (speed, torque, force, shares) | read as accuracy 0.5 % of reading | project choice |

Project choices: the noise of each reading is a fifth of its accuracy (1 sigma), the resolution is
0.01 of the unit (0.1 C for temperatures), the spans of the pressure transmitters (in SPAN below).

The calibration bias of each instrument is drawn once per FACTORY (`factory_seed`), not per run: the
runs are days of the same plant, so they read through the same devices. Noise is drawn per reading.
PLC states and commands are digital and exact.
"""
import math
import random

SPAN = {"bar": 400.0, "barg": 10.0, "kpa": 100.0}     # pressure transmitter spans (project choice)


LOW_END = 0.05      # under 5 % of its range a %-of-reading instrument keeps its error at that point


def _accuracy(tag, value, rng=None):
    """(accuracy as a 1-sided bound, resolution) for a tag's reading; `rng` the instrument's range."""
    unit = tag.rsplit(".", 1)[-1]
    v = abs(value) if rng is None else max(abs(value), LOW_END * rng)
    if unit.endswith("_c"):
        if v <= 150.0:
            return 0.15 + 0.002 * v, 0.1                      # Pt100 class A
        return max(1.5, 0.004 * v), 0.1                       # type K class 1
    if unit.endswith(("_a", "_v", "_kw", "_w", "_kvar")) or unit in ("p_kw", "q_kvar"):
        return 0.005 * v, 0.01                                # PMD class 0.5
    for suf, span in SPAN.items():
        if unit.endswith("_" + suf) or unit == suf:
            return 0.00075 * span, 0.01                       # pressure transmitter
    if unit.endswith(("_m3h", "_kgs")):
        return 0.005 * v, 0.01                                # magnetic flowmeter
    return 0.005 * v, 0.01


class Instruments:
    def __init__(self, factory_seed=0, noise=True, ranges=None):
        self.factory_seed = factory_seed
        self.noise = noise
        self.ranges = ranges or {}
        self.bias = {}                                        # tag -> bias share of the accuracy (-1..1)
        self.rng = random.Random()

    def seed_noise(self, run_seed):
        self.rng = random.Random(f"noise:{run_seed}")

    def _bias(self, tag):
        b = self.bias.get(tag)
        if b is None:
            b = random.Random(f"cal:{self.factory_seed}:{tag}").uniform(-1.0, 1.0)
            self.bias[tag] = b
        return b

    def read(self, tag, value):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return value
        acc, res = _accuracy(tag, value, self.ranges.get(tag))
        x = value + self._bias(tag) * acc
        if self.noise and acc > 0.0:
            x += self.rng.gauss(0.0, acc / 5.0)
        return round(x / res) * res

    def read_all(self, tags, names):
        return {t: self.read(t, tags.get(t)) for t in names}

    def read_column(self, tag, values, run_seed):
        """A whole column of readings of one instrument for a run (2026-10-18): the same bias,
        accuracy and resolution as `read`, with the noise from the instrument's own stream (seeded by
        the run and the tag). So the noise of one instrument does not depend on which other tags are
        read. Values that are not finite numbers pass through unchanged."""
        import hashlib

        import numpy as np
        vals = list(values)
        num = np.array([isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals],
                       bool)
        if not num.any():
            return vals
        x = np.array([float(v) if ok else 0.0 for v, ok in zip(vals, num)])
        rng_ = self.ranges.get(tag)
        v = np.abs(x) if rng_ is None else np.maximum(np.abs(x), LOW_END * rng_)
        unit = tag.rsplit(".", 1)[-1]
        if unit.endswith("_c"):
            acc = np.where(v <= 150.0, 0.15 + 0.002 * v, np.maximum(1.5, 0.004 * v))
            res = 0.1
        else:
            _, res = _accuracy(tag, 1.0, rng_)
            a1, _ = _accuracy(tag, 1.0, None)                 # the accuracy of a reading of 1.0
            a0, _ = _accuracy(tag, 0.0, None)
            acc = a0 + (a1 - a0) * v                          # every non-temperature rule is linear in v
        y = x + self._bias(tag) * acc
        if self.noise:
            seed = int.from_bytes(hashlib.sha256(f"noise:{run_seed}:{tag}".encode()).digest()[:8], "little")
            y = y + np.random.default_rng(seed).normal(0.0, 1.0, len(y)) * (acc / 5.0)
        y = np.round(y / res) * res
        return [float(a) if ok else b for a, b, ok in zip(y.tolist(), vals, num)]
