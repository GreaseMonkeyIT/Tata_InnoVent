"""A4 Correlator internals: lagged cross-correlation between pod signal vectors.

MASTER_PLAN section 1.4.3. Lags are positive: "src leads dst by lag_s".
"""
from __future__ import annotations

import numpy as np
from scipy import stats

DT_S = 5.0
LAGS_S: tuple[int, ...] = (0, 5, 15, 30, 60, 120)


def _znorm(x: np.ndarray) -> np.ndarray:
    sd = np.std(x)
    return (x - np.mean(x)) / (sd if sd > 1e-9 else 1.0)


def corr_at_lag(src: np.ndarray, dst: np.ndarray, lag_samples: int) -> float:
    """Pearson r between src[t] and dst[t+lag]; falls back to Spearman for heavy tails."""
    if lag_samples > 0:
        a, b = src[:-lag_samples], dst[lag_samples:]
    else:
        a, b = src, dst
    if len(a) < 12 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return 0.0
    if abs(float(stats.skew(a))) > 2.0 or abs(float(stats.skew(b))) > 2.0:
        r = stats.spearmanr(a, b).statistic
    else:
        r = stats.pearsonr(a, b).statistic
    return float(0.0 if np.isnan(r) else r)


def lag_profile(src: np.ndarray, dst: np.ndarray) -> dict[int, float]:
    """r at every configured lag (seconds -> r)."""
    out: dict[int, float] = {}
    for lag_s in LAGS_S:
        ls = int(round(lag_s / DT_S))
        if ls >= len(src) - 12:
            continue
        out[lag_s] = corr_at_lag(_znorm(src), _znorm(dst), ls)
    return out


def best_directed(srcv: np.ndarray, dstv: np.ndarray) -> dict:
    """Evaluate both directions; return the stronger with its peak lag and profile.

    Returns {"forward": bool, "r": float, "lag_s": int, "profile": {lag: r}}
    where forward=True means first argument leads.
    """
    n = min(len(srcv), len(dstv))  # align unequal-length windows; tail = most recent, so lags stay time-consistent
    srcv, dstv = np.asarray(srcv)[-n:], np.asarray(dstv)[-n:]
    fwd = lag_profile(srcv, dstv)
    rev = lag_profile(dstv, srcv)
    fbest = max(fwd.items(), key=lambda kv: abs(kv[1]), default=(0, 0.0))
    rbest = max(rev.items(), key=lambda kv: abs(kv[1]), default=(0, 0.0))
    # Zero-lag ties carry no direction; prefer a nonzero-lag winner if close.
    if abs(rbest[1]) > abs(fbest[1]) + 1e-9:
        return {"forward": False, "r": rbest[1], "lag_s": rbest[0], "profile": rev}
    return {"forward": True, "r": fbest[1], "lag_s": fbest[0], "profile": fwd}


def adjacent_support(profile: dict[int, float], peak_lag: int, floor: float = 0.4) -> bool:
    """Stat clause 1b: |r| stays elevated at a lag adjacent to the peak."""
    lags = sorted(profile)
    if peak_lag not in lags:
        return False
    i = lags.index(peak_lag)
    neighbors = [lags[j] for j in (i - 1, i + 1) if 0 <= j < len(lags)]
    return any(abs(profile[n]) >= floor for n in neighbors)
