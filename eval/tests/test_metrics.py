"""Unit checks of the score metrics (eval/metrics.py) on hand-made timelines."""

import pytest

from eval.metrics import Pass, score_calm_run, score_fault_run, summarize, verdicts, wilson


def test_wilson_matches_known_values():
    lo, hi = wilson(27, 30)
    assert lo == pytest.approx(0.744, abs=0.002) and hi == pytest.approx(0.965, abs=0.002)
    lo, hi = wilson(90, 100)
    assert lo == pytest.approx(0.826, abs=0.002) and hi == pytest.approx(0.945, abs=0.002)
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(7, 7)
    assert hi == 1.0 and 0.6 < lo < 0.7       # Wilson is a little tighter than Clopper-Pearson (0.59)


def _passes(seq, t0=0.0, dt=10.0):
    return [Pass(t0 + i * dt, list(r) if r else []) for i, r in enumerate(seq)]


def test_verdict_needs_three_passes_and_counts_episodes_once():
    seq = [None, ["a"], ["a"], ["b"], ["b"], ["b"], ["b"], ["b"], None, ["b"], ["b"], ["b"]]
    v = verdicts(_passes(seq))
    assert [(r, t) for t, r, _ in v] == [("b", 50.0), ("b", 110.0)]


def test_fault_run_scores_top1_rank_delay_and_pre_onset_alarms():
    seq = [None, None, ["x"], ["x"], ["x"], None, None,           # a false alarm before the onset (t = 70)
           None, ["p", "q"], ["q", "p"], ["q", "p"], ["q", "p"], ["q"]]
    r = score_fault_run(_passes(seq), onset_t=70.0, truth="p", watch_s=600.0)
    assert r["false_alarms_pre"] == 1
    assert r["detected"] and r["verdict"] == "q" and not r["top1"]
    assert r["top3"] and r["rr"] == 0.5
    assert r["first_root"] == "p" and r["first_root_t"] == 10.0
    assert r["verdict_t"] == 40.0


def test_cards_true_false_covered_and_lead():
    ps = [Pass(0.0, [], [("m1", 100.0)]), Pass(10.0, [], [("m1", 90.0), ("m2", 50.0)]),
          Pass(20.0, [], [("m2", 40.0)])]
    r = score_fault_run(ps, onset_t=0.0, truth="m1", watch_s=600.0, trips={"m1": 120.0, "m3": 30.0})
    c = r["cards"]
    assert c["carded"] == {"m1": True, "m2": False}
    assert c["covered"]["m1"] == {"lead_s": 120.0, "eta_err_s": 20.0}
    assert c["covered"]["m3"] is None


def test_summary_rates_and_false_alarms_per_hour():
    runs = []
    for i in range(10):
        seq = [None] * 5 + [["p"]] * 4 if i < 8 else [None] * 5 + [["z"]] * 4
        r = score_fault_run(_passes(seq), onset_t=40.0, truth="p", watch_s=600.0)
        r["scenario"] = "PS1"
        runs.append(r)
    calm = score_calm_run(_passes([None] * 359 + [None]))           # one hour, quiet
    calm2 = score_calm_run(_passes([["w"]] * 4 + [None] * 356))      # one hour, one false episode
    s = summarize(runs + [calm, calm2])
    assert s["by_scenario"]["PS1"]["top1"]["k"] == 8 and s["by_scenario"]["PS1"]["top1"]["n"] == 10
    assert s["by_scenario"]["PS1"]["wrong_roots"] == ["z"]
    fa = s["false_alarms"]
    assert fa["episodes"] == 1 and fa["calm_runs_with_any"] == 1
    assert fa["per_hour"] == pytest.approx(1.0 / (2 * 3590 / 3600 + 10 * 30 / 3600), rel=1e-6)


def test_cards_use_the_pass_clock_after_an_onset():
    """Regression (2026-10-15 smoke run): trips stored relative to the onset made every card late.
    Trips and passes share the run clock."""
    ps = [Pass(1000.0 + 10.0 * i, [], [("m1", 120.0)]) for i in range(5)]
    r = score_fault_run(ps, onset_t=1000.0, truth="m1", watch_s=600.0, trips={"m1": 1130.0})
    assert r["cards"]["covered"]["m1"]["lead_s"] == 130.0
