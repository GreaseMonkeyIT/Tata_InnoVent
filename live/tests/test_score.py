"""Checks of live/score.py on hand-made incident rows: the first incident from the onset names the
root or not, an incident before the onset is false, a control run with an incident is false, a pair
passes when both roots are named, and the rates have their n."""
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)

from live import score as SC                                         # noqa: E402


def _inc(t0, named, hit=None, victims=None):
    return {"id": 0, "t0": t0, "named": named, "hit": hit or {named: t0}, "victims": victims or {}}


def test_live_score_rules():
    runs = {
        "validation|normal|1": (None, {"seed": 1}),
        "validation|fault|2": (None, {"fid": "F1", "root": "press-1", "control": False, "onset_s": 1000.0}),
        "validation|fault|3": (None, {"fid": "F1", "root": "press-1", "control": False, "onset_s": 2000.0}),
        "validation|fault|4": (None, {"fid": "F14", "root": "", "control": True, "onset_s": 500.0}),
        "validation|pair|5": (None, {"fid": "F6+F13", "onset_s": 100.0, "faults": [
            {"root": "compressor-1", "control": False}, {"root": "washer-1", "control": False}]}),
    }
    rows = [
        {"key": "validation|normal|1", "incidents": [_inc(50, "ced-1"), _inc(900, "tr-2")]},
        # a false incident before the onset, then the root named 30 s after the onset
        {"key": "validation|fault|2", "incidents": [_inc(400, "weld-2"), _inc(1030, "press-1")]},
        # the first incident from the onset names a victim; press-1 is in it as a candidate
        {"key": "validation|fault|3", "incidents": [_inc(2050, "lmp-1", hit={"lmp-1": 2050, "press-1": 2060})]},
        {"key": "validation|fault|4", "incidents": [_inc(600, "hot-1")]},
        {"key": "validation|pair|5", "incidents": [_inc(150, "compressor-1"), _inc(3000, "washer-1")]},
    ]
    s = SC.score(rows, runs, hours=6.0)
    f1 = s["faults"]["F1"]
    assert (f1["root_named"]["k"], f1["root_named"]["n"]) == (1, 2) and f1["delay_median_s"] == 30.0
    assert (f1["in_incident"]["k"], f1["in_incident"]["n"]) == (2, 2)
    assert (s["controls"]["F14"]["k"], s["controls"]["F14"]["n"]) == (1, 1)
    assert (s["pairs"]["F6+F13"]["k"], s["pairs"]["F6+F13"]["n"]) == (1, 1)
    fl = s["false"]
    assert fl["normal_incidents"] == 2 and fl["normal_hours"] == 6.0
    assert fl["pre_onset_incidents"] == 1 and abs(fl["pre_onset_hours"] - (1000 + 2000 + 500 + 100) / 3600) < 1e-9
    md = SC.report_md(s, "test")
    assert "| F1 | press-1 | 1 / 2 = 50 %" in md and "one per 3.0 h" in md
