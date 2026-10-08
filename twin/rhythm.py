"""Rhythm discovery, version 0 (ideas.md 12.3 step 2): does a signal repeat, with what period, and
where in its cycle is each sample? The engine learns this from the signal alone: nobody tells it
that a press has a bend cycle.

v0 method (simple, a stand-in for the Matrix Profile of the design):
  - period: the first strong peak of the autocorrelation of the standardized signal (lags from
    min_lag to max_lag); when there is none (cycles of varying length smear the peak), the median
    interval between rising crossings, if those intervals are regular; else "no rhythm" (None)
  - cycle starts: the rising crossings of the signal through its midrange, spaced at least half a
    period apart
  - phase: for each sample, the share of the way from its cycle start to the next (0..1), so the
    small model can take the phase as an input, as step 3 asks

Limits of v0: one dominant period per signal; a cycle with a varying length (part handling
by hand) gives a smeared autocorrelation peak, so the period is the mean cycle. The Matrix Profile
(Yeh et al. 2016) replaces it in a later version: it finds repeated shapes (motifs) and odd cycles
(discords) directly.
"""
import numpy as np


def period(x, min_lag=2, max_lag=None, min_corr=0.4):
    """Dominant period in samples, or None. x: 1-D array."""
    x = np.asarray(x, float)
    x = x - x.mean()
    sd = x.std()
    if sd < 1e-12:
        return None
    x = x / sd
    n = len(x)
    max_lag = max_lag or n // 3
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n] / n
    ac = ac / ac[0]
    best = None
    for k in range(max(min_lag, 1) + 1, min(max_lag, n - 2)):
        if ac[k] > ac[k - 1] and ac[k] >= ac[k + 1] and ac[k] >= min_corr:
            best = k
            break
    if best is None:
        return interval_period(x)
    # refine with a parabola through the peak
    y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
    d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) != 0 else 0.0
    return best + d


def interval_period(x, min_cycles=10, max_cv=0.5, min_samples=3):
    """Period from the intervals between rising crossings through the midrange: for cycles whose
    length varies (a press waits for its operator), the autocorrelation peak smears out, but each
    cycle still starts with the same rise. Rhythmic when the intervals are regular (coefficient
    of variation under max_cv) and longer than a few samples. Returns the median interval or None."""
    x = np.asarray(x, float)
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-12:
        return None
    mid = 0.5 * (lo + hi)
    up = np.flatnonzero((x[:-1] < mid) & (x[1:] >= mid)) + 1
    if len(up) < min_cycles + 1:
        return None
    gaps = np.diff(up).astype(float)
    med = float(np.median(gaps))
    if med < min_samples or gaps.std() / gaps.mean() > max_cv:
        return None
    return med


def cycle_starts(x, per):
    """Indices where a cycle starts: rising crossings through the midrange, at least per/2 apart."""
    x = np.asarray(x, float)
    mid = 0.5 * (np.percentile(x, 5) + np.percentile(x, 95))
    up = np.flatnonzero((x[:-1] < mid) & (x[1:] >= mid)) + 1
    out = []
    for i in up:
        if not out or i - out[-1] >= 0.5 * per:
            out.append(int(i))
    return out


def phase(x, per=None):
    """Phase in [0, 1) for every sample (NaN before the first cycle start). Returns (phase, period)."""
    x = np.asarray(x, float)
    per = per or period(x)
    ph = np.full(len(x), np.nan)
    if per is None:
        return ph, None
    starts = cycle_starts(x, per)
    for a, b in zip(starts, starts[1:] + [len(x)]):
        span = (b - a) if b < len(x) else per
        idx = np.arange(a, b)
        ph[idx] = np.clip((idx - a) / span, 0.0, 0.999999)
    return ph, per


class OnlinePhase:
    """Causal phase for live data: seconds since the last cycle start, from past samples only.
    A start is a rising crossing through `mid` (learned from training data), at least `gap_s`
    after the last one. The time since the start goes into a bank of radial basis functions over
    0 .. `span_s` (generic, the same for every node): the small model's phase input."""

    def __init__(self, mid, period_s, n_basis=12, span_factor=2.0, gap_frac=0.5):
        self.mid, self.period = mid, period_s
        self.span = span_factor * period_s
        self.gap = gap_frac * period_s
        self.centers = np.linspace(0.0, self.span, n_basis)
        self.width = self.span / (n_basis - 1)
        self.prev = None
        self.t = 0.0
        self.t_start = None

    @classmethod
    def learn(cls, x, dt, **kw):
        x = np.asarray(x, float)
        per = period(x)
        if per is None:
            return None
        mid = 0.5 * (np.percentile(x, 5) + np.percentile(x, 95))
        return cls(mid, per * dt, **kw)

    def step(self, value, dt):
        self.t += dt
        if self.prev is not None and self.prev < self.mid <= value:
            if self.t_start is None or self.t - self.t_start >= self.gap:
                self.t_start = self.t
        self.prev = value
        since = self.span if self.t_start is None else min(self.t - self.t_start, self.span)
        return np.exp(-0.5 * ((since - self.centers) / self.width) ** 2)

    def run(self, x, dt):
        return np.stack([self.step(v, dt) for v in x])


def segment_durations(x, mid, dt, min_gap_s=0.0):
    """Per cycle: how long the signal stays above `mid` (the working part of the cycle), in s.
    The durations of the parts of a cycle are signals of their own (ideas.md 12.3 step 2): a worn
    pump does not change the pressure of a bend, it stretches the bend."""
    x = np.asarray(x, float)
    above = x >= mid
    out, run, i_start = [], 0, None
    for i, a in enumerate(above):
        if a:
            run += 1
        elif run:
            out.append(run * dt)
            run = 0
    return np.array(out)
