"""A1 forecaster: OOM / saturation early-warning by linear extrapolation to a resource limit.

Pure functions, no I/O (like merge.py). `run_pass` stays untouched -- the service layer collects the
working-set vectors + per-pod limits and calls incipient_findings(), so the 13 fixtures are safe.

A memory leak is self-caused (source == victim), so it forms NO cross-pod causal edge -- the warning
is a per-pod projection of the working-set ramp to the pod's memory limit ("OOM in ~Ns"), emitted
BEFORE the kill. A flat level or a plateau (e.g. a DB cache fill) has ~zero tail slope and is skipped
by forecast_to_limit; a slow drift whose ETA lands beyond the horizon is skipped here. This realizes
the "forecast the failure before it happens" beat (OOM cards, and the PS5 trip card via FORECAST_PAIRS).
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


THERMAL_TAIL = 48          # samples (4 min): a first-order curve needs enough of the bend to see it
TAU_GRID_S = np.geomspace(20.0, 1200.0, 48)   # candidate thermal time constants, seconds
THERMAL_CONFIRM = (0, 2, 4)  # the fit must give a card now, 10 s ago, and 20 s ago (samples back). Early
                             # in a climb the bend is not visible yet and a fit can flash a card for one
                             # or two passes. On the forge evening of 2026-09-25 the real cards lasted 10 to
                             # 31 passes and the flashes 1 to 3.
THERMAL_TAU_MAX_S = 600.0    # LOG-099: a best tau above this means the bend is not visible yet, so T_inf is a
                             # guess (100 to 179 C). Every false card of the 2026-09-26 watch (Scenario 2
                             # press-1 and cnc-1) had tau at the grid edge (1200 s). Every real card had
                             # 88 to 502 s. The cost: real cards come 10 to 20 s later, still 75 s or more
                             # before the trip.


def first_order_eta(seg: np.ndarray, limit: float, dt: float = detectors.DT_S):
    """Fit T(t) = T_inf + C * exp(-(t - t_now) / tau) to a rising segment (LOG-091).

    A coolant temperature is a first-order lag: after a step in heat or cooling it bends over and
    levels off at T_inf. A straight line through the early climb overshoots, so the old forecaster
    gave trip cards to machines that levelled off below the trip (forge 2026-09-25: press-1 at 74 C
    in Scenario 2, cnc-1 at 65 C in Scenario 5) and a countdown that ran early (Scenario 1).
    For each tau on a grid, (T_inf, C) is an ordinary least-squares fit; the best tau wins.
    Returns (eta_s, t_inf, tau_s), or None when the segment is not rising toward the limit: it is
    flat, noise, cooling (C >= 0), it levels off at or below the limit, or the bend is not visible
    yet (tau above THERMAL_TAU_MAX_S)."""
    x = np.asarray(seg, dtype=float)
    n = x.size
    if n < MIN_FIT_N:
        return None
    t = np.arange(n, dtype=float) * dt
    slope = np.polyfit(t, x, 1)[0]
    if slope <= 1e-9 or float(np.corrcoef(t, x)[0, 1]) < 0.5:
        return None                                  # not truly climbing, same test as the line
    best = None
    for tau in TAU_GRID_S:
        e = np.exp(-(t - t[-1]) / tau)
        a = np.column_stack([np.ones(n), e])
        coef, *_ = np.linalg.lstsq(a, x, rcond=None)
        sse = float(np.sum((a @ coef - x) ** 2))
        if best is None or sse < best[0]:
            best = (sse, float(coef[0]), float(coef[1]), float(tau))
    _, t_inf, c, tau = best
    if c >= 0 or t_inf <= limit:
        return None                                  # cooling, or it levels off below the trip
    if tau > THERMAL_TAU_MAX_S:
        return None                                  # still a straight climb: where it levels off is unknown
    t_now = t_inf + c                                # the fitted value now (less noise than x[-1])
    if t_now >= limit:
        return 0.0, t_inf, tau
    return float(tau * np.log((t_inf - t_now) / (t_inf - limit))), t_inf, tau


def stopped_pods(current_vectors: dict[str, np.ndarray], floor_a: float = 1.0, frac: float = 0.1) -> set:
    """Machines that draw (almost) no current now: a protective trip opened the contactor, or the PLC
    holds them out of RUN. A stopped machine makes no heat, so it cannot be heading for a trip, and
    it gets no trip card (LOG-091: forge showed cards on a tripped, cooling press-1 in Scenarios 1
    and 5). 'Almost none' is below floor_a amps, or below frac of the machine's median in the ring."""
    out = set()
    for pod, vec in (current_vectors or {}).items():
        x = np.asarray(vec, dtype=float)
        if x.size and float(x[-1]) < max(floor_a, frac * float(np.median(x))):
            out.add(pod)
    return out


def incipient_findings(
    mem_vectors: dict[str, np.ndarray],
    limits: dict[str, float],
    horizon_s: float = DEFAULT_HORIZON_S,
    tail: int = DEFAULT_TAIL,
    min_frac: float = DEFAULT_MIN_FRAC,
    signal: str = "mem",
    cls: str = "leak",
    floors: dict[str, float | None] | None = None,
    model: str = "linear",
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

    floors: an optional per-pod baseline band (the engine's learned threshold for the same signal).
    A pod whose recent level sits inside its band is skipped: a steady coolant temperature at 83 %
    of the trip limit is normal, not a ramp. None, or a pod missing from floors, skips no pod.

    model: "linear" (the memory ramp) or "first_order" (a coolant temperature, LOG-091). A
    first-order finding also carries `t_inf`, the level the curve settles at, and `tau_s`.
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
        floor = (floors or {}).get(pod)
        if floor is not None and float(np.median(x[-6:])) <= floor:
            continue  # still inside its learned normal band: a steady level, not a ramp
        extra = {}
        if model == "first_order":
            fits = [first_order_eta(_fit_segment(x[:x.size - k], THERMAL_TAIL), float(limit))
                    for k in THERMAL_CONFIRM if x.size - k >= MIN_FIT_N]
            if len(fits) < len(THERMAL_CONFIRM) or any(f is None for f in fits):
                continue
            fit = fits[0]
            eta = fit[0]
            extra = {"t_inf": round(fit[1], 1), "tau_s": round(fit[2]), "model": "first_order"}
        else:
            seg = _fit_segment(x, tail)                      # fit over the ACTIVE climb, not the diluted tail
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
            **extra,
        })
    out.sort(key=lambda f: f["eta_s"])
    return out
