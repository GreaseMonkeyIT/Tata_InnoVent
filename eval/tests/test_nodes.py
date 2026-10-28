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
    # the lead (the end of the warm-up, here all of its 120 s) comes before the 180 scored samples
    assert info0["lead"] == 120 and len(rows0["compressor-1.current_a"]) == 120 + 180


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
    assert N.cusum_limit(list(range(1, 41)), None, 0.0) == 40.0       # no alpha: the largest maximum


def test_gumbel_limit():
    """The Gumbel limit of Gumbel samples comes near the true quantile and goes past the largest
    maximum for a small alpha."""
    rng = np.random.default_rng(12)
    m = rng.gumbel(100.0, 20.0, 400)
    h = N.cusum_limit(m, 0.01, 0.0, "gumbel")
    assert abs(h - (100.0 - 20.0 * np.log(-np.log(0.99)))) < 15.0
    few = m[:12]
    assert N.cusum_limit(few, 0.008, 0.0, "gumbel") > few.max()
    assert N.cusum_limit(few, 0.1, 0.0, "conformal") == few.max()


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


def test_lead_warms_the_filters_and_is_dropped():
    """RunsModel drops the lead rows of each run after the filters ran through them: the rows left
    equal the features of the whole run from the lead on."""
    rng = np.random.default_rng(8)
    U1, U2 = rng.normal(size=(500, 2)), rng.normal(size=(300, 2))
    m = N.RunsModel(["a", "b"], ["y"], taus=(5.0, 60.0))
    plain = N.RunsModel(["a", "b"], ["y"], taus=(5.0, 60.0))
    m.bounds, m.leads = [500, 300], [100, 40]
    X = m.features(np.vstack([U1, U2]), 1.0)
    assert np.array_equal(X, np.vstack([plain.features(U1, 1.0)[100:], plain.features(U2, 1.0)[40:]]))
    assert N.lead_of(N.Run({"x": [1]}, 7)) == 7 and N.lead_of({"x": [1]}) == 0


def test_view_keeps_the_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(N, "WARMUP_S", 300.0)
    monkeypatch.setattr(N, "LEAD_S", 200.0)
    monkeypatch.setattr(N, "_VERSION", [])
    w, path, info = N.view(40002, 0.02, None)
    r = N.load_view(path, ["press-1.oil_c"])
    assert info["lead"] == 200 and r.lead == 200 and len(r["press-1.oil_c"]) == 200 + 72


def test_documents_list_the_loops_and_the_air_header():
    """Every tag of a PLC loop and of the air header is a plant tag, every loop output is a command
    of its asset's node, and every node knows its asset and that asset's loop outputs."""
    from model.wiring import loops, wiring
    p = N.new_plant(N.SEED_BASE["train"])
    p.step(0.1)
    tags = set(p.tags())
    w = wiring(p)
    for lp in loops(p):
        assert set(lp["measured"] + lp["output"]) <= tags
        assert set(lp["output"]) <= set(w[lp["asset"]]["commands"]) | set(w[lp["asset"]]["feeds"])
        assert w[f"{lp['asset']}:loop"]["measures"] == lp["output"]
    assert set(w["compressor-1:air"]["measures"]) <= tags and "compressor-1.pressure_barg" in w["compressor-1:air"]["measures"]
    for name, doc in w.items():
        assert doc["asset"] == name.split(":")[0]
    assert w["compressor-1"]["loop_outputs"] == ["compressor-1.state"] == w["compressor-1:air"]["loop_outputs"]


def test_text_output_and_own_loop_outputs_left_out_of_coverage():
    """A controller's text state as an output becomes one output per category; an input that is a
    loop output of the node's own asset does not count for coverage (inputs and their filters)."""
    doc = {"feeds": ["a.p"], "commands": ["a.state"], "measures": ["a.x"], "asset": "a",
           "loop_outputs": ["a.state"]}
    r = {"a.p": np.array([1.0, 2.0, 3.0]), "a.state": np.array(["on", "off", "on"]), "a.x": np.array([0.1, 0.2, 0.3])}
    nd = N.Node("a", doc)
    names = nd.design([(0, r)])
    assert names == ["a.p", "a.state=off", "a.state=on"] and nd.out_names == ["a.x"]
    assert nd.coverage_columns(names, 2) == [True, False, False, True, True, False, False, False, False]
    loop = N.Node("a:loop", {"feeds": ["a.p"], "commands": [], "measures": ["a.state"], "asset": "a",
                             "loop_outputs": ["a.state"]})
    loop.design([(0, r)])
    assert loop.out_names == ["a.state=off", "a.state=on"]
    assert loop.matrices(r)[1].tolist() == [[0.0, 1.0], [1.0, 0.0], [0.0, 1.0]]


def test_cusum_up_and_rolling_mean_equal_their_loops():
    rng = np.random.default_rng(11)
    u = rng.normal(size=(800, 2))
    cov = rng.random(800) > 0.1
    S, ref = np.zeros(2), []
    for t in range(800):
        if cov[t]:
            S = np.maximum(0.0, S + u[t] - np.array([0.3, 1.0]))
        ref.append(S.max())
    assert np.allclose(N.cusum_up(u, cov, np.array([0.3, 1.0])), ref, atol=1e-9)
    m = N.rolling_mean(u, cov, 20)
    for t in (5, 9, 10, 50, 799):
        sl = slice(max(0, t + 1 - 20), t + 1)
        okk = cov[sl]
        exp = u[sl][okk].mean(0) if okk.sum() >= 10 else np.array([np.nan, np.nan])
        assert np.allclose(m[t], exp, equal_nan=True)


def test_fit2_split_gives_more_spread_days():
    """fit2 with 60 training days: 24 fit days (2 per month) and 36 spread days (3 per month)."""
    import collections
    import datetime

    def month(d):
        return (datetime.date(2025, 1, 1) + datetime.timedelta(days=d)).month
    train = [(N.SEED_BASE["train"] + i, None) for i in range(60)]
    fit, cal = N.fit_and_calibration(train, "fit2")
    assert collections.Counter(month(N.day_of_year(s)) for s, _ in fit) == {m: 2 for m in range(1, 13)}
    assert collections.Counter(month(N.day_of_year(s)) for s, _ in cal) == {m: 3 for m in range(1, 13)}
    assert N.fit_and_calibration(train[:36]) == N.fit_and_calibration(train[:36], "thirds")


def test_score_by_asset():
    """A machine alarms when the first of its nodes does; the root is scored on the machine."""
    z = np.zeros(10, bool)
    al = {"c": z.copy(), "c:air": z.copy(), "t": z.copy()}
    al["c:air"][6] = True
    al["t"][7] = True
    rec = N.score_fault(al, {"onset_s": 5.0, "root": "c", "control": False},
                        {"c": "c", "c:air": "c", "t": "t"})
    assert rec["detected"] and rec["delay_s"] == 1.0 and rec["first"] == ["c"] and rec["top1"]
    assert rec["alarmed"] == ["c", "t"]


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


def test_change_point_and_root_by_change_point():
    """Page's estimate: the sample after the last zero of the statistic that passed its limit. The
    root by change point is the machine whose change began first, even if it alarmed later."""
    s = np.array([0.0, 0.0, 1.0, 3.0, 6.0, 9.0])
    assert N.change_point([(s, 5.0)], 4) == 2 and N.change_point([(s, 50.0)], 4) == 4
    z = np.zeros(20, bool)
    al = {"root": z.copy(), "victim": z.copy()}
    al["root"][12] = True                         # alarms later
    al["victim"][8] = True                        # alarms first
    rec = N.score_fault(al, {"onset_s": 5.0, "root": "root", "control": False}, None,
                        {"root": 6, "victim": 7})
    assert rec["first"] == ["victim"] and not rec["top1"]
    assert rec["first_cp"] == ["root"] and rec["top1_cp"]


def test_clip_caps_z_for_the_cusum_rules():
    """With a clip, a node's z is capped at +-clip (a one-sample spike cannot pass a small limit
    alone); the gate rule keeps its z."""
    rng = np.random.default_rng(13)

    def run(n, spike=False):
        u = rng.normal(size=n)
        y = 2.0 * u + 0.1 * rng.normal(size=n)
        if spike:
            y[n // 2] += 50.0
        return {"a.u": u, "a.y": y}
    doc = {"feeds": ["a.u"], "commands": [], "measures": ["a.y"], "asset": "a", "loop_outputs": []}
    fit, cal = [(i, run(400)) for i in range(3)], [(9, run(400)), (10, run(400))]
    clipped = N.Node("a", doc, "cusumk", clip=4.0).train(fit, cal)
    z, _ = clipped.z(run(400, spike=True))
    assert np.nanmax(np.abs(z)) <= 4.0 + 1e-12
    gate = N.Node("a", doc, "gate", clip=4.0).train(fit, cal)
    zg, _ = gate.z(run(400, spike=True))
    assert np.nanmax(np.abs(zg)) > 4.0


def test_feedback_inputs_follow_the_documents():
    """An input is feedback when its node can be reached from this node's outputs; a feed from a
    node that never sees this node's outputs is not."""
    w = {"pump": {"feeds": ["user.out_c", "grid.v"], "commands": [], "measures": ["pump.supply_c"]},
         "user": {"feeds": ["pump.supply_c", "grid.v"], "commands": [], "measures": ["user.out_c"]},
         "grid": {"feeds": [], "commands": [], "measures": ["grid.v"]}}
    fb = N.feedback_inputs(w)
    assert fb == {"pump": ["user.out_c"], "user": ["pump.supply_c"], "grid": []}
    nd = N.Node("pump", dict(w["pump"], loop_outputs=[], feedback_inputs=fb["pump"]))
    assert nd.coverage_columns(["user.out_c", "grid.v"], 1) == [False, True, False, True]


def test_tie_in_the_first_second_is_split_by_change_point():
    z = np.zeros(20, bool)
    al = {"root": z.copy(), "victim": z.copy()}
    al["root"][9] = True
    al["victim"][9] = True                        # the same second
    rec = N.score_fault(al, {"onset_s": 5.0, "root": "root", "control": False}, None,
                        {"root": 6, "victim": 8})
    assert rec["first"] == ["root", "victim"] and not rec["top1"]
    assert rec["named"] == ["root"] and rec["top1_named"]


def test_graph_rule_names_the_upstream_machine():
    """The victim alarms first, the root 30 s later: within the window the victim is downstream of the
    root in the documents, so the graph rule names the root."""
    w = {"pump": {"feeds": [], "commands": [], "measures": ["pump.q"]},
         "user": {"feeds": ["pump.q"], "commands": [], "measures": ["user.t"]}}
    reach = N.downstream(w)
    assert reach == {"pump": {"user"}, "user": set()}
    z = np.zeros(400, bool)
    al = {"pump": z.copy(), "user": z.copy()}
    al["user"][100] = True
    al["pump"][130] = True
    rec = N.score_fault(al, {"onset_s": 50.0, "root": "pump", "control": False}, None, {}, reach)
    assert not rec["top1"] and rec["top1_g120"] and rec["top1_g600"]


def test_confirm_drops_short_episodes():
    a = np.array([0, 1, 1, 0, 1, 1, 1, 1, 0], bool)
    assert N.confirm(a, 3).tolist() == [False, False, False, False, False, False, True, True, False]
    assert N.confirm(a, 1).tolist() == a.tolist()


def test_change_point_uses_the_sum_that_passed():
    """Two sums: one stays a little above 0 from the start (noise), one starts at sample 50 and
    passes the limit. The change point is 50, not the start of the run."""
    T = 100
    noise = np.full(T, 0.5)                       # never back at 0
    sig = np.where(np.arange(T) >= 50, (np.arange(T) - 49) * 2.0, 0.0)
    S = np.column_stack([noise, sig])
    assert N.change_point([(S, 30.0)], 80) == 50
    assert N.change_point([(S.max(axis=1), 30.0)], 80) == 0      # what the largest sum alone gives
    # in an episode below the limit at t (the alarm holds until the sum is back at 0)
    S2 = np.column_stack([np.r_[np.zeros(10), np.arange(1, 21) * 3.0, np.arange(20, 0, -1) * 1.0, np.zeros(10)]])
    assert N.change_point([(S2, 40.0)], 45) == 10


def test_score_pair_names_both_roots():
    """Two faults: both roots alarm and neither is downstream of the other, so both are named and the
    pair passes; a victim downstream of a root is not named; a control case needs no root."""
    reach = {"a": {"v"}, "b": set(), "v": set()}
    z = np.zeros(50, bool)
    al = {"a": z.copy(), "b": z.copy(), "v": z.copy()}
    al["a"][20] = al["b"][30] = al["v"][21] = True
    info = {"fid": "F1+F2", "seed": 1, "onset_s": 10.0, "faults": [
        {"fid": "F1", "root": "a", "control": False, "onset_s": 10.0},
        {"fid": "F2", "root": "b", "control": False, "onset_s": 25.0}]}
    rec = N.score_pair(al, info, {}, reach)
    assert rec["named"] == ["a", "b"] and rec["passed"] and rec["all_detected"]
    # an upstream machine that alarms after a root does not remove it
    late = dict(al, up=np.zeros(50, bool))
    late["up"][35] = True                         # after both roots
    rec2 = N.score_pair(late, info, {}, dict(reach, up={"a", "b", "v"}))
    assert "a" in rec2["named"] and "b" in rec2["named"]
    # a root that alarmed before its own onset (set off by the other fault) does not pass
    early = dict(info, faults=[info["faults"][0], dict(info["faults"][1], onset_s=35.0)])
    assert not N.score_pair(al, early, {}, reach)["passed"]
    info["faults"][1] = {"fid": "F15", "root": "", "control": True, "onset_s": 25.0}
    assert N.score_pair(al, info, {}, reach)["roots"] == ["a"]


def test_simulate_multi_applies_both_faults(monkeypatch):
    """Two faults in one short run: each is injected at its own onset (the press friction and the
    plant air leak show in their plant parameters)."""
    monkeypatch.setattr(N, "WARMUP_S", 60.0)
    monkeypatch.setattr(N, "LEAD_S", 30.0)
    raw, info = N.simulate_multi(40003, 0.02, (("F1", 0.5, 10.0), ("F6", 0.5, 40.0)))
    assert info["fid"] == "F1+F6" and info["onset_s"] == 10.0 and info["lead"] == 30
    assert [f["fid"] for f in info["faults"]] == ["F1", "F6"] and all(f["magnitude"] > 0 for f in info["faults"])
    assert len(raw["press-1.oil_c"]) == 30 + 72


@pytest.mark.parametrize("fault", [("F6", 0.5, 60.0), ("F11", 0.9, 60.05), ("F3", 0.7, 60.0),
                                   (("F8", 0.5, 60.0), ("F13", 0.5, 90.0))])
def test_fault_run_equals_the_normal_run_before_its_onset(monkeypatch, fault):
    """Before its onset a fault run is the normal run of its seed, value for value: the range rule runs
    on a separate plant (2026-10-28). On the plant that runs, it moved the cooling pump's operating point
    and gave a start transient (LOG-125). A short run: 2 min warm-up, the onset 60 s after it."""
    monkeypatch.setattr(N, "WARMUP_S", 120.0)
    monkeypatch.setattr(N, "_VERSION", [])
    normal, _ = N.simulate(40000, 0.05)
    multi = isinstance(fault[0], tuple)
    raw, info = N.simulate_multi(40000, 0.05, fault) if multi else N.simulate(40000, 0.05, fault)
    before = info["lead"] + 60                                    # the samples before the first onset
    assert set(raw) == set(normal)
    for t, col in normal.items():
        assert [repr(v) for v in raw[t][:before]] == [repr(v) for v in col[:before]], t
    if fault[0] != "F3":                                          # (a jam acts only when the conveyor moves)
        assert any(raw[t][before + 30:] != normal[t][before + 30:] for t in normal)    # and the fault acts


@pytest.mark.parametrize("multi", [False, True])
def test_one_cycle_dip_shows_for_any_onset(monkeypatch, multi):
    """A level-0 F11 dip lasts one cycle (20 ms, IEC 61000-4-11 class 3). It shows in incomer-1.v_min_v,
    the lowest 11 kV voltage of the last second, in one poll, wherever its onset falls in a step of
    0.1 s: the plant steps to the clearing time (fault_step). Before 2026-10-28 it showed only when the
    onset fell less than 20 ms before a step."""
    monkeypatch.setattr(N, "WARMUP_S", 60.0)
    monkeypatch.setattr(N, "_VERSION", [])
    for frac in (0.0, 0.004, 0.019, 0.021, 0.05, 0.079, 0.081, 0.099):
        fault = ("F11", 0.9, 20.0 + frac)                      # severity 0.9: the level 0 pu
        raw, info = N.simulate_multi(40000, 0.01, (fault,)) if multi else N.simulate(40000, 0.01, fault)
        assert (info["faults"][0] if multi else info)["magnitude"] == 0.0
        v = np.asarray(raw["incomer-1.v_min_v"])
        low = np.flatnonzero(v < 0.5 * 11000.0)
        assert len(low) == 1 and info["lead"] + 20 <= low[0] <= info["lead"] + 21, (frac, low)
        assert v[low[0]] < 100.0 and min(raw["incomer-1.voltage_v"]) > 10_000.0   # the present value misses it


def test_fault_step_clears_inside_a_step():
    """fault_step: a fault goes on at the start of the first step at or after its onset and clears its
    duration later, inside the step by a sub-step; the plant clock stays on whole steps. A clearing time
    within FAULT_TOL_S of a step clears at that step (no sub-step)."""
    log = []

    class Grid:
        def dip(self, m):
            log.append(("dip", m))

        def clear(self):
            log.append(("clear",))

    class Plant:                                                  # a stand-in: the step and the grid only
        t, grid = 0.0, Grid()

        def step(self, dt):
            log.append(("step", dt))
            self.t += dt
    p = Plant()
    ends = {"F11": (1.0, 0.0, "IEC levels")}
    f = N.fault_plan("F11", 0.9, 0.25 - N.WARMUP_S, ends)        # level 0 pu, 1 cycle: on at 0.3 s
    g = N.fault_plan("F11", 0.6, 0.5 - N.WARMUP_S, ends)         # level 0.4 pu, 10 cycles: on at 0.5 s
    for _ in range(9):
        N.fault_step(p, [f, g], 0.1)
    assert f["on_t"] == pytest.approx(0.3) and f["off_t"] == pytest.approx(0.32) and f["state"] == "cleared"
    assert g["on_t"] == pytest.approx(0.5) and g["off_t"] == pytest.approx(0.7) and g["state"] == "cleared"
    kinds = [x[0] if x[0] != "step" else ("step" if abs(x[1] - 0.1) < 1e-9 else round(x[1], 6)) for x in log]
    assert kinds == ["step"] * 3 + ["dip", 0.02, "clear", 0.08] + ["step", "dip", "step", "step", "clear"] + ["step"] * 2
    assert p.t == sum([0.1] * 9)                                  # the clock of whole steps


def test_crossings_and_a_second_fault_on_an_alarmed_machine():
    """Sum 0 passes its limit early (the first fault); sum 1 starts at 60 and passes later (a second
    fault). crossings() reports both, each with its own change point; score_pair counts the second
    fault as detected on a machine that was already in alarm."""
    T = 200
    s0 = np.where(np.arange(T) >= 10, (np.arange(T) - 9) * 5.0, 0.0)
    s1 = np.where(np.arange(T) >= 60, (np.arange(T) - 59) * 5.0, 0.0)
    ev = N.crossings([(np.column_stack([s0, s1]), 100.0)], 0)
    assert ev == [(30, 10), (80, 60)]
    z = np.zeros(T, bool)
    al = {"m": z.copy()}
    al["m"][30:] = True
    info = {"fid": "Fa+Fb", "seed": 1, "onset_s": 10.0, "faults": [
        {"fid": "Fa", "root": "m", "control": False, "onset_s": 10.0},
        {"fid": "Fb", "root": "m2", "control": False, "onset_s": 58.0}]}
    asset = {"m": "m", "m2:x": "m2"}
    rec = N.score_pair(dict(al, **{"m2:x": z.copy()}), info, asset, {}, {"m": ev, "m2:x": []})
    assert rec["detected"] == {"m": True, "m2": False}
    info["faults"][1]["root"] = "m"
    rec = N.score_pair(al, info, {}, {}, {"m": ev})
    assert rec["detected"]["m"] is True


def test_masked_second_root_is_named_by_its_own_change():
    """R2 is downstream of R1, R1's fault sets R2 off at once (a victim), then R2's own fault starts
    later: its own change, with no upstream alarm before it, names R2 and the pair passes."""
    T = 400
    z = np.zeros(T, bool)
    al = {"r1": z.copy(), "r2": z.copy()}
    al["r1"][50:] = True
    al["r2"][60:] = True                          # set off by r1 within 120 s: a victim at first
    info = {"fid": "Fa+Fb", "seed": 1, "onset_s": 40.0, "faults": [
        {"fid": "Fa", "root": "r1", "control": False, "onset_s": 40.0},
        {"fid": "Fb", "root": "r2", "control": False, "onset_s": 300.0}]}
    reach = {"r1": {"r2"}, "r2": set()}
    rec = N.score_pair(al, info, {}, reach, {"r1": [(50, 41)], "r2": [(60, 52), (320, 305)]})
    assert "r2" in rec["named"] and rec["passed"]
    rec = N.score_pair(al, info, {}, reach, {"r1": [(50, 41)], "r2": [(60, 52)]})
    assert rec["named"] == ["r1"] and not rec["passed"]


def test_order_aware_graph_rule():
    """The root alarms first, an upstream machine (no cycle) alarms 30 s later: the plain graph rule
    drops the root, the order-aware one keeps it."""
    z = np.zeros(400, bool)
    al = {"root": z.copy(), "up": z.copy()}
    al["root"][100] = True
    al["up"][130] = True
    reach = {"up": {"root"}, "root": set()}
    rec = N.score_fault(al, {"onset_s": 50.0, "root": "root", "control": False}, None, {}, reach)
    assert rec["top1"] and not rec["top1_g120"] and rec["top1_g120o"]


def test_node_state_round_trip_and_the_twin_on_trained_nodes():
    """A trained node as plain data (state, from_state) predicts and alarms as before, after a pickle;
    in the twin, a reset of the upstream node removes its residual and the downstream node keeps its
    own (eval/cf.py, LOG-122)."""
    import pickle
    from twin.twin import Twin
    rng = np.random.default_rng(21)

    def run(n, bump=0.0):
        u = rng.normal(size=n)
        ya = 2.0 * u + 0.1 * rng.normal(size=n)
        ya[n // 2:] += bump
        yb = 3.0 * ya + 0.1 * rng.normal(size=n)
        return N.Run({"site.u": u, "a.y": ya, "b.y": yb}, lead=20)
    da = {"feeds": ["site.u"], "commands": [], "measures": ["a.y"], "asset": "a", "loop_outputs": []}
    db = {"feeds": ["a.y"], "commands": [], "measures": ["b.y"], "asset": "b", "loop_outputs": []}
    fit, cal = [(i, run(600)) for i in range(3)], [(9, run(600)), (10, run(600))]
    nodes = {"a": N.Node("a", da).train(fit, cal), "b": N.Node("b", db).train(fit, cal)}
    r = run(600, bump=1.0)
    back = N.Node.from_state(pickle.loads(pickle.dumps(nodes["a"].state())))
    assert np.allclose(back.predict(r)[0], nodes["a"].predict(r)[0])
    assert (back.alarms(r) == nodes["a"].alarms(r)).all()
    tw = Twin(nodes, {"a": da, "b": db}, {"site.u", "a.y", "b.y"})
    base, meas = tw.base(r), tw.measured(r)
    vals, _ = tw.replay(r, 0, reset=["a"])
    assert np.allclose(vals["a"], base["a"])                              # a at its learned normal
    # b keeps its own residual and loses the bump that a carried into it
    assert np.allclose(vals["b"] - tw.nodes["b"].predict(N.Run({**r, "a.y": np.r_[r["a.y"][:20], base["a"][:, 0]]}, 20))[0],
                       meas["b"] - base["b"])
    assert abs(float(np.mean(vals["b"][300:] - meas["b"][300:]))) > 1.0


def test_cf_targets_and_scores():
    """The counterfactual rule: the targets are the machines whose first alarm or new crossing lies in
    the window's last seconds, and a suspect scores the trouble its reset removes at them."""
    from eval import cf as CF
    rec = {"hit": {"a": 100, "b": 150, "c": 400}, "cross": {"d": [130], "c": [900]}}
    assert CF.targets_at(rec, 100, 120) == {"a", "b", "d"}
    win = {"d": {"a": {"a": np.array([4.0, 2.0]), "b": np.array([6.0])}, "b": {"b": np.array([1.0])},
                 "c": {"c": np.array([50.0])}}}
    sig2 = {"a": np.array([1.0, 2.0]), "b": np.array([2.0]), "c": np.array([1.0])}
    sc = CF.target_scores(win, sig2, {"a", "b"}, {"a": "a", "b": "b", "c": "c"})
    assert sc == {"a": 4.0 + 1.0 + 3.0, "b": 0.5, "c": 0.0}


def _counter_runs(seed=31):
    """A machine like pierce-1: a PLC hit counter steps by one each second, except in the second of the
    stroke (every 6 s), where the pressure peaks."""
    rng = np.random.default_rng(seed)

    def run(n):
        phase = (np.arange(n) + rng.integers(6)) % 6
        stroke = phase == 0
        count = np.cumsum(~stroke).astype(float) + rng.integers(10_000, 20_000)
        p = 10.0 + 140.0 * stroke + 0.5 * rng.normal(size=n)
        return N.Run({"m.v": 400.0 + rng.normal(size=n), "m.count": count, "m.p": p}, lead=30)
    return run


def test_command_changes_carry_the_cycle():
    """With cmd_delta a numeric command also enters as its change since the last sample: the counter's
    pause marks the stroke, which its level and its filters do not resolve (2026-10-22)."""
    run = _counter_runs()
    doc = {"feeds": ["m.v"], "commands": ["m.count"], "measures": ["m.p"], "asset": "m", "loop_outputs": []}
    fit, cal = [(i, run(1200)) for i in range(3)], [(9, run(1200)), (10, run(1200))]
    plain = N.Node("m", doc, "cusumk").train(fit, cal)
    nd = N.Node("m", doc, "cusumk")
    nd.cmd_delta = True
    nd.train(fit, cal)
    assert nd.delta_in == ["m.count"] and nd.model.inputs[-1] == "d:m.count"
    r = run(50)
    U, _ = nd.matrices(r)
    assert U[0, -1] == 0.0 and np.array_equal(U[1:, -1], np.diff(r["m.count"]))
    assert nd.r2[0] > 0.95 > plain.r2[0]
    back = N.Node.from_state(nd.state())
    assert back.delta_in == ["m.count"] and (back.alarms(r) == nd.alarms(r)).all()
    assert nd.coverage_columns(["m.v", "d:m.v"], 1) == [True, True, True, True]
    own = N.Node("m", dict(doc, loop_outputs=["m.count"]))
    assert own.coverage_columns(["m.count", "d:m.count"], 1) == [False, False, False, False]


def test_counter_reset_enters_as_the_new_value():
    """A counter that steps down was reset (a new electrode): its change is the new value, not the drop.
    A node saved before the reset rule keeps the plain change (LOG-128)."""
    doc = {"feeds": ["m.v"], "commands": ["m.count"], "measures": ["m.p"], "asset": "m", "loop_outputs": []}
    nd = N.Node("m", doc, "cusumk")
    nd.num_in, nd.delta_in = ["m.v"], ["m.count"]
    r = N.Run({"m.v": np.zeros(7), "m.count": np.array([1366.0, 1367, 1368, 0, 1, 1, 2]), "m.p": np.zeros(7)})
    U, _ = nd.matrices(r)
    assert U[:, -1].tolist() == [0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    r["m.count"][3] = 1.0                                        # a start in the sample of the change
    assert nd.matrices(r)[0][3, -1] == 1.0
    old = N.Node.__new__(N.Node)
    old.__dict__.update({k: v for k, v in nd.__dict__.items() if k != "counter_resets"})
    assert old.matrices(r)[0][:, -1].tolist() == [0.0, 1.0, 1.0, -1367.0, 0.0, 0.0, 1.0]


def test_is_counter():
    """A counter steps up by whole numbers (a rare reset allowed); an on/off command and a setpoint
    are not counters (E1, LOG-128)."""
    count = np.r_[np.arange(5000, 7000), np.arange(0, 3000)]          # one reset (a new electrode)
    assert N.is_counter([count.astype(float)])
    assert not N.is_counter([np.tile([0.0, 0.0, 1.0, 1.0], 500)])      # a valve that opens and closes
    assert not N.is_counter([np.r_[np.full(500, 250.0), np.full(500, 300.0), np.full(500, 0.0)]])  # a setpoint
    assert not N.is_counter([np.linspace(0.0, 10.0, 100)])               # not whole numbers
