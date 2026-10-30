"""Checks of live/engine.py: a trained node, one sample at a time, alarms as `Node.alarms` does on the
whole run, with the same change points; the incidents open, take candidates and victims and close as
documented; the kept samples give back the run. `python -m live.engine check` repeats the first check
on the saved runs of a campaign."""

import numpy as np
import pytest

from live import engine as E

N = E.N


def _runs(seed=3):
    rng = np.random.default_rng(seed)

    def run(n, bump=0.0, at=None, gap=None):
        u = rng.normal(size=n)
        st = np.where(np.arange(n) % 300 < 200, "run", "idle")
        y = 2.0 * u + np.where(st == "run", 1.0, 0.0) + 0.1 * rng.normal(size=n)
        if at is not None:
            y[at:] += bump
        if gap is not None:
            y[gap:gap + 30] = np.nan                    # a gap in the meter: the sums hold
        mode = np.where(y > 0.5, "hi", "lo")
        return N.Run({"site.u": u, "a.state": st, "a.y": y, "a.mode": mode}, lead=60)
    return run


DOC = {"feeds": ["site.u"], "commands": ["a.state"], "measures": ["a.y", "a.mode"], "asset": "a", "loop_outputs": []}


@pytest.fixture(scope="module")
def trained():
    run = _runs()
    fit, cal = [(i, run(1500)) for i in range(3)], [(9, run(1500)), (10, run(1500))]
    return {rule: N.Node("a", DOC, rule).train(fit, cal) for rule in ("cusumkv", "cusumk")}, run


@pytest.mark.parametrize("rule,confirm_s", [("cusumkv", 0.0), ("cusumk", 0.0), ("cusumkv", 5.0)])
def test_stream_node_equals_batch(trained, rule, confirm_s):
    nodes, run = trained
    nd = nodes[rule]
    nd.confirm_s = confirm_s
    try:
        r = run(2400, bump=0.4, at=1500, gap=900)
        alarm = nd.alarms(r)
        z = nd.z(r)[0]
        nd.alarms(r)                                    # leaves the batch sums in nd._stats
        sn = E.StreamNode(nd)
        sn.reset(r.lead)
        got, zs, cps = [], [], {}
        for s in E.samples_of(r):
            a = sn.step(s)
            if a is None:
                continue
            got.append(a)
            zs.append(sn.z)
            if a and (not got[:-1] or not got[-2]):
                cps[sn.t] = sn.change_point()
        assert np.array_equal(np.array(got), alarm)
        assert alarm[1500 - r.lead:].any()              # the bump alarms
        np.testing.assert_allclose(np.array(zs), z, rtol=0, atol=1e-9)
        assert cps and all(c == N.change_point(nd._stats, t) for t, c in cps.items())
    finally:
        nd.confirm_s = 0.0


class _Scripted:
    """A node whose alarm follows a script: on in the given [start, end) samples, after the lead."""

    def __init__(self, spans, lead=0):
        self.spans, self.lead, self.k, self.t, self.alarm = spans, lead, 0, -1, False

    def reset(self, lead):
        self.lead, self.k, self.t = lead, 0, -1

    def step(self, s):
        self.k += 1
        if self.k <= self.lead:
            return None
        self.t += 1
        self.alarm = any(a <= self.t < b for a, b in self.spans)
        return self.alarm

    def change_point(self):
        return self.t


def test_incidents_candidates_victims_and_new_roots():
    """b is downstream of a in the documents, c stands alone. Within 120 s of a's first alarm, b is a
    candidate and the graph rule names a. Later b alone opens its own incident, and c joins it inside
    its 120 s. A b alarm while a alarms, after a's 120 s, is a victim of a's incident. c then, with no
    upstream machine in alarm, opens a new incident."""
    w = {"a": {"feeds": ["site.u"], "commands": [], "measures": ["a.y"], "asset": "a"},
         "b": {"feeds": ["a.y"], "commands": [], "measures": ["b.y"], "asset": "b"},
         "c": {"feeds": ["site.u"], "commands": [], "measures": ["c.y"], "asset": "c"}}
    eng = E.Engine({}, w)
    eng.sn = {"a": _Scripted([(100, 400), (3000, 3500)]), "b": _Scripted([(150, 450), (1500, 1600), (3200, 3300)]),
              "c": _Scripted([(1550, 1560), (3250, 3260)])}
    eng.asset = {"a": "a", "b": "b", "c": "c"}
    eng.start(10)
    for _ in range(10 + 5000):
        eng.step({})
    got = [(i["t0"], i["named"], sorted(i["hit"]), dict(i["victims"]), i["closed"]) for i in eng.incidents]
    # an incident closes 600 s after the last sample at which one of its machines alarmed
    assert got == [(100, "a", ["a", "b"], {}, 449 + 600),
                   (1500, "b", ["b", "c"], {}, 1599 + 600),
                   (3000, "a", ["a"], {"b": 3200}, 3499 + 600),
                   (3250, "c", ["c"], {}, 3259 + 600)]
    assert eng.incidents[0]["named_graph"] == ["a"] and eng.incidents[0]["named_cf"] is None


def test_same_root_merges_and_cycle_victims():
    """A later incident that the counterfactual explains by the root of an older open incident joins it
    (its machines become victims, the record keeps `merged_into`). A machine in a cycle of the documents
    with a machine that alarms in an open incident is a victim of that incident, not a new root."""
    w = {"a": {"feeds": ["site.u"], "commands": [], "measures": ["a.y"], "asset": "a"},
         "c": {"feeds": ["site.u"], "commands": [], "measures": ["c.y"], "asset": "c"}}
    eng = E.Engine({}, w, sig2={})
    eng.sn = {"a": _Scripted([(100, 3000)]), "c": _Scripted([(1500, 1600)])}
    eng.asset = {"a": "a", "c": "c"}
    eng.counterfactual = lambda t0, targets: ("a", {"a": 1.0})        # the twin explains c by a
    eng.start(0)
    for _ in range(2000):
        eng.step({})
    one, two = eng.incidents
    assert one["named"] == "a" and two["merged_into"] == 1 and two["closed"] == 1620 and one["victims"] == {"c": 1500}
    assert eng.inc_of["c"] is one and one["closed"] is None
    w = {"x": {"feeds": ["y.out"], "commands": [], "measures": ["x.out"], "asset": "x"},
         "y": {"feeds": ["x.out"], "commands": [], "measures": ["y.out"], "asset": "y"}}
    eng = E.Engine({}, w)
    eng.sn = {"x": _Scripted([(100, 2000)]), "y": _Scripted([(1000, 1100)])}
    eng.asset = {"x": "x", "y": "y"}
    eng.start(0)
    for _ in range(1500):
        eng.step({})
    assert len(eng.incidents) == 1 and eng.incidents[0]["victims"] == {"y": 1000}


def test_kept_samples_give_back_the_run(trained):
    _, run = trained
    r = run(500)
    eng = E.Engine({}, {"a": DOC})
    eng.start(r.lead)
    for s in E.samples_of(r):
        eng.step(s)
    back, off = eng.run_so_far()
    assert off == 0 and back.lead == r.lead and set(back) == set(r)
    for t in r:
        if np.asarray(r[t]).dtype.kind in "U":
            assert isinstance(back[t], N.Text) and list(back[t].decode()) == list(r[t])
        else:
            assert np.array_equal(back[t], r[t], equal_nan=True)
    small = E.Engine({}, {"a": DOC}, keep_s=200.0)      # past the cap: the oldest samples go
    small.start(r.lead)
    for s in E.samples_of(r):
        small.step(s)
    back, off = small.run_so_far()
    assert len(back["a.y"]) == 200 and off == (500 - 200) + int(N.LEAD_S) - r.lead


def test_stream_node_with_command_changes():
    """A node with cmd_delta (a numeric command also enters as its change): the stream takes the change
    from its own last value, a step down as a reset, and alarms as the batch does."""
    from eval.tests.test_nodes import _counter_runs
    run = _counter_runs(7)
    doc = {"feeds": ["m.v"], "commands": ["m.count"], "measures": ["m.p"], "asset": "m", "loop_outputs": []}
    nd = N.Node("m", doc, "cusumkv")
    nd.cmd_delta = True
    nd.train([(i, run(1200)) for i in range(3)], [(9, run(1200)), (10, run(1200))])
    r = run(1500)
    r["m.p"] = r["m.p"] + np.where(np.arange(1500) >= 900, 3.0, 0.0)      # a pressure offset from sample 900
    r["m.count"][1300:] -= r["m.count"][1300]                              # the counter resets at sample 1300
    d = E.StreamNode(nd)
    assert np.array_equal([d._uy(s)[0][-1] for s in E.samples_of(r)], nd.matrices(r)[0][:, -1])
    alarm = nd.alarms(r)
    sn = E.StreamNode(nd)
    sn.reset(r.lead)
    got = [a for a in (sn.step(s) for s in E.samples_of(r)) if a is not None]
    assert np.array_equal(np.array(got), alarm) and alarm[900 - r.lead:].any()


def test_rules_without_a_stream_are_refused(trained):
    nodes, _ = trained
    nd = N.Node.from_state(nodes["cusumk"].state())
    nd.rule = "gate"
    with pytest.raises(ValueError):
        E.StreamNode(nd)
