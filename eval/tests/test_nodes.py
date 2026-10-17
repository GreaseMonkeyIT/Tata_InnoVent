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


def test_fit_and_calibration_days_each_cover_the_year():
    """36 training days: 24 fit days, 2 in every month, and 12 calibration days, 1 in every month,
    all on different days. Fewer than 3 rounds: the last quarter calibrates, as before."""
    import collections
    import datetime

    def month(d):
        return (datetime.date(2025, 1, 1) + datetime.timedelta(days=d)).month
    train = [(N.SEED_BASE["train"] + i, None) for i in range(36)]
    fit, calib = N.fit_and_calibration(train)
    fm = collections.Counter(month(N.day_of_year(sd)) for sd, _ in fit)
    cm = collections.Counter(month(N.day_of_year(sd)) for sd, _ in calib)
    assert fm == {m: 2 for m in range(1, 13)} and cm == {m: 1 for m in range(1, 13)}
    assert len({N.day_of_year(sd) for sd, _ in train}) == 36
    fit12, cal12 = N.fit_and_calibration(train[:12])
    assert [sd for sd, _ in cal12] == [sd for sd, _ in train[9:12]]


def test_plant_starts_on_the_day_of_its_split():
    """The plant of a run reads the weather of its split's day, not the seed's hashed day."""
    from model import weather as WX
    if not os.path.exists(os.environ.get("VISR_WEATHER", WX.DEFAULT_FILE)):
        pytest.skip("no site weather file")
    sd = N.SEED_BASE["validation"] + 3
    p = N.new_plant(sd)
    assert p.wx.h0 == N.day_of_year(sd) * 24.0 + (sd * 7.37) % 24.0


def _cusum_loop(z, cov, k):
    """The CUSUM recursion, one sample at a time (the reference for the vectorized form)."""
    up, dn, out = np.zeros(z.shape[1]), np.zeros(z.shape[1]), []
    for t in range(len(z)):
        for j in range(z.shape[1]):
            if cov[t] and np.isfinite(z[t, j]):
                up[j] = max(0.0, up[j] + z[t, j] - k)
                dn[j] = max(0.0, dn[j] - z[t, j] - k)
        out.append(max(up.max(), dn.max()))
    return np.array(out)


def test_cusum_equals_the_recursion():
    """The cumulative-sum form equals the recursion, with samples outside the training range and
    NaN residuals holding the sums."""
    rng = np.random.default_rng(3)
    z = rng.normal(0.0, 1.0, (2000, 3)) + np.where(np.arange(2000) > 1200, 1.5, 0.0)[:, None] * [1, 0, -1]
    z[rng.random(z.shape) < 0.01] = np.nan
    cov = rng.random(2000) > 0.1
    assert np.allclose(N.cusum(z, cov, 0.5), _cusum_loop(z, cov, 0.5), atol=1e-9)
    assert N.cusum(np.zeros((5, 0)), np.ones(5, bool)).tolist() == [0.0] * 5


def test_cusum_alarm_holds_until_the_sum_is_back_at_zero():
    s = np.array([0.0, 2.0, 6.0, 4.0, 1.0, 0.0, 3.0, 7.0])
    assert N.cusum_alarm(s, 5.0).tolist() == [False, False, True, True, True, False, False, True]


def test_cusum_carries_evidence_across_idle_seconds():
    """A press that bends 8 s in every 41 s: the window gate never sees 79 of 120 s high, and the
    CUSUM passes its limit within two cycles."""
    z = np.zeros((600, 1))
    for c in range(0, 600, 41):
        z[c:c + 8] = 10.0
    cov = np.ones(600, bool)
    assert not N.gate(np.abs(z)).any()
    first = int(np.flatnonzero(N.cusum_alarm(N.cusum(z, cov), 50.0))[0])
    assert first < 41 + 8


def test_cusum_with_a_k_per_output_equals_the_recursion():
    rng = np.random.default_rng(5)
    z = rng.normal(0.0, 1.0, (1500, 2))
    cov = np.ones(1500, bool)
    k = np.array([0.5, 2.0])
    up, dn, ref = np.zeros(2), np.zeros(2), []
    for t in range(1500):
        up = np.maximum(0.0, up + z[t] - k)
        dn = np.maximum(0.0, dn - z[t] - k)
        ref.append(max(up.max(), dn.max()))
    assert np.allclose(N.cusum(z, cov, k), ref, atol=1e-9)


def test_slow_bias_k():
    """An output with a slow bias of 2 gets k near 2, one with no bias keeps the textbook 0.5."""
    rng = np.random.default_rng(6)
    t = np.arange(7200)
    z = np.column_stack([rng.normal(0.0, 1.0, 7200), rng.normal(0.0, 1.0, 7200) + 2.0 * np.sin(t / 7200 * np.pi)])
    k = N.slow_bias_k([(z, np.ones(7200, bool))])
    assert k[0] == 0.5 and 1.8 < k[1] < 2.1
    assert N.slow_bias_k([(z[:100], np.ones(100, bool))]) == 0.5        # too short: the textbook k


def test_cusum_limit_is_the_conformal_quantile():
    m = list(range(1, 13))                                           # 12 calibration days
    assert N.cusum_limit(m, 0.1, 0.0) == 12.0                         # ceil(13 x 0.9) = 12: the largest
    assert N.cusum_limit(list(range(1, 41)), 0.1, 0.0) == 37.0        # ceil(41 x 0.9) = 37
    assert N.cusum_limit([0.5, 1.0], 0.1, 5.0) == 5.0                 # never below the textbook h
    assert N.cusum_limit([], 0.1, 5.0) == 5.0


def test_first_activity_of_the_root():
    doc = {"feeds": [], "commands": ["p.phase", "p.valve"], "measures": ["p.x"]}
    rows = {"p.phase": np.asarray(["idle"] * 5 + ["bend"] * 3), "p.valve": np.zeros(8)}
    assert N.first_activity(rows, doc, 2) == 5
    assert N.first_activity(rows, {"feeds": [], "commands": ["p.valve"], "measures": []}, 2) == 2


def test_saved_view_equals_record(tmp_path, monkeypatch):
    """The engine view on disk gives back what record() gives: the same numbers bit for bit, the
    same text states (as codes), the same documents and labels. A second call reuses the file."""
    monkeypatch.setattr(N, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(N, "WARMUP_S", 120.0)
    monkeypatch.setattr(N, "_VERSION", [])
    fault = ("F1", 0.5, 60.0)
    w0, rows0, info0 = N.record(40001, 0.05, fault)
    w1, path, info1 = N.view(40001, 0.05, fault)
    w2, path2, info2 = N.view(40001, 0.05, fault)
    assert path == path2 and w0 == w1 == w2 and info0 == info1 == info2
    back = N.load_view(path)
    assert set(back) == set(rows0)
    texts = 0
    for t, v in rows0.items():
        if v.dtype.kind in "US":
            texts += 1
            assert isinstance(back[t], N.Text) and back[t].decode().tolist() == v.tolist()
        else:
            assert back[t].dtype == v.dtype and np.array_equal(back[t], v, equal_nan=True)
    assert texts > 0
    some = N.load_view(path, ["press-1.phase", "press-1.oil_c"])
    assert list(some) == ["press-1.phase", "press-1.oil_c"]


def test_node_reads_text_codes_as_text():
    """Node.design, matrices and bins give the same numbers from Text codes as from text arrays."""
    doc = {"feeds": ["f.v"], "commands": ["m.state"], "measures": ["m.x"]}
    txt = {"f.v": np.array([1.0, 2.0, 3.0, 4.0]), "m.state": np.array(["run", "idle", "run", "off"]),
           "m.x": np.array([0.5, 0.6, 0.7, 0.8])}
    cod = dict(txt, **{"m.state": N.Text.encode(txt["m.state"])})
    a, b = N.Node("m", doc), N.Node("m", doc)
    a.design([(0, txt)])
    b.design([(0, cod)])
    assert a.cats == b.cats and a.num_in == b.num_in
    assert np.array_equal(a.matrices(txt)[0], b.matrices(cod)[0])
    assert a.bins(txt).tolist() == b.bins(cod).tolist()
    assert cod["m.state"][:2].names == ("idle", "off", "run") and cod["m.state"][1] == "idle"


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
