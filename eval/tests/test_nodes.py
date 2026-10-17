"""Checks of eval/nodes.py: the simulated-day cache gives back exactly what the plant gave, a run
read from the cache equals a fresh run, and the splits use separate days of the year."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "plant"))

from eval import nodes as N                                           # noqa: E402


def test_cache_round_trip_is_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "CACHE_DIR", str(tmp_path))
    raw = {"a.x_c": [1.0, 0.1 + 0.2, float("nan"), -0.0], "b.state": ["run", "idle", "run", "stop"],
           "c.count": [1, 2, 3, 4], "d.on": [True, False, True, True], "e.mixed": [1, 2.5, None, "x"]}
    path, key = N._cache_path(1, 2.0, ("F1", 0.3, 4000.0), "v")
    N._save(path, key, raw, {"seed": 1, "magnitude": 0.1 + 0.2})
    back, info = N._load(path, key)
    assert info == {"seed": 1, "magnitude": 0.1 + 0.2}
    for t, vals in raw.items():
        assert [type(v) for v in back[t]] == [type(v) for v in vals]
        assert [repr(v) for v in back[t]] == [repr(v) for v in vals]       # bit for bit, nan too
    assert N._load(path, key + "other") is None                               # a stale key never loads


def test_cache_key_changes_with_the_run():
    keys = {N._cache_path(s, h, f, "v")[0] for s, h, f in
            [(1, 2.0, None), (2, 2.0, None), (1, 3.0, None), (1, 2.0, ("F1", 0.3, 4000.0)),
             (1, 2.0, ("F1", 0.31, 4000.0))]}
    assert len(keys) == 5


def test_cached_run_equals_fresh_run(tmp_path, monkeypatch):
    """A short run (2 min warm-up, 3 min sampled) with a fault: fresh, then from the cache. The engine
    input (documents and instruments applied) is the same, value for value."""
    monkeypatch.setattr(N, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(N, "WARMUP_S", 120.0)
    monkeypatch.setattr(N, "_VERSION", [])
    fault = ("F6", 0.5, 60.0)
    monkeypatch.setattr(N, "CACHE", False)
    w0, rows0, info0 = N.record(40000, 0.05, fault)
    monkeypatch.setattr(N, "CACHE", True)
    w1, rows1, info1 = N.record(40000, 0.05, fault)                         # simulates and saves
    assert len(os.listdir(tmp_path)) == 1
    w2, rows2, info2 = N.record(40000, 0.05, fault)                         # loads
    assert w0 == w1 == w2 and info0 == info1 == info2
    for t in rows0:
        for r in (rows1, rows2):
            assert r[t].dtype == rows0[t].dtype
            assert np.array_equal(r[t], rows0[t], equal_nan=r[t].dtype.kind == "f")
    assert len(rows0["compressor-1.current_a"]) == 180


def _campaign_seeds(split, normal=8, faults=16, fault_runs=10):
    b = N.SEED_BASE[split]
    return [b + i for i in range(normal)] + [b + 1000 * (j + 1) + i for j in range(faults) for i in range(fault_runs)]


def test_splits_use_separate_days_of_the_year():
    """No day of the year is in two splits, and no run starts on the gap day of a block or on day 364
    (a run that crosses midnight then stays in its own block). Seeds as a campaign of 12 training
    days and 16 faults makes them."""
    days = {s: {N.day_of_year(sd) for sd in _campaign_seeds(s)} for s in N.SPLITS}
    days["train"] = {N.day_of_year(N.SEED_BASE["train"] + i) for i in range(12)}

    def block(d):
        return d // N.BLOCK_D
    for a in N.SPLITS:
        assert all(d % N.BLOCK_D != N.BLOCK_D - 1 and d < 364 for d in days[a])
        for b in N.SPLITS:
            if a < b:
                assert not days[a] & days[b]
                assert not {block(d) for d in days[a]} & {block(d) for d in days[b]}
    assert {len(N.split_days(s, m)) for s in N.SPLITS for m in range(12)} <= set(range(6, 10))
    assert N.day_of_year(1) is None                                    # outside the splits: the seed rule


def test_training_days_cover_the_months():
    """4 training days fall in the 4 seasons (January, April, July, October), 12 in the 12 months."""
    import datetime

    def month(d):
        return (datetime.date(2025, 1, 1) + datetime.timedelta(days=d)).month
    four = [N.day_of_year(N.SEED_BASE["train"] + i) for i in range(4)]
    assert sorted(month(d) for d in four) == [1, 4, 7, 10]
    twelve = {month(N.day_of_year(N.SEED_BASE["train"] + i)) for i in range(12)}
    assert twelve == set(range(1, 13))


def test_plant_starts_on_the_day_of_its_split():
    """The plant of a run reads the weather of its split's day, not the seed's hashed day."""
    from model import weather as WX
    if not os.path.exists(os.environ.get("VISR_WEATHER", WX.DEFAULT_FILE)):
        pytest.skip("no site weather file")
    sd = N.SEED_BASE["validation"] + 3
    p = N.new_plant(sd)
    assert p.wx.h0 == N.day_of_year(sd) * 24.0 + (sd * 7.37) % 24.0


def test_node_matrices_from_arrays_equal_lists():
    """Node.matrices and bins give the same numbers from numpy columns as from Python lists."""
    doc = {"feeds": ["f.v"], "commands": ["m.state"], "measures": ["m.x"]}
    lst = {"f.v": [1.0, 2.0, 3.0, 4.0], "m.state": ["run", "idle", "run", "off"], "m.x": [0.5, 0.6, 0.7, 0.8]}
    arr = {t: N._array(v) for t, v in lst.items()}
    nd = N.Node("m", doc)
    nd.design([(0, lst)])
    for r in (lst, arr):
        U, Y = nd.matrices(r)
        assert U.tolist() == [[1.0, 0.0, 0.0, 1.0], [2.0, 1.0, 0.0, 0.0], [3.0, 0.0, 0.0, 1.0], [4.0, 0.0, 1.0, 0.0]]
        assert nd.bins(r).tolist() == [2, 0, 2, 1]
