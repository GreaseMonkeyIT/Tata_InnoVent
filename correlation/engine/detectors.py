"""A1 Resource Agent internals: EWMA+CUSUM changepoint detection and shape classification.

MASTER_PLAN section 1.4.2. Pure functions, no I/O. Python 3.10+.
"""
from __future__ import annotations

import numpy as np

DT_S = 5.0  # sample resolution, seconds (L0 scrape cadence)


def ewma(x: np.ndarray, alpha: float = 0.3) -> np.ndarray:
    """Exponentially weighted moving average baseline."""
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def robust_sigma(x: np.ndarray) -> float:
    """MAD-based sigma estimate; floor avoids div-by-zero on flat signals."""
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return max(1.4826 * mad, 1e-6)


def cusum_onsets(
    x: np.ndarray,
    k: float = 0.5,
    h: float = 5.0,
    warmup: int = 12,
) -> list[dict]:
    """Two-sided CUSUM on EWMA residual z-scores.

    Returns [{"idx": int, "direction": "up"|"down", "zpeak": float}].
    `warmup` samples are never flagged; sigma is estimated on a longer quiet
    prefix (first third, clamped 24..60 samples) because a 12-sample estimate
    under-estimates badly on compound-noise signals and fabricates onsets.
    Detection resets after each onset so multiple events in one window are caught.
    """
    if len(x) < warmup + 4:
        return []
    base = ewma(x)
    resid = x - np.concatenate(([base[0]], base[:-1]))  # one-step-ahead residual
    sig_n = int(min(max(warmup * 2, len(x) // 3), 60))
    sigma = robust_sigma(resid[:sig_n])
    z = resid / sigma
    onsets: list[dict] = []
    gp = gn = 0.0
    armed_at = warmup
    for i in range(warmup, len(z)):
        gp = max(0.0, gp + z[i] - k)
        gn = max(0.0, gn - z[i] - k)
        if gp > h and i >= armed_at:
            onsets.append({"idx": _backtrack(z, i, +1), "direction": "up", "zpeak": float(z[i])})
            gp = gn = 0.0
            armed_at = i + 3
        elif gn > h and i >= armed_at:
            onsets.append({"idx": _backtrack(z, i, -1), "direction": "down", "zpeak": float(z[i])})
            gp = gn = 0.0
            armed_at = i + 3
    return onsets


def _backtrack(z: np.ndarray, alarm_idx: int, sign: int) -> int:
    """Walk back from the alarm to the first sample contributing to the shift."""
    i = alarm_idx
    while i > 0 and sign * z[i - 1] > 0.5:
        i -= 1
    return i


def classify(x: np.ndarray, onset_idx: int, cap: float | None = None) -> str:
    """Categorize the post-onset shape: saturation | burst | leak | flap | shift.

    Order matters: saturation -> burst -> leak -> flap -> shift.
    """
    seg = x[onset_idx:]
    if len(seg) < 6:
        return "shift"
    pre = float(np.median(x[:max(onset_idx, 1)]))
    sigma_pre = robust_sigma(x[:onset_idx] if onset_idx > 8 else x)
    tail = seg[-max(3, len(seg) // 4):]
    t = np.arange(len(seg), dtype=float)
    slope = float(np.polyfit(t, seg, 1)[0])
    if cap is not None and float(np.median(seg[-max(3, len(seg) // 3):])) >= 0.92 * cap:
        return "saturation"
    excursion = float(np.max(np.abs(seg - pre)))
    came_back = abs(float(np.median(tail)) - pre) < 1.5 * sigma_pre
    if came_back and excursion > 3 * sigma_pre:
        return "burst"
    if slope > 0 and (float(seg[-1]) - float(seg[0])) > 2 * sigma_pre:
        return "leak"
    mid = (float(np.max(seg)) + float(np.min(seg))) / 2.0
    crossings = int(np.sum(np.diff(np.sign(seg - mid)) != 0))
    if crossings >= 4 and np.ptp(seg) > 4 * sigma_pre:
        return "flap"
    return "shift"


def forecast_to_limit(x: np.ndarray, limit: float, tail: int = 24) -> float | None:
    """Linear extrapolation: seconds until `limit` is hit, or None if not truly trending."""
    seg = x[-tail:] if len(x) >= tail else x
    t = np.arange(len(seg), dtype=float)
    slope, _ = np.polyfit(t, seg, 1)
    fit_r = float(np.corrcoef(t, seg)[0, 1])
    if slope <= 1e-9 or fit_r < 0.5:  # must actually be trending, not noise drift
        return None
    samples_left = (limit - seg[-1]) / slope
    eta_s = float(samples_left * DT_S)
    return None if (samples_left < 0 or eta_s > 3600.0) else eta_s
