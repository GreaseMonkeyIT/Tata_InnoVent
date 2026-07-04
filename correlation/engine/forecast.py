"""A1 forecaster: OOM / saturation early-warning by linear extrapolation to a resource limit.

Pure functions, no I/O (like merge.py). `run_pass` stays untouched -- the service layer collects the
working-set vectors + per-pod limits and calls incipient_findings(), so the 13 fixtures are safe.

A memory leak is self-caused (source == victim), so it forms NO cross-pod causal edge -- the warning
is a per-pod projection of the working-set ramp to the pod's memory limit ("OOM in ~Ns"), emitted
BEFORE the kill. A flat level or a plateau (e.g. a DB cache fill) has ~zero tail slope and is skipped
by forecast_to_limit; a slow drift whose ETA lands beyond the horizon is skipped here. This realizes
the S5 "forecast OOM before it happens" beat (MASTER_PLAN 2.5 / 5.5).
"""
from __future__ import annotations

import numpy as np

from . import detectors

DEFAULT_HORIZON_S = 900.0   # only warn when the limit is within this window (a leak an hour out is noise)
DEFAULT_TAIL = 24           # samples (~2min) cap on the slope-fit window -- a plateau over this tail = no warning
MIN_FIT_N = 6               # never fit fewer than this many samples (a just-started climb isn't trusted yet)
DEFAULT_MIN_FRAC = 0.6      # only warn once the pod is already past this fraction of its limit -- a real OOM
                            # risk is CLOSE to the cap; a transient climb (cooling-monitor under fio, ~43%) or
                            # a slow drift (safety-interlock, ~24%) far below the limit is not a leak


def _fit_segment(x: np.ndarray, tail: int, min_n: int = MIN_FIT_N) -> np.ndarray:
    """The slice to fit the slope over: from the most recent UPWARD CUSUM onset (the active leak's
    start), but never more than `tail` samples (a long steady climber uses the recent tail) and never
    fewer than `min_n`. This stops the pre-leak flat baseline from diluting the slope -- so early in a
    leak the ETA reflects the real climb rate instead of being stretched ~6x by the flat history."""
    n = len(x)
    ups = [o["idx"] for o in detectors.cusum_onsets(x) if o["direction"] == "up"]
    lo = max(n - tail, ups[-1]) if ups else n - tail
    lo = min(lo, n - min_n)
    return x[max(0, lo):]


def incipient_findings(
    mem_vectors: dict[str, np.ndarray],
    limits: dict[str, float],
    horizon_s: float = DEFAULT_HORIZON_S,
    tail: int = DEFAULT_TAIL,
    min_frac: float = DEFAULT_MIN_FRAC,
    signal: str = "mem",
    cls: str = "leak",
) -> list[dict]:
    """mem_vectors: {pod: working_set bytes vector}; limits: {pod: memory limit bytes}.

    Signal-agnostic (2C'): the same ramp-to-limit projection serves any (signal, limit) pair —
    the OOM pair (mem → mem_limit, class "leak") and the plant thermal pair (coolant_temp →
    temp_limit, class "trip", the PS5 card). `signal`/`cls` only label the finding.

    Returns one `incipient` finding per pod whose working set is (a) already past `min_frac` of its
    limit and (b) linearly trending to that limit within `horizon_s`. Pods with no positive limit,
    sitting below `min_frac` of the cap (a transient climb or a slow drift, not a real OOM risk),
    not genuinely trending (forecast_to_limit -> None: flat/plateau/noise), or with an ETA beyond
    the horizon are skipped. Sorted soonest-first.
    """
    out: list[dict] = []
    for pod, vec in mem_vectors.items():
        limit = limits.get(pod)
        if not limit or limit <= 0:
            continue
        x = np.asarray(vec, dtype=float)
        if x.size == 0:
            continue
        cur = float(x[-1])
        if cur < min_frac * limit:
            continue  # not close enough to the cap to be a real OOM risk yet (drops the transient
                      # cooling-monitor-under-fio climb and the safety-interlock slow drift)
        seg = _fit_segment(x, tail)                          # fit over the ACTIVE climb, not the diluted tail
        eta = detectors.forecast_to_limit(seg, float(limit), tail=len(seg))
        if eta is None or eta > horizon_s:
            continue
        out.append({
            "pod": pod,
            "signal": signal,
            "kind": "incipient",
            "class": cls,
            "eta_s": round(eta, 1),
            "value": round(cur, 1),
            "limit": round(float(limit), 1),
            "headroom_frac": round(max(0.0, 1.0 - cur / limit), 3),
        })
    out.sort(key=lambda f: f["eta_s"])
    return out
