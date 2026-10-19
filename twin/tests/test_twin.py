"""The twin (twin/twin.py) on small linear nodes with a known answer."""
import numpy as np

from twin.twin import Twin, scc_order


class R(dict):
    def __init__(self, cols, lead=0):
        super().__init__(cols)
        self.lead = lead


class Lin:
    """y = sum of gain * input, as a trained node gives it (predict, matrices, out_names, doc)."""

    def __init__(self, gains, out):
        self.gains, self.out_names = gains, [out]
        self.doc = {"feeds": list(gains), "commands": [], "measures": [out], "asset": out.split(".")[0]}

    def predict(self, r):
        p = sum(g * np.asarray(r[t], float)[r.lead:] for t, g in self.gains.items())
        return p[:, None], np.ones(len(p), bool)

    def matrices(self, r):
        U = np.column_stack([np.asarray(r[t], float) for t in self.gains])
        return U, np.asarray(r[self.out_names[0]], float)[:, None]


def chain(rng, lead=0):
    x = rng.normal(size=200 + lead)
    na, nb = rng.normal(size=200 + lead), rng.normal(size=200 + lead)
    ya = 2 * x + na
    yb = 3 * ya + nb
    nodes = {"a": Lin({"site.x": 2.0}, "a.y"), "b": Lin({"a.y": 3.0}, "b.y")}
    w = {n: dict(nd.doc) for n, nd in nodes.items()}
    return nodes, w, R({"site.x": x, "a.y": ya, "b.y": yb}, lead), (x, na, nb)


def test_free_replay_chains_the_predictions():
    nodes, w, run, (x, _, _) = chain(np.random.default_rng(1))
    tw = Twin(nodes, w, {"site.x", "a.y", "b.y"})
    assert [e["basis"] for e in tw.edges] == ["documented"]
    vals, _ = tw.replay(run, 50, free=True)
    assert np.allclose(vals["a"][50:, 0], 2 * x[50:])
    assert np.allclose(vals["b"][50:, 0], 6 * x[50:])
    assert np.allclose(vals["b"][:50, 0], run["b.y"][:50])       # measured before the start


def test_reset_keeps_the_downstream_residual():
    nodes, w, run, (x, na, nb) = chain(np.random.default_rng(2), lead=10)
    tw = Twin(nodes, w, {"site.x", "a.y", "b.y"})
    vals, _ = tw.replay(run, 0, reset=["a"])
    assert np.allclose(vals["a"][:, 0], 2 * x[10:])               # a at its learned normal
    assert np.allclose(vals["b"][:, 0], 6 * x[10:] + nb[10:])     # b keeps its own residual
    vals, _ = tw.replay(run, 0, reset=["b"])
    assert np.allclose(vals["a"][:, 0], run["a.y"][10:])         # upstream of the reset: measured


def test_a_cycle_converges_to_its_fixed_point():
    rng = np.random.default_rng(3)
    x = rng.normal(size=100)
    nodes = {"a": Lin({"site.x": 1.0, "b.y": 0.5}, "a.y"), "b": Lin({"a.y": 0.5}, "b.y")}
    w = {n: dict(nd.doc) for n, nd in nodes.items()}
    run = R({"site.x": x, "a.y": rng.normal(size=100), "b.y": rng.normal(size=100)})
    tw = Twin(nodes, w, {"site.x", "a.y", "b.y"})
    assert tw.order == [["a", "b"]]
    vals, last = tw.replay(run, 0, free=True, passes=40)
    assert np.allclose(vals["a"][:, 0], x / 0.75, atol=1e-6)
    assert last < 1e-6


def test_commands_text_tags_and_loop_outputs_are_not_chained():
    nodes = {"a": Lin({"site.x": 1.0}, "a.y"), "b": Lin({"a.y": 1.0}, "b.y")}
    w = {n: dict(nd.doc) for n, nd in nodes.items()}
    assert not Twin(nodes, w, {"site.x"}).edges[0]["chained"]                  # a.y is text
    w["a"]["loop_outputs"] = ["a.y"]
    assert not Twin(nodes, w, {"site.x", "a.y"}).edges[0]["chained"]           # a controller output


def test_scc_order_is_topological():
    succ = {"a": {"b"}, "b": {"c"}, "c": {"b", "d"}, "d": set(), "e": {"a"}}
    assert scc_order(succ) == [["e"], ["a"], ["b", "c"], ["d"]]
