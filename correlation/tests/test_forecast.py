"""Unit suite for the OOM early-warning forecaster (engine/forecast.py).

Plants a known working-set ramp and asserts the engine projects the OOM before it happens, and stays
silent on a flat level / a plateau (a DB cache fill) / a missing limit / a too-far drift. Pure --
no cluster, no `run_pass` change, so the 13 kernel fixtures are untouched.
"""
import numpy as np

from engine import detectors
from engine.forecast import incipient_findings

N = 180                         # 15-min window at 5s
LIMIT = 512 * 1024 * 1024       # 512Mi, vision-qc's memory limit
rng = np.random.default_rng(7)


def _jitter(n=N):
    """Real working_set is never bit-flat; jitter it ~2.5 MB so a 'flat' level still looks live."""
    return rng.normal(0, 0.005 * LIMIT, n)


def _ramp(start_frac, end_frac, n=N):
    """working_set climbing linearly from start_frac*LIMIT to end_frac*LIMIT across the window."""
    return np.linspace(start_frac * LIMIT, end_frac * LIMIT, n)


def test_leak_projects_oom_before_the_limit():
    out = incipient_findings({"vision-qc": _ramp(0.60, 0.95)}, {"vision-qc": LIMIT})
    assert len(out) == 1
    f = out[0]
    assert f["pod"] == "vision-qc" and f["kind"] == "incipient" and f["signal"] == "mem"
    assert f["class"] == "leak"
    assert f["eta_s"] > 0
    assert 0.0 <= f["headroom_frac"] < 0.10          # ~5% headroom left at 95% of the limit


def test_flat_level_near_limit_is_silent():
    # firmware-cache parked near its limit but FLAT (usage != pressure) -> no warning
    v = np.full(N, 0.9 * LIMIT) + _jitter()
    assert incipient_findings({"firmware-cache": v}, {"firmware-cache": LIMIT}) == []


def test_plateau_cache_fill_is_silent():
    # a DB cache fill: climbs early then plateaus far below the limit -> recent tail is flat -> silent
    v = np.concatenate([np.linspace(0.05 * LIMIT, 0.20 * LIMIT, 60), np.full(N - 60, 0.20 * LIMIT)])
    v = v + _jitter()
    assert incipient_findings({"timescaledb": v}, {"timescaledb": LIMIT}) == []


def test_missing_or_zero_limit_is_skipped():
    v = _ramp(0.60, 0.95)
    assert incipient_findings({"x": v}, {}) == []            # no limit known
    assert incipient_findings({"x": v}, {"x": 0.0}) == []    # limit not set (0)


def test_eta_beyond_horizon_is_skipped_but_fires_with_a_wider_one():
    # a slow drift whose projected OOM is ~25 min out: filtered by a 15-min horizon, kept by a 60-min one
    v = _ramp(0.84, 0.90)
    assert incipient_findings({"x": v}, {"x": LIMIT}, horizon_s=900.0) == []
    assert len(incipient_findings({"x": v}, {"x": LIMIT}, horizon_s=3600.0)) == 1


def test_below_min_frac_is_skipped_even_when_trending_to_the_limit_soon():
    # a steep climb that WOULD hit the limit within the horizon, but the pod is only at ~45% of its
    # limit -> not a real OOM risk yet (the cooling-monitor-under-fio / safety-interlock false-fire class)
    v = np.concatenate([np.full(160, 0.10 * LIMIT), np.linspace(0.10 * LIMIT, 0.45 * LIMIT, 20)])
    assert incipient_findings({"x": v}, {"x": LIMIT}) == []                    # 45% < default min_frac 0.6
    assert len(incipient_findings({"x": v}, {"x": LIMIT}, min_frac=0.4)) == 1  # fires once the bar is lower


def test_onset_anchored_eta_is_sharper_than_the_diluted_tail():
    # a just-started leak: flat for most of the window, fast climb only in the last ~12 samples.
    # the full 24-sample tail (half flat) dilutes the slope -> long ETA; anchoring to the onset
    # fits only the active climb -> a realistic, shorter ETA.
    v = np.concatenate([np.full(168, 0.30 * LIMIT),
                        np.linspace(0.30 * LIMIT, 0.50 * LIMIT, 12)]) + _jitter()
    diluted = detectors.forecast_to_limit(v, LIMIT, tail=24)
    # min_frac=0 isolates the ETA-sharpness behavior under test (the fixture sits at ~50% of the
    # limit, below the default 0.6 proximity gate, which is exercised separately above)
    out = incipient_findings({"vision-qc": v}, {"vision-qc": LIMIT}, horizon_s=3600.0, min_frac=0.0)
    assert len(out) == 1 and out[0]["eta_s"] > 0
    assert diluted is not None and out[0]["eta_s"] < diluted    # onset-anchoring is sharper
