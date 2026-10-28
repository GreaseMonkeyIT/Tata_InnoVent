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
from dataclasses import dataclass

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
    # the three-phase tags (unbalance.py, 2026-10-28): the Class A analyzer's voltage unbalance
    # (+-0.15 points over 0.5 to 5 %, IEC 61000-4-30 Class A as Schneider Electric's ION note gives it),
    # phase angles to 1 degree (project choice)
    if unit == "unbalance_pct":
        return 0.15, 0.01
    if unit.endswith("_deg"):
        return 1.0, 0.1
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
        import numpy as np
        vals = list(values)
        num = _numbers(vals)
        if not num.any():
            return vals
        x = np.array([float(v) if ok else 0.0 for v, ok in zip(vals, num)])
        z = np.random.default_rng(_noise_seed(run_seed, tag)).normal(0.0, 1.0, len(x)) if self.noise else None
        y = _readings(x, z, *self.constants(tag))
        return [float(a) if ok else b for a, b, ok in zip(y.tolist(), vals, num)]

    def constants(self, tag):
        """The constants of a tag's instrument for `_readings`: (temperature, low end, accuracy of a
        reading of 0, accuracy of a reading of 1, bias share, resolution)."""
        rng_ = self.ranges.get(tag)
        a0, _ = _accuracy(tag, 0.0, None)
        a1, _ = _accuracy(tag, 1.0, None)
        _, res = _accuracy(tag, 1.0, rng_)
        low = 0.0 if rng_ is None else LOW_END * rng_       # max(|x|, 0.0) is |x|
        return tag.rsplit(".", 1)[-1].endswith("_c"), low, a0, a1, self._bias(tag), res

    def stream(self, tags, run_seed):
        """A reader of `tags` one sample at a time, equal to `read_column` over the run (Stream)."""
        return Stream(self, tags, run_seed)


def _numbers(vals):
    """A boolean array: where a value is a finite number (not a bool)."""
    import numpy as np
    return np.array([isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vals],
                    bool)


def _noise_seed(run_seed, tag):
    import hashlib
    return int.from_bytes(hashlib.sha256(f"noise:{run_seed}:{tag}".encode()).digest()[:8], "little")


def _readings(x, z, temp, low, a0, a1, bias, res):
    """The readings of the numbers `x` with the standard normal draws `z` (None: no noise). The
    constants are of one instrument (a column) or one per element (a sample of many instruments).
    Each element is computed alone, so both give the same reading, bit for bit."""
    import numpy as np
    v = np.maximum(np.abs(x), low)
    acc = np.where(temp, np.where(v <= 150.0, 0.15 + 0.002 * v, np.maximum(1.5, 0.004 * v)),
                   a0 + (a1 - a0) * v)                        # every non-temperature rule is linear in v
    y = x + bias * acc
    if z is not None:
        y = y + z * (acc / 5.0)
    return np.round(y / res) * res


class Stream:
    """The readings of a run one sample at a time (2026-10-21, for the live plant service,
    live/plant_service.py). Sample k of a tag equals element k of `read_column` for the same run:
    the same constants, and the same noise stream, drawn in blocks. Give `read` one value per tag,
    in the order of `tags`, for every sample of the run in turn."""

    BLOCK = 4096                                              # noise draws per instrument at a time

    def __init__(self, inst, tags, run_seed):
        import numpy as np
        self.tags = list(tags)
        cols = list(zip(*[inst.constants(t) for t in self.tags])) or [()] * 6
        self._c = [np.array(c, bool if i == 0 else float) for i, c in enumerate(cols)]
        self._gens = [np.random.default_rng(_noise_seed(run_seed, t)) for t in self.tags] if inst.noise else None
        self._z, self._k = None, self.BLOCK

    def read(self, values):
        import numpy as np
        if not self.tags:
            return list(values)
        if self._gens is not None:                            # one draw per instrument and sample, number or not
            if self._k == self.BLOCK:
                self._z, self._k = np.array([g.normal(0.0, 1.0, self.BLOCK) for g in self._gens]), 0
            z = self._z[:, self._k]
            self._k += 1
        else:
            z = None
        vals = list(values)
        num = _numbers(vals)
        if not num.any():
            return vals
        x = np.array([float(v) if ok else 0.0 for v, ok in zip(vals, num)])
        y = _readings(x, z, *self._c)
        return [float(a) if ok else b for a, b, ok in zip(y.tolist(), vals, num)]


# ------------------------------------------------------------------------- the front end --
# The rest of an instrument (task 24, 2026-10-28): what happens to a reading between the sensing element
# and the SCADA. Off by default: `read_column` and `Stream` give the readings of version 0, and nothing
# here runs unless a front end is asked for (`front_end`, `default_front_end`). The failure modes are
# instrument settings, not plant faults: they are not in the fault registry (faults.py).
#
# | Item | Default when on | Source |
# |---|---|---|
# | damping of a temperature transmitter | 5 s, first order | Rosemount 644 configuration data sheet (5 s default) |
# | damping of a pressure transmitter | 0.4 s | Rosemount 3051 configuration data sheet (0.40 s default) |
# | damping of a magnetic flowmeter | 2.0 s | Rosemount 8700 configuration data sheet (its example value; project choice) |
# | refresh of a feeder meter's registers | 1 s | research check of 2026-10-15 (a project choice) |
# | deadband | none (absolute or a share of the range when set) | Kepware OPC UA client: none, percent or absolute |
# | maximum time between records | none (when set: a record at least this often) | Ignition tag history |
# | quality | Good; Uncertain_EngineeringUnitsExceeded over 120 % of the range; Bad_SensorFailure in a dropout | OPC UA Part 8 (Data Access); IEC 61869-2 (a CT to 120 % of rated current) |
#
# Project choices: the averaging window as a trailing mean of whole samples (a meter's own 200 ms
# averaging is under the 1 s sample), the order of the steps below, a stuck reading that keeps the last
# good one and stays Good (the instrument does not know).
GOOD, UNCERTAIN_EGU, BAD_SENSOR = "Good", "Uncertain_EngineeringUnitsExceeded", "Bad_SensorFailure"
EGU_LIMIT = 1.2


@dataclass(frozen=True)
class FrontEnd:
    """The settings of one instrument's front end. Times in seconds of the run (0 = the first sample).
    drift_per_h: the reading drifts by this much per hour from drift_from_s (failure). stuck: (start,
    end): the reading freezes at its last value (failure). dropout: (start, end): no reading, quality
    bad (failure). range_hi: the instrument's range (the 120 % limit of the quality)."""
    damping_s: float = 0.0
    avg_s: float = 0.0
    refresh_s: float = 0.0
    deadband: float = 0.0
    deadband_pct: float = 0.0
    max_hold_s: float = 0.0
    drift_per_h: float = 0.0
    drift_from_s: float = 0.0
    stuck: tuple = ()
    dropout: tuple = ()
    range_hi: float = None


def default_front_end(tag, range_hi=None):
    """The front end of a tag's instrument by the rule of `_accuracy` (the class of the quantity)."""
    unit = tag.rsplit(".", 1)[-1]
    if unit.endswith("_c"):
        return FrontEnd(damping_s=5.0, range_hi=range_hi)
    if any(unit.endswith("_" + s) or unit == s for s in SPAN):
        return FrontEnd(damping_s=0.4, range_hi=range_hi)
    if unit.endswith(("_m3h", "_kgs")):
        return FrontEnd(damping_s=2.0, range_hi=range_hi)
    if unit.endswith(("_a", "_v", "_kw", "_w", "_kvar")) or unit in ("p_kw", "q_kvar", "pf"):
        return FrontEnd(refresh_s=1.0, range_hi=range_hi)
    return FrontEnd(range_hi=range_hi)


def _inside(t, span):
    return bool(span) and span[0] <= t < span[1]


def front_end(values, spec, sample_s=1.0):
    """A column of readings (from `read_column`) through a front end: (values, quality), one per sample.
    In order: the failures (drift, stuck, dropout), the damping and the averaging, the register refresh,
    the deadband with the maximum time between records, and the quality. A value that is not a finite
    number passes through (quality Good: a state or a command is digital)."""
    import numpy as np
    vals = list(values)
    num = _numbers(vals)
    out, qual = [], []
    damp = 1.0 - math.exp(-sample_s / spec.damping_s) if spec.damping_s > 0.0 else 1.0
    n_avg = max(1, int(round(spec.avg_s / sample_s))) if spec.avg_s > 0.0 else 1
    window = []
    y = None                  # the damped value
    held = None               # the value of the stuck sensor
    reg, reg_t = None, None   # the register and when it was refreshed
    rep, rep_t = None, None   # the last reported value and when
    db = spec.deadband + (spec.deadband_pct / 100.0 * spec.range_hi if spec.deadband_pct and spec.range_hi else 0.0)
    for k, (v, ok) in enumerate(zip(vals, num)):
        t = k * sample_s
        if not ok:
            out.append(v)
            qual.append(GOOD)
            continue
        if _inside(t, spec.dropout):
            out.append(None)
            qual.append(BAD_SENSOR)
            continue
        x = float(v)
        if spec.drift_per_h and t >= spec.drift_from_s:
            x += spec.drift_per_h * (t - spec.drift_from_s) / 3600.0
        if _inside(t, spec.stuck):
            held = x if held is None else held
            x = held
        else:
            held = None
        y = x if y is None else y + damp * (x - y)
        window = (window + [y])[-n_avg:]
        a = float(np.mean(window)) if n_avg > 1 else y
        if spec.refresh_s > 0.0 and reg_t is not None and t - reg_t < spec.refresh_s - 1e-9:
            a = reg
        else:
            reg, reg_t = a, t
        if db > 0.0 and rep is not None and abs(a - rep) <= db and not (
                spec.max_hold_s and t - rep_t >= spec.max_hold_s - 1e-9):
            a = rep
        else:
            rep, rep_t = a, t
        out.append(a)
        qual.append(UNCERTAIN_EGU if (spec.range_hi and abs(a) > EGU_LIMIT * spec.range_hi) else GOOD)
    return out, qual
