#!/usr/bin/env python3
"""Two-step training (ideas.md 12.3, step 9): the joint pass of the twin.

Step 1 is eval/nodes.py: each node's model trains alone, on measured inputs. In a replay, a node takes
the predictions of the nodes upstream of it, not the meters, so the errors of the upstream models
enter its inputs. The joint pass refits every node that takes a chained input (twin/twin.py) on its
fit days twice: once with the measured inputs and once with the chained inputs from a free replay of
the step 1 twin. The targets stay the measured outputs. The node then learns to predict well from the
inputs that a replay gives it, as in training on a model's own simulated inputs (simulation error
against prediction error in system identification: Ljung, System Identification, 2nd ed. 1999,
section 7.3).

One pass, all nodes at once: each node learns against the step 1 models upstream of it. The refitted
models are for the twin only (eval/cf.py --twin-models). The alarms keep the step 1 models, so the
detection does not change.

    python -m eval.joint eval/results/<UTC time>-nodes --workers 14
"""
import argparse
import concurrent.futures as cf
import os
import pickle
import sys
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

from eval.nodes import SAMPLE_S, TAUS, Node, Run, RunsModel, Text, load_view  # noqa: E402
from twin.twin import Twin  # noqa: E402

_CTX = {}


def _init(models_path):
    with open(models_path, "rb") as fh:
        m = pickle.load(fh)
    _CTX["nodes"] = {n: Node.from_state(s) for n, s in m["nodes"].items()}
    _CTX["w"] = m["wiring"]


def free_job(path, out_path):
    """The chained columns of one fit day from a free replay of the step 1 twin (the whole day after
    the lead), saved to out_path."""
    run = load_view(path)
    tw = Twin(_CTX["nodes"], _CTX["w"], {t for t, v in run.items() if not isinstance(v, Text)})
    vals, _ = tw.replay(run, 0, free=True)
    cols = {}
    for t, src in tw.chained.items():
        c = np.asarray(run[t], float).copy()
        c[run.lead:] = vals[src][:, tw.nodes[src].out_names.index(t)]
        cols[t] = c
    np.savez(out_path, **{f"c{i}": v for i, v in enumerate(cols.values())}, names=np.asarray(list(cols)))
    return out_path


def fit_job(name, fit_paths, free_paths):
    """Refit one node on its fit days with measured inputs and with free replay inputs."""
    nd = _CTX["nodes"][name]
    tags = nd.doc["feeds"] + nd.doc["commands"] + nd.doc["measures"]
    runs = []
    for p, fp in zip(fit_paths, free_paths):
        r = load_view(p, tags)
        runs.append(r)
        with np.load(fp) as z:
            names = [str(x) for x in z["names"]]
            free = {t: z[f"c{i}"] for i, t in enumerate(names) if t in tags and t not in nd.doc["measures"]}
        runs.append(Run({**r, **free}, r.lead))
    Us, Ys = zip(*(nd.matrices(r) for r in runs))
    leads = [r.lead for r in runs]
    m = RunsModel(nd.model.inputs, nd.out_names, taus=TAUS, hidden=nd.hidden)
    m.cov_cols = nd.model.cov_cols
    m.bounds, m.leads = [len(u) for u in Us], leads
    t0 = time.time()
    m.fit(np.vstack(Us), np.vstack([y[n:] for y, n in zip(Ys, leads)]), SAMPLE_S, epochs=150, patience=15)
    m.bounds = m.leads = None
    return name, dict(m.__dict__), time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="an eval/nodes.py results folder with models.pkl")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args(argv)
    t0 = time.time()
    mpath = os.path.join(a.results, "models.pkl")
    with open(mpath, "rb") as fh:
        m = pickle.load(fh)
    fit = [p for _, p in m["fit"]]
    tmp = os.path.join(a.results, "joint_free")
    os.makedirs(tmp, exist_ok=True)
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init, initargs=(mpath,)) as ex:
        free = list(ex.map(free_job, fit, [os.path.join(tmp, f"{i}.npz") for i in range(len(fit))]))
        print(f"free replays of {len(free)} fit days ({time.time() - t0:.0f} s)", flush=True)
        _init(mpath)
        run = load_view(fit[0])
        tw = Twin(_CTX["nodes"], _CTX["w"], {t for t, v in run.items() if not isinstance(v, Text)})
        todo = sorted(n for n in tw.w if tw.pred[n])
        futs = [ex.submit(fit_job, n, fit, free) for n in todo]
        models = {}
        for f in cf.as_completed(futs):
            n, md, s = f.result()
            models[n] = md
            print(f"refitted {n} ({s:.0f} s)", flush=True)
    with open(os.path.join(a.results, "twin_models.pkl"), "wb") as fh:
        pickle.dump({"models": models, "fit": m["fit"], "wall_s": round(time.time() - t0)}, fh)
    print(f"written {os.path.join(a.results, 'twin_models.pkl')}: {len(models)} nodes, {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
