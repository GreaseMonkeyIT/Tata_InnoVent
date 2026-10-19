"""The twin (ideas.md 12.3, step 8): the node models joined by the plant's documents.

Each node's model (twin/node_model.py, trained by eval/nodes.py) predicts the node's measures from
its inputs. In the twin, an input that another node measures can come from that node's prediction
instead of the meter, so the output of one model is the input of the next. Example: incomer-1 gives
the rail voltage psu-a.voltage_v, and press-1 takes it.

The graph comes from the documents (plant/model/wiring.py): an edge A -> B for each tag that A
measures and B takes as a feed. Each edge has a basis (ideas.md 18.3). Every edge of this version is
"documented". An edge carries a predicted value ("chained") when its tag is a number and not an
output of the source asset's own PLC control loops. PLC commands, controller outputs and the tags
that no node measures (the site load, the weather) stay as measured: they are the twin's exogenous
inputs.

Time: every model is causal (its features are the present inputs and their filtered past), so a
replay runs forward from measured history. A cycle of chained edges (the cooling water loop:
compressor-1 returns warmer water to cool-1, and cool-1 supplies it) is solved by repeated passes over
the replay (waveform relaxation: Lelarasmee, Ruehli and Sangiovanni-Vincentelli, IEEE Trans.
Computer-Aided Design 1(3), 1982). Each pass takes the values of the previous pass on the cycle's
edges.

Two replays (`Twin.replay`):
  - free: from `start` on, every value is the model's prediction. This is the twin's normal plant for
    the measured exogenous inputs.
  - reset: from `start` on, the nodes in `reset` take their predictions (their own residual set to 0,
    their learned normal). Every node downstream of them keeps its own residual and takes the change
    of its inputs through its model. This is a counterfactual in three steps: abduction (the
    residuals), action (the reset), prediction (Pearl, Causality, 2nd ed. 2009, section 7.1). The
    other nodes keep their measured values.
"""
import numpy as np


class Twin:
    def __init__(self, nodes, w, numeric):
        """nodes: {name: a trained node with predict(run) -> (T x outputs, covered), matrices(run) ->
        (U, Y) and out_names}; w: the documents; numeric: the tags whose values are numbers."""
        self.nodes, self.w = nodes, w
        meas_of = {}
        for n, d in w.items():
            for t in d["measures"]:
                if t in meas_of:
                    raise ValueError(f"{t} is measured by {meas_of[t]} and {n}")
                meas_of[t] = n
        self.edges = []
        for n, d in w.items():
            for t in d["feeds"] + d["commands"]:
                src = meas_of.get(t)
                if src is None or src == n:
                    continue
                own = set(w[src].get("loop_outputs", []))
                self.edges.append({"src": src, "dst": n, "tag": t, "basis": "documented",
                                   "chained": t in numeric and t not in own and t not in d["commands"]})
        self.chained = {e["tag"]: e["src"] for e in self.edges if e["chained"]}
        self.succ = {n: set() for n in w}
        self.pred = {n: set() for n in w}
        for e in self.edges:
            if e["chained"]:
                self.succ[e["src"]].add(e["dst"])
                self.pred[e["dst"]].add(e["src"])
        self.order = scc_order(self.succ)
        # the output columns of each node that other nodes read as chained inputs
        self.writes = {n: [(t, self.nodes[n].out_names.index(t)) for t, s in self.chained.items() if s == n]
                       for n in w}

    def affected(self, reset):
        """The nodes in `reset` and every node downstream of them along chained edges."""
        seen, todo = set(reset), list(reset)
        while todo:
            for b in self.succ[todo.pop()]:
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
        return seen

    def measured(self, run):
        """Each node's measured outputs after the lead (T x outputs)."""
        return {n: self.nodes[n].matrices(run)[1][run.lead:] for n in self.w}

    def base(self, run, with_cover=False):
        """Each node's prediction from the measured inputs (T x outputs, after the lead); with_cover:
        also each node's covered mask (T: its inputs inside their training range)."""
        got = {n: self.nodes[n].predict(run) for n in self.w}
        pred = {n: p for n, (p, _) in got.items()}
        return (pred, {n: c for n, (_, c) in got.items()}) if with_cover else pred

    def replay(self, run, start, reset=(), free=False, base=None, meas=None, passes=4):
        """The twin's values of every node's outputs (T x outputs, after the lead): measured before
        sample `start` (counted after the lead), replayed from it on. Returns ({node: values}, the
        largest change of a cycle's values in its last pass, 0 when no cycle is replayed)."""
        base = base if base is not None else self.base(run)
        meas = meas if meas is not None else self.measured(run)
        lead = run.lead
        hit = set(self.w) if free else self.affected(reset)
        cur = type(run)(dict(run), lead)
        vals = dict(meas)
        changed = set()                 # nodes whose chained outputs differ from the measured ones
        last = 0.0
        for comp in self.order:
            todo = [n for n in comp if n in hit]
            if not todo:
                continue
            cyc = len(comp) > 1
            for _ in range(passes if cyc else 1):
                change = 0.0
                for n in todo:
                    # a model whose inputs did not change gives its prediction from the meters
                    p = self.nodes[n].predict(cur)[0] if self.pred[n] & changed else base[n]
                    v = p if (free or n in reset) else meas[n] + (p - base[n])
                    v = np.array(v, float, copy=True)
                    v[:start] = meas[n][:start]
                    if cyc:
                        change = max(change, float(np.nanmax(np.abs(v - vals[n]))) if v.size else 0.0)
                    vals[n] = v
                    if self.writes[n]:
                        changed.add(n)
                    for t, j in self.writes[n]:
                        col = np.asarray(run[t], float).copy()
                        col[lead:] = v[:, j]
                        cur[t] = col
            if cyc:
                last = max(last, change)
        return vals, last


def scc_order(succ):
    """The strongly connected components of a directed graph {node: successors}, in topological order
    (a component comes before every component it has an edge to). Tarjan's algorithm (SIAM J.
    Computing 1(2), 1972), which gives them in reverse topological order."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for u in sorted(succ[v]):
            if u not in index:
                visit(u)
                low[v] = min(low[v], low[u])
            elif u in on:
                low[v] = min(low[v], index[u])
        if low[v] == index[v]:
            comp = []
            while True:
                u = stack.pop()
                on.discard(u)
                comp.append(u)
                if u == v:
                    break
            out.append(sorted(comp))

    for v in sorted(succ):
        if v not in index:
            visit(v)
    return out[::-1]
