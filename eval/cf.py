#!/usr/bin/env python3
"""The counterfactual root (ideas.md 12.3, step 10) and the replay error of the twin (step 8), scored
with the trained nodes of one eval/nodes.py campaign (run it with --save-models).

    python -m eval.cf eval/results/<UTC time>-nodes --workers 14

The method follows Budhathoki, Minorics, Bloebaum and Janzing, "Causal structure-based root cause
analysis of outliers", ICML 2022: each node is a mechanism plus its own noise, and the root of an
outlier is the node whose noise, set back to normal, removes the most of the outlier. Here the
mechanism is the node's model, its noise is its residual, and the twin (twin/twin.py) carries a change
from node to node along the documents.

  1. The normal plant: the twin replays the window freely (every value is a prediction, from the
     measured exogenous inputs and the measured history before the window).
  2. The trouble of a set of values: for every node output, the squared deviation from the normal
     plant, divided by the free-replay variance of that output on normal days, summed over the
     window. Only the samples where the node's inputs lie inside their training range count, as for
     the alarms (where the model does not know, a large residual is not evidence).
  3. For each machine S (a suspect), the twin replays the window with S reset: S's nodes take their
     predictions, every node downstream keeps its own residual and takes the change of its inputs.
     The removed trouble is the trouble of the measured values less the trouble of the replay.
  4. The targets are the machines that alarm in the last `--post` seconds of the window (for a single
     fault: the first machine to alarm and the machines that alarm within 120 s of it, the
     candidates of the graph rule).
  5. The root is the suspect whose reset removes the most trouble at the targets. It can be a target
     or a machine upstream of one that has not alarmed. A target that only reacts to a change
     upstream removes little with its own reset: its own residual is small, and its deviation goes
     when the upstream machine is reset (ideas.md 12.3: "a machine that only reacts to a supply dip
     removes nothing"). When no reset removes any trouble, the window names nothing, and a single
     fault takes the graph rule's root.

The window ends at a machine's first alarm plus the graph rule's 120 s and starts `--window` seconds
before the alarm. A single fault is explained at the first alarm. A fault pair is explained at the
first alarm of every machine that alarms after the first onset, and at every new crossing of a CUSUM
sum (a second fault on a machine that the first one already holds in alarm). The named roots are the
explanations.

The variant of c1 and c2 (every machine a suspect, its removed trouble standardized by its mean and
spread on calibration windows) is reported next to it (`top1_cf_all`).

The graph rule of eval/nodes.py (`top1_g120`) is scored on the same runs as the cross-check.
"""
import argparse
import concurrent.futures as cf
import json
import os
import pickle
import sys
import time

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

from eval.nodes import (SAMPLE_S, Node, Run, Text, change_point, crossings, downstream,  # noqa: E402
                        first_alarm, load_view, score_fault, score_pair)
from eval.metrics import wilson  # noqa: E402
from twin.twin import Twin  # noqa: E402

WINDOW_S, POST_S = 900.0, 120.0     # project choices: about 2 x the slowest alarm delay of a strong fault
CAL_WINDOWS = 4                     # windows per calibration day
MAX_PAIR_WINDOWS = 12

_CTX = {}


def _init(models_path, twin_path=None):
    with open(models_path, "rb") as fh:
        m = pickle.load(fh)
    _CTX["nodes"] = {n: Node.from_state(s) for n, s in m["nodes"].items()}
    _CTX["w"] = m["wiring"]
    _CTX["twin"] = None
    # the twin's models: the step 1 models, or the joint pass of step 9 (eval/joint.py) where it refitted one
    _CTX["tnodes"] = _CTX["nodes"]
    if twin_path:
        with open(twin_path, "rb") as fh:
            tm = pickle.load(fh)["models"]
        _CTX["tnodes"] = {}
        for n, s in m["nodes"].items():
            _CTX["tnodes"][n] = Node.from_state(dict(s, model=tm[n]) if n in tm else s)


def _twin(run):
    if _CTX["twin"] is None:
        numeric = {t for t, v in run.items() if not isinstance(v, Text)}
        _CTX["twin"] = Twin(_CTX["tnodes"], _CTX["w"], numeric)
    return _CTX["twin"]


TROUBLE = os.environ.get("VISR_CF_TROUBLE", "all")   # "all" or "numeric" (trouble_mask)


def trouble_mask(nd, which=None):
    """The output columns of a node that count for the trouble. "all" (the default since c2): every
    output, as the CUSUM on the mean (a controller output carries the evidence of a control fault,
    F13). "numeric": numbers only, not the outputs of the asset's own control loops (c1)."""
    if (which or TROUBLE) == "all":
        return np.ones(len(nd.out_names), bool)
    own = set(nd.doc.get("loop_outputs", []))
    return np.array([("=" not in o) and (o not in own) for o in nd.out_names], bool)


def truncate(run, end):
    """The run up to sample `end` after its lead (the models are causal: the values before `end` do
    not change)."""
    n = run.lead + end
    return Run({t: v[:n] for t, v in run.items()}, run.lead)


def window_terms(run, t_end, window_s=WINDOW_S, post_s=POST_S, suspects=None):
    """The raw terms of one window that ends at sample t_end (after the lead): for every node, the
    squared deviations of the measured values from the free replay (ss0) and of the one-step
    predictions from them (ss1), per counted output; for every suspect machine, per node it
    reaches, ss0 less the squared deviations of its reset replay (d)."""
    nodes, w = _CTX["nodes"], _CTX["w"]
    tw = _twin(run)
    T = len(next(iter(run.values()))) - run.lead
    start = max(0, int(t_end - window_s / SAMPLE_S))
    end = min(T, int(t_end + post_s / SAMPLE_S))
    rt = truncate(run, end)
    (base, cover), meas = tw.base(rt, with_cover=True), tw.measured(rt)
    if _CTX["tnodes"] is not nodes:     # the coverage of the step 1 models, as for the alarms
        cover = {n: nodes[n].predict(rt)[1] for n in w}
    free, conv = tw.replay(rt, start, free=True, base=base, meas=meas)
    masks = {n: trouble_mask(nodes[n]) for n in w}
    # a sample counts only where the node's inputs (as measured) are inside their training range:
    # elsewhere the model does not know, as for the alarms (eval/nodes.py, coverage)
    rows = {n: cover[n][start:end] for n in w}

    def ss(vals, n):
        e = (vals - free[n])[start:end][rows[n]][:, masks[n]]
        return np.nansum(e * e, axis=0)

    ss0 = {n: ss(meas[n], n) for n in w}
    ss1 = {n: np.nansum(((meas[n] - base[n])[start:end][rows[n]][:, masks[n]]) ** 2, axis=0) for n in w}
    assets = sorted({d["asset"] for d in w.values()})
    d = {}
    for S in (assets if suspects is None else suspects):     # () gives the free replay terms only
        reset = [n for n in w if w[n]["asset"] == S]
        vals, _ = tw.replay(rt, start, reset=reset, base=base, meas=meas)
        d[S] = {n: ss0[n] - ss(vals[n], n) for n in tw.affected(reset)}
    cnt = {n: int(rows[n].sum()) for n in w}
    return {"t": int(t_end), "n": end - start, "cnt": cnt, "ss0": ss0, "ss1": ss1, "d": d, "conv": conv}


def calib_job(path, hours, n_windows=CAL_WINDOWS, window_s=WINDOW_S, post_s=POST_S, suspects=None):
    """Windows on a normal day, evenly spaced."""
    run = load_view(path)
    T = len(next(iter(run.values()))) - run.lead
    return [window_terms(run, int(T * k / (n_windows + 1)), window_s, post_s, suspects)
            for k in range(1, n_windows + 1)]


def free_variance(cal_w, w):
    """{node: the free-replay variance per output} from the calibration windows, in their order."""
    sig2 = {}
    for n in w:
        num = sum(x["ss0"][n] for x in cal_w)
        den = sum(x["cnt"][n] for x in cal_w)
        sig2[n] = np.maximum(num / max(den, 1), 1e-12)
    return sig2


def fault_job(kind, path, info, window_s=WINDOW_S, post_s=POST_S):
    """A fault or pair run: the alarms and the graph rule as in eval/nodes.py, then the windows."""
    nodes, w = _CTX["nodes"], _CTX["w"]
    run = load_view(path)
    amap = {n: d["asset"] for n, d in w.items()}
    reach = downstream(w)
    io = int(info["onset_s"] / SAMPLE_S)
    al, cps, events = {}, {}, {}
    for n, nd in nodes.items():
        a, cov = nd.alarms(run, with_cover=True)
        al[n] = a
        fa = first_alarm(a, io)
        cps[n] = None if fa is None else change_point(nd._stats, fa)
        if kind == "pair":
            events[n] = [(t, c) for t, c in crossings(nd._stats, io) if cov[t]]
    rec = score_fault(al, info, amap, cps, reach) if kind == "fault" else score_pair(al, info, amap, reach, events)
    hit = {}
    for n, a in al.items():
        i = first_alarm(a, io)
        if i is not None and (amap[n] not in hit or i < hit[amap[n]]):
            hit[amap[n]] = i
    times = sorted(set(hit.values()))
    cross = {}
    if kind == "pair":
        # a second fault on a machine that the first fault already holds in alarm shows no new first
        # alarm, only a new crossing of a CUSUM sum (eval/nodes.py, crossings): a window there too
        # (a new change: its change point after the first onset, as in score_pair). The first alarms
        # come first, the crossings fill the windows that are left (c4: in c3 the crossings of a growing
        # air leak took every window before the second fault's first alarm)
        for n, ev in events.items():
            cross.setdefault(amap[n], []).extend(t for t, c in ev if c >= io)
        times = times[:MAX_PAIR_WINDOWS]
        extra = []
        for t in sorted({t for ts in cross.values() for t in ts}):
            if len(times) + len(extra) < MAX_PAIR_WINDOWS and all(abs(t - u) > post_s / SAMPLE_S for u in times + extra):
                extra.append(t)
        times = sorted(times + extra)
    if info.get("control"):
        times = []                      # no root to name (F14 to F16)
    times = times[:1] if kind == "fault" else times
    rec["windows"] = [window_terms(run, t, window_s, post_s) for t in times]
    rec["hit"] = hit
    rec["cross"] = {m: sorted(set(ts)) for m, ts in cross.items()}
    return rec


def standardize(win, sig2, mu, sd):
    """{suspect: standardized removed trouble} of one window."""
    out = {}
    for S, dn in win["d"].items():
        v = sum(float(np.nansum(np.asarray(x) / sig2[n])) for n, x in dn.items())
        out[S] = (v - mu.get(S, 0.0)) / sd.get(S, 1.0)
    return out


def removed(win, sig2):
    return {S: sum(float(np.nansum(np.asarray(x) / sig2[n])) for n, x in dn.items()) for S, dn in win["d"].items()}


def target_scores(win, sig2, targets, asset):
    """{suspect: the trouble its reset removes at the nodes of the target machines}: the attribution of
    the alarmed machines' trouble (Budhathoki et al. 2022, with the alarmed machines as the target)."""
    return {S: sum(float(np.nansum(np.asarray(x) / sig2[n])) for n, x in dn.items() if asset[n] in targets)
            for S, dn in win["d"].items()}


def targets_at(rec, t, post):
    """The machines whose first alarm, or a new crossing (fault pairs), lies in [t, t + post]."""
    out = {m for m, ti in rec["hit"].items() if t <= ti <= t + post}
    return out | {m for m, ts in rec.get("cross", {}).items() if any(t <= ti <= t + post for ti in ts)}


def parts(win, sig2):
    """The normalized terms of a window, kept in the report so that a rule on them needs no new
    replay: own[node] = the trouble of the measured values, cut[suspect][node] = the trouble that
    the suspect's reset removes at that node."""
    own = {n: float(np.nansum(np.asarray(x) / sig2[n])) for n, x in win["ss0"].items()}
    cut = {S: {n: float(np.nansum(np.asarray(x) / sig2[n])) for n, x in dn.items()} for S, dn in win["d"].items()}
    return {"t": win["t"], "n": win["n"], "own": own, "cut": cut}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="an eval/nodes.py results folder with models.pkl")
    ap.add_argument("--window", type=float, default=WINDOW_S, help="seconds of the window before the alarm")
    ap.add_argument("--post", type=float, default=POST_S, help="seconds of the window after the alarm")
    ap.add_argument("--calib-step", type=int, default=4, help="use every n-th calibration day")
    ap.add_argument("--limit", type=int, default=0, help="score only the first n fault runs of each split (a quick check)")
    ap.add_argument("--tag", default="", help="a label for the output files")
    ap.add_argument("--kinds", default="fault,pair", help="the run kinds to score: fault, pair or both")
    ap.add_argument("--twin-models", action="store_true",
                    help="replay with the joint pass of step 9 (twin_models.pkl from eval/joint.py)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args(argv)
    t0 = time.time()
    mpath = os.path.join(a.results, "models.pkl")
    with open(mpath, "rb") as fh:
        m = pickle.load(fh)
    w, hours = m["wiring"], m["hours"]
    calib = m["calib"][::a.calib_step]
    splits = m["meta"]["splits"]
    kinds = [x.strip() for x in a.kinds.split(",") if x.strip()]
    jobs = [(k, v) for k, v in m["runs"].items() if k.split("|")[1] in kinds and k.split("|")[0] in splits]
    if a.limit:
        jobs = [j for sp in splits for j in [x for x in jobs if x[0].startswith(sp + "|")][:a.limit]]
    normals = [(k, v) for k, v in m["runs"].items() if k.split("|")[:2] == ["tuning", "normal"]]
    with cf.ProcessPoolExecutor(max_workers=a.workers, initializer=_init,
                                initargs=(mpath, os.path.join(a.results, "twin_models.pkl") if a.twin_models else None)) as ex:
        cal = [ex.submit(calib_job, p, hours, CAL_WINDOWS, a.window, a.post) for _, p in calib]
        nor = [ex.submit(calib_job, v[0], hours, CAL_WINDOWS, a.window, a.post) for _, v in normals]
        fj = {ex.submit(fault_job, k.split("|")[1], v[0], v[1], a.window, a.post): k for k, v in jobs}
        cal_w = [x for f in cal for x in f.result()]
        print(f"calibration: {len(cal_w)} windows ({time.time() - t0:.0f} s)", flush=True)
        nor_w = [x for f in nor for x in f.result()]
        recs = {}
        for f in cf.as_completed(fj):
            recs[fj[f]] = f.result()
            print(f"{fj[f]} done ({time.time() - t0:.0f} s)", flush=True)
    # the free-replay variance per output, from the calibration windows
    sig2 = free_variance(cal_w, w)
    # each suspect's removed trouble on normal windows: its mean and spread
    rem = [removed(x, sig2) for x in cal_w]
    mu = {S: float(np.mean([r[S] for r in rem])) for S in rem[0]}
    sd = {S: float(np.std([r[S] for r in rem])) + 1e-9 for S in rem[0]}
    cal_max = sorted(max(standardize(x, sig2, mu, sd).values()) for x in cal_w)
    # step 8: the replay error against the one-step error, normal tuning days
    ratio = {}
    for n in w:
        f = sum(x["ss0"][n] for x in nor_w)
        o = sum(x["ss1"][n] for x in nor_w)
        ratio[n] = [float(np.sqrt(fi / oi)) if oi > 0 else None for fi, oi in zip(np.atleast_1d(f), np.atleast_1d(o))]
    conv = max((x["conv"] for x in cal_w + nor_w), default=0.0)
    out = {"meta": {"results": os.path.basename(os.path.normpath(a.results)), "window_s": a.window, "post_s": a.post,
                    "trouble": TROUBLE, "coverage": True, "twin_models": a.twin_models,
                    "calib_days": len(calib), "calib_windows": len(cal_w), "normal_windows": len(nor_w),
                    "cycle_last_change": conv, "wall_s": round(time.time() - t0)},
           "mu": mu, "sd": sd, "sig2": sig2, "calib_max": cal_max, "ratio": ratio, "faults": {}, "pairs": {},
           "calib_windows": [parts(x, sig2) for x in cal_w]}
    asset = {n: d["asset"] for n, d in w.items()}
    for k, rec in sorted(recs.items()):
        sp, kind, _ = k.split("|")
        wins = rec.pop("windows")
        expl = []
        for x in wins:
            # the rule: the suspect whose reset removes the most trouble of the machines that alarm in
            # the window's last post seconds (the targets); none when no reset removes any (c3)
            tg = targets_at(rec, x["t"], a.post / SAMPLE_S)
            sc = target_scores(x, sig2, tg, asset)
            best = max(sc, key=sc.get) if sc and max(sc.values()) > 0 else None
            # the variant of c1 and c2: every suspect, its removed trouble standardized on normal days
            sa = standardize(x, sig2, mu, sd)
            expl.append({"t": x["t"], "targets": sorted(tg), "named": best, "score": sc.get(best),
                         "scores": dict(sorted(sc.items(), key=lambda kv: -kv[1])[:5]),
                         "named_all": max(sa, key=sa.get), "parts": parts(x, sig2)})
        rec["cf"] = expl
        if kind == "fault":
            if not rec.get("control"):
                g = rec.get("named_g120") or []
                got = expl[0]["named"] if expl else None
                rec["named_cf"] = got if got is not None else (g[0] if len(g) == 1 else None)
                rec["top1_cf"] = rec["named_cf"] == rec["root"]
                rec["top1_cf_all"] = bool(expl) and expl[0]["named_all"] == rec["root"]
            out["faults"].setdefault(sp, []).append(rec)
        else:
            named = sorted({e["named"] for e in expl if e["named"] is not None})
            rec["named_cf"] = named
            rec["passed_cf"] = all(r in named and rec["detected"][r] for r in rec["roots"])
            out["pairs"].setdefault(sp, []).append(rec)
    tag = ("-" + a.tag) if a.tag else ""
    with open(os.path.join(a.results, f"cf{tag}.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o))
    md = report_md(out, splits)
    with open(os.path.join(a.results, f"cf{tag}.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md)
    return 0


def report_md(out, splits):
    m = out["meta"]
    L = [f"# Counterfactual root ({m['results']}, window {m['window_s']:.0f} s + {m['post_s']:.0f} s)", "",
         f"Calibration: {m['calib_windows']} windows on {m['calib_days']} days. Largest change of a cycle in the "
         f"last replay pass: {m['cycle_last_change']:.3g}. Wall time {m['wall_s']} s.", "",
         "## Faults: root named", "",
         "| Split | Fault | Root | Runs | Detected | Graph rule (120 s) | Counterfactual (targets) | Counterfactual, every suspect standardized |",
         "|---|---|---|---|---|---|---|---|"]
    tot = {}
    for sp in splits:
        recs = [r for r in out["faults"].get(sp, []) if not r.get("control")]
        for fid in sorted({r["fid"] for r in recs}, key=lambda x: int(x[1:])):
            rs = [r for r in recs if r["fid"] == fid]
            g = sum(bool(r.get("top1_g120")) for r in rs)
            c = sum(bool(r.get("top1_cf")) for r in rs)
            cr = sum(bool(r.get("top1_cf_all")) for r in rs)
            det = sum(bool(r.get("detected")) for r in rs)
            L.append(f"| {sp} | {fid} | {rs[0]['root']} | {len(rs)} | {det} | {g} | {c} | {cr} |")
            t = tot.setdefault(sp, [0, 0, 0, 0, 0])
            t[0] += len(rs)
            t[1] += det
            t[2] += g
            t[3] += c
            t[4] += cr
    L += ["", "| Split | Runs | Detected | Graph rule | Counterfactual (95 % CI) | Every suspect standardized |", "|---|---|---|---|---|---|"]
    for sp, t in tot.items():
        lo, hi = wilson(t[3], t[0])
        L.append(f"| {sp} | {t[0]} | {t[1]} | {t[2]} | {t[3]} ({100 * lo:.0f} to {100 * hi:.0f} %) | {t[4]} |")
    L += ["", "## Where the two rules differ", "", "| Split | Fault | Seed | Root | Graph rule | Counterfactual | Top scores |",
          "|---|---|---|---|---|---|---|"]
    for sp in splits:
        for r in out["faults"].get(sp, []):
            if r.get("control") or bool(r.get("top1_g120")) == bool(r.get("top1_cf")):
                continue
            top = ", ".join(f"{k} {v:.1f}" for k, v in list(r["cf"][0]["scores"].items())[:3]) if r["cf"] else "-"
            L.append(f"| {sp} | {r['fid']} | {r['seed']} | {r['root']} | {','.join(r.get('named_g120', []))} | "
                     f"{r.get('named_cf')} | {top} |")
    if out["pairs"]:
        L += ["", "## Fault pairs (passed: every real root named and detected)", "",
              "| Split | Pair | Runs | Graph rule passed | Counterfactual passed | Machines named by the counterfactual, median |",
              "|---|---|---|---|---|---|"]
        for sp in splits:
            recs = out["pairs"].get(sp, [])
            for fid in sorted({r["fid"] for r in recs}):
                rs = [r for r in recs if r["fid"] == fid]
                nn = sorted(len(r["named_cf"]) for r in rs)[len(rs) // 2]
                L.append(f"| {sp} | {fid} | {len(rs)} | {sum(r['passed'] for r in rs)} | "
                         f"{sum(r['passed_cf'] for r in rs)} | {nn} |")
    L += ["", "## The twin: free replay error against the one-step error (normal tuning days)", "",
          "Per node: the root mean square deviation of the measured values from the free replay, divided by "
          "that of the residuals, per counted output, over windows of the same length.", "",
          "| Node | Ratio per output |", "|---|---|"]
    for n, rs in out["ratio"].items():
        L.append(f"| {n} | {', '.join('-' if v is None else f'{v:.2f}' for v in rs)} |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
