"""Measurement: what a real instrument reports for a true value (ideas.md 11.2 step 7: the tags a
real machine gives, at the real rate).

Each sensor instance draws its own fixed error once, inside its accuracy class (a real instrument
has a fixed calibration error), adds a small noise per reading, rounds to its resolution, and
holds its value between samples. A drift fault adds a slowly growing error.

Accuracy classes:
  - Temperature, Pt100 RTD, IEC 60751 class B: +-(0.3 + 0.005 |t|) C. Class A: +-(0.15 + 0.002 |t|)
    (Senmatic and Reotemp notes on IEC 60751).
  - Current, current transformer class 0.5S (IEC 61869-2): 0.75 % at 5 % of rated current, 0.5 %
    from 20 % to 120 % (Schneider Electric FAQ FA243581). The meter adds its own small error.
  - Pressure transmitter: 0.25 % of span (project choice: a common industrial accuracy).
The fixed error is drawn uniformly over +-1/2 of the class limit (a calibrated instrument sits
inside its class, project choice), the noise is 1/10 of the class limit (project choice).
"""
import random


class Sensor:
    def __init__(self, name, limit_fn, resolution=0.0, period_s=0.0, seed=None):
        """limit_fn(true_value) -> class limit (absolute, in the unit of the value)."""
        self.name, self.limit_fn = name, limit_fn
        self.resolution, self.period_s = resolution, period_s
        self.rng = random.Random(f"sensor:{name}" if seed is None else seed)
        self.bias_frac = self.rng.uniform(-0.5, 0.5)
        self.drift_per_s = 0.0           # fault: a growing error, unit per second
        self.drift = 0.0
        self.stuck = None                # fault: the reading freezes at this value
        self._t = 0.0
        self._next = 0.0
        self.value = None

    def read(self, true_value, dt):
        """Advance dt and return the reported value (held between samples)."""
        self._t += dt
        self.drift += self.drift_per_s * dt
        if self.stuck is not None:
            self.value = self.stuck
            return self.value
        if self.value is None or self._t >= self._next:
            lim = abs(self.limit_fn(true_value))
            v = true_value + self.bias_frac * lim + self.rng.gauss(0.0, 0.1 * lim) + self.drift
            if self.resolution > 0:
                v = round(v / self.resolution) * self.resolution
            self.value = v
            self._next = self._t + self.period_s
        return self.value


def rtd_class_b(t):
    return 0.3 + 0.005 * abs(t)


def rtd_class_a(t):
    return 0.15 + 0.002 * abs(t)


def ct_class_05s(i_rated):
    def lim(i):
        x = abs(i) / i_rated
        pct = 0.75 if x < 0.2 else 0.5
        if x < 0.05:
            pct = 1.5
        return pct / 100.0 * max(abs(i), 0.01 * i_rated)
    return lim


def transmitter(span, pct=0.25):
    return lambda v: pct / 100.0 * span
