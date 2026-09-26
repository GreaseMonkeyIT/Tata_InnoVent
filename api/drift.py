"""The loop drift forecast (LOG-100): a trip card from the rise of the supply water.

When the chiller cannot remove the loop's heat (Scenario 2: a stuck-loaded compressor adds heat,
or Scenario 7: the chiller stopped), the supply water warms steadily and every cooled machine warms
with it. The rise is slow, a fraction of a degree per minute, so the engine's first-order fit never
sees a bend and gives no card (correlation/engine/forecast.py THERMAL_TAU_MAX_S). A plant engineer
reads it the simple way: the supply rises at r C/min, the machine sits d degrees under its trip,
so it trips in about d / r minutes. This module does exactly that, from SCADA tags only.

- The rate is a least-squares slope of the supply temperature over the last WINDOW_S.
- A card needs a steady rise: at least MIN_RATE_C_MIN, and a fit that explains the points (r >= 0.8).
- A card shows only inside HORIZON_S, and never for a tripped machine.
Pure: no I/O. main.py feeds one SCADA snapshot per incident pass.
"""
from __future__ import annotations

from collections import deque

WINDOW_S = 300.0            # 5 min of supply readings for the slope
MIN_POINTS = 12
MIN_RATE_C_MIN = 0.1        # below this the supply is steady (the compressor window ripple is shorter)
MIN_FIT_R = 0.8
HORIZON_S = 1500.0          # 25 min: a slow drift gets an early card (project choice)


class Drift:
    def __init__(self, window_s: float = WINDOW_S):
        self.window_s = window_s
        self.hist: deque = deque()          # (ts, supply C)

    def _slope(self):
        pts = list(self.hist)
        n = len(pts)
        if n < MIN_POINTS or pts[-1][0] - pts[0][0] < 0.6 * self.window_s:
            return None
        t0 = pts[0][0]
        xs = [p[0] - t0 for p in pts]
        ys = [p[1] for p in pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxx <= 0 or syy <= 0:
            return None
        return sxy / sxx, sxy / (sxx * syy) ** 0.5   # (C per s, correlation)

    def step(self, now: float, supply: float | None, machines: dict) -> list[dict]:
        """machines: {name: {"temp": C, "limit": C, "tripped": bool}}. Returns the drift cards."""
        if supply is None:
            return []
        self.hist.append((now, float(supply)))
        while self.hist and now - self.hist[0][0] > self.window_s:
            self.hist.popleft()
        fit = self._slope()
        if fit is None:
            return []
        rate, r = fit
        if rate * 60.0 < MIN_RATE_C_MIN or r < MIN_FIT_R:
            return []
        cards = []
        for name, m in sorted(machines.items()):
            temp, limit = m.get("temp"), m.get("limit")
            if temp is None or limit is None or m.get("tripped"):
                continue
            eta = max(0.0, (limit - temp) / rate)
            if eta <= HORIZON_S:
                cards.append({"pod": name, "class": "trip", "signal": "coolant_temp", "model": "drift",
                              "eta_s": round(eta, 1), "value": round(temp, 1), "limit": limit,
                              "rate_c_min": round(rate * 60.0, 2), "t_inf": None, "tau_s": None})
        return sorted(cards, key=lambda c: c["eta_s"])


def machines_from_tags(tags: list) -> tuple[float | None, dict]:
    """(supply C, {machine: {temp, limit, tripped}}) from the tag server's /tags rows (GOOD or STALE)."""
    by = {}
    for row in tags or []:
        if row.get("quality") not in ("GOOD", "STALE") or row.get("value") is None:
            continue
        by[(row.get("asset"), row.get("signal"))] = row["value"]
    supply = by.get(("cool-1", "SUPPLY_TEMP"))
    out = {}
    for (asset, sig), val in by.items():
        if sig == "TEMP":
            out[asset] = {"temp": val, "limit": by.get((asset, "TRIP_LIMIT")),
                          "tripped": bool(by.get((asset, "TRIP")))}
    return supply, out
