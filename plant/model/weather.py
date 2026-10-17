"""Site weather (ideas.md 11.7): dry bulb, humidity, pressure and wet bulb, hour by hour.

Source: NASA POWER hourly point data (MERRA-2 reanalysis, public domain), Jamshedpur 22.80 N 86.18 E,
local solar time, 2025: T2M (C), RH2M (%), PS (kPa). The file stays in the gitignored data/ folder
(fetch: plant/model/README.md, "Weather"). Without the file the plant uses FixedWeather, the design
condition it had before (35 C dry bulb, the 1 % wet bulb 28.1 C), and says so in `source`.

Wet bulb: the psychrometric equations of the ASHRAE Handbook of Fundamentals (2017, ch. 1): the
humidity ratio W = 0.621945 pw / (p - pw), and the wet bulb is the temperature that satisfies
W = ((2501 - 2.326 t*) Ws* - 1.006 (t - t*)) / (2501 + 1.86 t - 4.186 t*), found by bisection.
The saturation pressure is the Magnus form already used by the cooling tower (cooling_tower.p_sat_kpa).

Between the hourly values the weather is linear in time. The plant starts on a day of the year.
The caller can give that day (eval/nodes.py gives each split its own days). Without it, the seed
picks the day, so the seeds cover the seasons.
"""
import csv
import math
import os

from .cooling_tower import p_sat_kpa

DEFAULT_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "weather",
                            "nasa_power_jamshedpur_2025_hourly.csv")


def humidity_ratio(t_c, rh_pct, p_kpa):
    pw = rh_pct / 100.0 * p_sat_kpa(t_c)
    return 0.621945 * pw / (p_kpa - pw)


def wet_bulb(t_c, rh_pct, p_kpa):
    """Thermodynamic wet bulb, C (ASHRAE Fundamentals 2017 ch. 1, eq. 33 above freezing)."""
    w = humidity_ratio(t_c, rh_pct, p_kpa)
    lo, hi = -10.0, t_c
    for _ in range(50):
        ts = 0.5 * (lo + hi)
        pws = p_sat_kpa(ts)
        ws = 0.621945 * pws / (p_kpa - pws)
        w_calc = ((2501.0 - 2.326 * ts) * ws - 1.006 * (t_c - ts)) / (2501.0 + 1.86 * t_c - 4.186 * ts)
        if w_calc > w:
            hi = ts
        else:
            lo = ts
    return 0.5 * (lo + hi)


class FixedWeather:
    source = "fixed design condition (no weather file)"

    def __init__(self, t_db=35.0, t_wb=28.1, rh=None, p_kpa=99.0):
        self.t_db, self.t_wb, self.p_kpa = t_db, t_wb, p_kpa
        self.rh = rh if rh is not None else 50.0

    def at(self, t_s):
        return self


class HourlyWeather:
    """Hourly records; `at(t_s)` gives the weather t_s seconds after the plant start."""

    def __init__(self, path=DEFAULT_FILE, day0=0, hour0=0.0):
        rows = []
        with open(path, newline="") as fh:
            lines = [ln for ln in fh]
        start = next(i for i, ln in enumerate(lines) if ln.startswith("YEAR,"))
        for r in csv.DictReader(lines[start:]):
            t, rh, p = float(r["T2M"]), float(r["RH2M"]), float(r["PS"])
            if min(t, rh, p) <= -999.0:
                continue
            rows.append((t, rh, p))
        if len(rows) < 48:
            raise ValueError(f"{path}: too few weather records")
        self.rows = rows
        self.h0 = (day0 % (len(rows) // 24)) * 24.0 + hour0
        self.source = f"NASA POWER hourly, {os.path.basename(path)}"
        self.t_db = self.rh = self.p_kpa = self.t_wb = None
        self._last = None

    def at(self, t_s):
        h = self.h0 + t_s / 3600.0
        n = len(self.rows)
        i = int(math.floor(h)) % n
        f = h - math.floor(h)
        a, b = self.rows[i], self.rows[(i + 1) % n]
        key = (i, round(f, 3))
        if key != self._last:                    # the wet bulb needs a bisection: once per 3.6 s step
            self._last = key
            self.t_db = a[0] + f * (b[0] - a[0])
            self.rh = a[1] + f * (b[1] - a[1])
            self.p_kpa = a[2] + f * (b[2] - a[2])
            self.t_wb = wet_bulb(self.t_db, self.rh, self.p_kpa)
        return self


def site_weather(seed=0, t_day0_h=8.0, path=None, day0=None):
    """The weather of the site for a plant run: the hourly file if it is there, else the design
    condition. `day0` is the day of the year (0 = 1 January). Without it, the seed picks the day."""
    path = path or os.environ.get("VISR_WEATHER", DEFAULT_FILE)
    if not os.path.exists(path):
        return FixedWeather()
    if day0 is None:
        import random
        day0 = random.Random(f"weather:{seed}").randrange(365)
    return HourlyWeather(path, day0=day0, hour0=t_day0_h)
