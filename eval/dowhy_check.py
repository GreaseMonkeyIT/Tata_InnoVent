#!/usr/bin/env python3
"""The reference check of the counterfactual root (eval/cf.py) against DoWhy-GCM (ideas.md 12.3 step 10).

DoWhy-GCM (Bloebaum, Goetz, Budhathoki, Mastakouri and Janzing, JMLR 25, 2024) is the reference
implementation of the root cause method of Budhathoki et al., ICML 2022. Its `gcm.attribute_anomalies`
gives each node the Shapley value of its noise on the information-theoretic outlier score of a target
node. Our rule (eval/cf.py) asks a simpler question with the same parts (a mechanism and a noise per
node): which node's noise, set to 0, removes the most trouble at the alarmed nodes.

Two parts:

  synthetic  Random structural causal models with 8 nodes and one outlier in the noise of a known
             node. Both methods use the same mechanisms, fitted by DoWhy. Ours is the static form of
             eval/cf.py. DoWhy's root is the node with the largest attribution summed over the alarmed
             nodes. This checks the logic of our rule against the reference where both apply.

  plant      DoWhy-GCM as it is published (one sample, no time) on the validation fault runs of a
             campaign (eval/cache/dowhy: the views, the manifest and models.pkl, copied from forge).
             A graph of tags from the documents: each machine node's inputs -> its measures. The PLC
             commands and the controller outputs are root nodes (as in twin/twin.py). The cooling water
             cycle is cut at the return from compressor-1 to cool-1 (a DAG has no cycle; the twin solves
             it). The mechanisms are fitted by DoWhy (a gradient-boosting regressor with additive noise)
             on the 24 fit days, one sample every 30 s. Per run: the sample in the 120 s after the
             first alarm where a target machine's tag deviates the most, one target tag per machine that
             alarms within 120 s. Next to it, the static form of our rule on the same mechanisms, and
             our dynamic counterfactual (cf-c3) and the graph rule from the manifest.

Run with the DoWhy venv (DoWhy is not in .venv, it needs scipy 1.15.3):

    %USERPROFILE%/venvs/visr-dowhy/Scripts/python.exe -m eval.dowhy_check synthetic
    %USERPROFILE%/venvs/visr-dowhy/Scripts/python.exe -m eval.dowhy_check plant --data eval/cache/dowhy
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import sys
import time
import warnings

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
warnings.filterwarnings("ignore")

import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from dowhy import gcm  # noqa: E402
from dowhy.graph import get_ordered_predecessors  # noqa: E402
from dowhy.gcm.shapley import ShapleyApproximationMethods, ShapleyConfig  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

Z_ALARM = 3.0          # synthetic: a node alarms when its value is more than 3 spreads from its mean
NDS = 500              # samples per Shapley evaluation (DoWhy default 3000; 500 for speed)
SHAPLEY = dict(approximation_method=ShapleyApproximationMethods.PERMUTATION, num_permutations=25, n_jobs=1)


def _quiet():
    gcm.config.disable_progress_bars()
    warnings.filterwarnings("ignore")


# ------------------------------------------------------------ the static form of our rule --
def _predict(model, node, vals):
    pa = get_ordered_predecessors(model.graph, node)
    return model.causal_mechanism(node).prediction_model.predict(np.column_stack([vals[p] for p in pa])).ravel()


def replay(model, order, obs, reset=(), free=False, med=None, free_roots=False):
    """The values of every node: free (every non-root noise 0, the roots as observed, or at their
    training median with free_roots) or with the nodes in `reset` at their normal (a non-root's noise
    0, a root at its training median) and every other node keeping its own noise (abduction from
    `obs`). obs: {node: array}."""
    roots = {n for n in order if not list(model.graph.predecessors(n))}
    noise = {n: obs[n] - _predict(model, n, obs) for n in order if n not in roots}
    x = {}
    for n in order:
        if n in roots:
            at_median = (n in reset or (free and free_roots)) and med is not None
            x[n] = np.full_like(obs[n], med[n]) if at_median else obs[n]
        else:
            x[n] = _predict(model, n, x) + (0.0 if (free or n in reset) else noise[n])
    return x


def removed_trouble(model, order, obs, targets, groups, sig2, med, free_roots=False):
    """{group: the trouble that resetting its nodes removes at the target nodes}: our rule (eval/cf.py)
    on a static model. groups: {name: [nodes]}. free_roots: the root nodes are modelled nodes with a
    noise of their own (the synthetic models), so the normal plant has them at their median. In the
    plant they are exogenous (PLC commands, controller outputs, the weather) and stay as measured, as
    in twin/twin.py."""
    free = replay(model, order, obs, free=True, med=med, free_roots=free_roots)
    e0 = {t: (obs[t] - free[t]) ** 2 / sig2[t] for t in targets}
    out = {}
    for g, nodes in groups.items():
        x = replay(model, order, obs, reset=set(nodes), med=med)
        out[g] = float(sum(np.sum(e0[t] - (x[t] - free[t]) ** 2 / sig2[t]) for t in targets))
    return out


def free_variance(model, order, data, free_roots=False):
    obs = {n: data[n].to_numpy(float) for n in order}
    med = {n: float(np.median(obs[n])) for n in order}
    free = replay(model, order, obs, free=True, med=med, free_roots=free_roots)
    return {n: max(float(np.var(obs[n] - free[n])), 1e-12) for n in order}, med


def dowhy_scores(model, targets, row, groups_of):
    """{group: DoWhy's attribution summed over the target nodes}. groups_of: node -> group (None:
    exogenous, not a suspect)."""
    out = {}
    for t in targets:
        att = gcm.attribute_anomalies(model, t, anomaly_samples=row, num_distribution_samples=NDS,
                                      shapley_config=ShapleyConfig(**SHAPLEY))
        for n, v in att.items():
            g = groups_of.get(n)
            if g is not None:
                out[g] = out.get(g, 0.0) + float(np.asarray(v).ravel()[0])
    return out


def fit_model(G, data, regressor):
    m = gcm.InvertibleStructuralCausalModel(G)
    for n in G.nodes:
        if list(G.predecessors(n)):
            m.set_causal_mechanism(n, gcm.AdditiveNoiseModel(regressor()))
        else:
            m.set_causal_mechanism(n, gcm.EmpiricalDistribution())
    gcm.fit(m, data)
    return m


# ------------------------------------------------------------------------- synthetic --
def synthetic_trial(seed, kind, n=8, m=3000):
    _quiet()
    rng = np.random.default_rng(seed)
    parents = {0: []}
    for j in range(1, n):
        parents[j] = sorted(rng.choice(j, size=int(rng.integers(1, min(2, j) + 1)), replace=False).tolist())
    coef = {j: rng.uniform(0.5, 1.5, len(p)) * rng.choice([-1.0, 1.0], len(p)) for j, p in parents.items()}
    sig = rng.uniform(0.5, 1.5, n)

    def draw(N):
        X = {}
        for j in range(n):
            s = sum(c * X[f"x{p}"] for c, p in zip(coef[j], parents[j])) if parents[j] else 0.0
            if kind == "nonlinear" and parents[j]:
                s = 1.5 * np.tanh(s) + 0.5 * s
            X[f"x{j}"] = s + N[:, j]
        return X

    data = pd.DataFrame(draw(rng.normal(size=(m, n)) * sig))
    G = nx.DiGraph([(f"x{p}", f"x{j}") for j, ps in parents.items() for p in ps])
    G.add_nodes_from(data.columns)
    reg = gcm.ml.create_linear_regressor if kind == "linear" else gcm.ml.create_hist_gradient_boost_regressor
    model = fit_model(G, data, reg)
    order = list(nx.topological_sort(G))
    root = int(rng.integers(n))
    N = rng.normal(size=(1, n)) * sig
    N[0, root] = rng.choice([-1.0, 1.0]) * rng.uniform(4.0, 6.0) * sig[root]
    row = pd.DataFrame({k: v for k, v in draw(N).items()})
    z = {c: abs(float(row[c].iloc[0]) - data[c].mean()) / data[c].std() for c in data.columns}
    targets = [c for c in order if z[c] > Z_ALARM]
    if not targets:
        return {"seed": seed, "kind": kind, "root": f"x{root}", "skipped": True}
    sig2, med = free_variance(model, order, data, free_roots=True)
    obs = {c: row[c].to_numpy(float) for c in order}
    groups = {c: [c] for c in order}
    ours = removed_trouble(model, order, obs, targets, groups, sig2, med, free_roots=True)
    dw = dowhy_scores(model, targets, row, {c: c for c in order})
    return {"seed": seed, "kind": kind, "root": f"x{root}", "targets": targets,
            "ours": max(ours, key=ours.get), "dowhy": max(dw, key=dw.get), "skipped": False}


def run_synthetic(a):
    jobs = [(10_000 + i, k) for k in ("linear", "nonlinear") for i in range(a.trials)]
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(synthetic_trial, *zip(*jobs)))
    out = {}
    for k in ("linear", "nonlinear"):
        rs = [r for r in res if r["kind"] == k and not r["skipped"]]
        out[k] = {"trials": len([r for r in res if r["kind"] == k]), "scored": len(rs),
                  "ours": sum(r["ours"] == r["root"] for r in rs), "dowhy": sum(r["dowhy"] == r["root"] for r in rs),
                  "agree": sum(r["ours"] == r["dowhy"] for r in rs)}
    return {"part": "synthetic", "summary": out, "trials": res}


# ----------------------------------------------------------------------------- plant --
_P = {}


def plant_graph(w):
    """The DAG of tags from the documents, the cut edges, and tag -> machine."""
    edges = set()
    for n, d in w.items():
        if n.endswith(":loop"):
            continue                    # controller outputs are root nodes (as in twin/twin.py)
        for u in d["feeds"] + d["commands"]:
            for y in d["measures"]:
                if u != y:
                    edges.add((u, y))
    asset = {t: d["asset"] for n, d in w.items() for t in d["measures"]}
    cut = {(u, y) for u, y in edges if asset.get(u) == "compressor-1" and asset.get(y) == "cool-1"}
    G = nx.DiGraph(sorted(edges - cut))
    while not nx.is_directed_acyclic_graph(G):
        u, y = nx.find_cycle(G)[-1][:2]
        G.remove_edge(u, y)
        cut.add((u, y))
    return G, sorted(cut), asset


def frame(run, tags, text_cats, rows):
    """Numeric columns at the sample indices `rows` (after the lead); a text tag as one column per
    category ("tag=cat", 1 where it holds)."""
    from eval.nodes import Text, text_eq
    out = {}
    for t in tags:
        v = run[t]
        if isinstance(v, Text) or t in text_cats:
            for c in text_cats[t]:
                out[f"{t}={c}"] = text_eq(v, c)[run.lead:][rows].astype(float)
        else:
            out[t] = np.asarray(v, float)[run.lead:][rows]
    return pd.DataFrame(out)


def _plant_init(state_path):
    import pickle
    _quiet()
    with open(state_path, "rb") as fh:
        _P.update(pickle.load(fh))


def plant_run(r, data_dir):
    from eval.nodes import load_view
    P = _P
    model, order, G = P["model"], P["order"], P["G"]
    run = load_view(os.path.join(data_dir, "views", r["file"]), P["tags"])
    hit = r["hit"]
    t0 = min(hit.values())
    cands = sorted(m for m, t in hit.items() if t <= t0 + 120)
    rows = np.arange(t0, t0 + 121)
    rows = rows[rows < len(next(iter(run.values()))) - run.lead]
    df = frame(run, P["tags"], P["text_cats"], rows)[list(G.nodes)]
    z = (df - P["mean"]) / P["std"]
    tg_tags = {m: [t for t in G.nodes if P["asset"].get(t) == m and list(G.predecessors(t))] for m in cands}
    cols = [t for ts in tg_tags.values() for t in ts]
    if not cols:
        return dict(r, dowhy=None, static=None, note="no target tag in the graph")
    zz = z[cols].abs().fillna(0.0)
    i = int(np.argmax(zz.max(axis=1).to_numpy()))
    targets = [max(ts, key=lambda t: float(zz[t].iloc[i])) for ts in tg_tags.values() if ts]
    row = df.iloc[[i]].reset_index(drop=True).fillna(pd.Series(P["med"]))
    t1 = time.time()
    dw = dowhy_scores(model, targets, row, P["asset"])
    groups = {m: [t for t in G.nodes if P["asset"].get(t) == m] for m in P["machines"]}
    obs = {c: row[c].to_numpy(float) for c in order}
    ours = removed_trouble(model, order, obs, targets, groups, P["sig2"], P["med"])
    return dict(r, targets=targets, row=int(rows[i]), dowhy=max(dw, key=dw.get) if dw else None,
                static=max(ours, key=ours.get), dowhy_scores=dict(sorted(dw.items(), key=lambda kv: -kv[1])[:4]),
                wall_s=round(time.time() - t1, 1))


def run_plant(a):
    import pickle
    from eval.nodes import Text, load_view
    _quiet()
    man = json.load(open(os.path.join(a.data, "manifest.json")))
    w = man["wiring"]
    G, cut, asset = plant_graph(w)
    tags = sorted({t for d in w.values() for t in d["feeds"] + d["commands"] + d["measures"]})
    t_load = time.time()
    fit_runs = [load_view(os.path.join(a.data, "views", f), tags) for f in man["fit"]]
    text_cats = {}
    for t in tags:
        if any(isinstance(r[t], Text) for r in fit_runs):
            text_cats[t] = sorted(set().union(*({r[t].names[c] for c in np.unique(r[t].codes) if c >= 0}
                                                for r in fit_runs if isinstance(r[t], Text))))
    # the graph on the expanded columns: an edge from a text tag becomes an edge from each category
    H = nx.DiGraph()
    for u, y in G.edges:
        for uu in ([f"{u}={c}" for c in text_cats[u]] if u in text_cats else [u]):
            H.add_edge(uu, y)
    data = pd.concat([frame(r, tags, text_cats, np.arange(0, len(next(iter(r.values()))) - r.lead, a.step))
                      for r in fit_runs], ignore_index=True)
    keep = [c for c in H.nodes if c in data.columns]
    H = H.subgraph(keep).copy()
    data = data[list(H.nodes)].dropna()
    asset2 = {c: asset.get(c.split("=", 1)[0]) for c in H.nodes}
    machines = sorted({m for m in asset2.values() if m})
    print(f"graph: {H.number_of_nodes()} nodes, {H.number_of_edges()} edges, cut {cut}; data {len(data)} rows "
          f"({time.time() - t_load:.0f} s)", flush=True)
    t_fit = time.time()
    model = fit_model(H, data, gcm.ml.create_hist_gradient_boost_regressor)
    order = list(nx.topological_sort(H))
    sig2, med = free_variance(model, order, data)
    print(f"fitted {len(order)} mechanisms ({time.time() - t_fit:.0f} s)", flush=True)
    state = {"model": model, "order": order, "G": H, "tags": tags, "text_cats": text_cats, "asset": asset2,
             "machines": machines, "sig2": sig2, "med": med, "mean": data.mean(), "std": data.std() + 1e-9}
    sp = os.path.join(a.data, "dowhy_state.pkl")
    with open(sp, "wb") as fh:
        pickle.dump(state, fh)
    runs = man["runs"][: a.limit] if a.limit else man["runs"]
    runs = [r for r in runs if r["hit"]]
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_plant_init, initargs=(sp,)) as ex:
        futs = {ex.submit(plant_run, r, a.data): r["key"] for r in runs}
        res = []
        for f in cf.as_completed(futs):
            res.append(f.result())
            print(f"{futs[f]}: {res[-1].get('dowhy')} / {res[-1].get('static')} (root {res[-1]['root']})", flush=True)
    res.sort(key=lambda r: r["key"])
    by = {}
    for r in res:
        b = by.setdefault(r["info"]["fid"], {"runs": 0, "dowhy": 0, "static": 0, "cf": 0, "graph": 0})
        b["runs"] += 1
        b["dowhy"] += r.get("dowhy") == r["root"]
        b["static"] += r.get("static") == r["root"]
        b["cf"] += r.get("named_cf") == r["root"]
        b["graph"] += (r.get("named_g120") or []) == [r["root"]]
    return {"part": "plant", "data": man["results"], "graph_nodes": H.number_of_nodes(), "cut": cut,
            "fit_rows": len(data), "per_fault": by, "runs": res}


def report_md(out):
    if out["part"] == "synthetic":
        L = ["# DoWhy-GCM reference check: synthetic models", "",
             "| Mechanisms | Trials | Scored (an alarm) | Our rule right | DoWhy right | The two agree |", "|---|---|---|---|---|---|"]
        for k, s in out["summary"].items():
            L.append(f"| {k} | {s['trials']} | {s['scored']} | {s['ours']} | {s['dowhy']} | {s['agree']} |")
        return "\n".join(L) + "\n"
    L = [f"# DoWhy-GCM reference check: plant validation runs ({out['data']})", "",
         f"Graph: {out['graph_nodes']} nodes, cut {out['cut']}. Fit rows: {out['fit_rows']}.", "",
         "| Fault | Runs with an alarm | DoWhy-GCM (static) | Our rule (static) | Our counterfactual (dynamic, cf-c3) | Graph rule |",
         "|---|---|---|---|---|---|"]
    tot = {"runs": 0, "dowhy": 0, "static": 0, "cf": 0, "graph": 0}
    for fid in sorted(out["per_fault"], key=lambda x: int(x[1:])):
        b = out["per_fault"][fid]
        L.append(f"| {fid} | {b['runs']} | {b['dowhy']} | {b['static']} | {b['cf']} | {b['graph']} |")
        for k in tot:
            tot[k] += b[k]
    L.append(f"| all | {tot['runs']} | {tot['dowhy']} | {tot['static']} | {tot['cf']} | {tot['graph']} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", choices=("synthetic", "plant"))
    ap.add_argument("--trials", type=int, default=100, help="synthetic: trials per mechanism kind")
    ap.add_argument("--data", default=os.path.join(ROOT, "eval", "cache", "dowhy"))
    ap.add_argument("--step", type=int, default=30, help="plant: seconds between fit samples")
    ap.add_argument("--limit", type=int, default=0, help="plant: only the first n runs")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args(argv)
    t0 = time.time()
    out = run_synthetic(a) if a.part == "synthetic" else run_plant(a)
    out["wall_s"] = round(time.time() - t0)
    d = os.path.join(ROOT, "eval", "results", datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                     + f"-dowhy-{a.part}")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "report.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    md = report_md(out)
    with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md + f"\nwall {out['wall_s']} s, written to {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
