#!/usr/bin/env python3
"""Verify the whole plant as a ground truth: long runs, many seeds, checks on every tick.

The engine is scored against this plant, so the plant must never give an impossible state, a fake
constant or a fake rhythm. One run = the whole site (UtilitiesPlant with every machine and the
building) for --hours at the plant tick, from a random time of day per seed.

Checks:
  physics     plant.check() every second (machine checks, no export, ranges)
  load flow   the sweep solution: the voltage mismatch on every branch after the solve
  loop heat   the heat into the loop water minus the heat removed, against the change of the
              heat in the water (the loop's energy balance over the run)
  tags        every numeric tag every second: no NaN or infinity, LV bus voltage inside +-10 % of
              nominal (IEC 60038), transformer load at most 100 %, no export at the incomer
  flat        a tag that never changes in the run (a fake constant)
  rhythm      a tag whose upward crossings of its median come at intervals with a spread under
              0.5 %: an exact machine period. A PLC auto cycle can have one; work driven by people
              or by arrivals cannot. The report lists them for review, it does not fail on them.

    python plant/verify_plant.py --hours 24 --seeds 4 --workers 4

Output: plant/verify_results/<UTC time>/report.json and report.md (gitignored).
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import math
import os
import random
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

from plant.model.plant import UtilitiesPlant      # noqa: E402

WARMUP_S = 1800.0
V_LIMIT_PU = 0.10            # IEC 60038: LV supply within +-10 % of nominal


def flow_mismatch(net):
    """Largest relative voltage mismatch on a branch after the sweep: v_child against
    v_parent / ratio - z i, with i from the final child voltage and power."""
    worst = 0.0
    for br in net._order():
        c = br.child
        if abs(c.v) < 1e-6:
            continue
        i = (c.s_down / (3.0 * c.v)).conjugate()
        v_calc = br.parent.v / br.ratio - br.z * i
        worst = max(worst, abs(v_calc - c.v) / abs(c.v))
    return worst


def run(seed, hours, dt):
    rng = random.Random(f"verify:{seed}")
    p = UtilitiesPlant(seed=seed, t_day0_h=rng.uniform(0.0, 24.0), weather="site")
    n = int(hours * 3600.0 / dt)
    every = int(round(1.0 / dt))
    names, cols = None, None
    violations = {}
    mismatch = 0.0
    e_in = 0.0
    loop = p.loop
    h0 = None
    t0 = time.time()
    for k in range(n):
        p.step(dt)
        if p.t >= WARMUP_S:
            if h0 is None:
                h0 = loop.heat_content()
            e_in += (loop.q_users + loop.pump.p_shaft - loop.q_chiller) * dt
        if k % every:
            continue
        for msg in p.check():
            key = msg.split(":")[0] + ": " + msg.split(":", 1)[-1].strip()[:60]
            v = violations.setdefault(key, {"count": 0, "first_s": p.t})
            v["count"] += 1
        if p.t < WARMUP_S:
            continue
        if k % (10 * every) == 0:
            mismatch = max(mismatch, flow_mismatch(p.net))
        tags = p.tags()
        if names is None:
            names = sorted(k2 for k2, v in tags.items() if isinstance(v, (int, float, bool)))
            cols = {nm: [] for nm in names}
        for nm in names:
            cols[nm].append(float(tags.get(nm, float("nan"))))
    loop_err = (loop.heat_content() - h0) - e_in
    out = {"seed": seed, "t_day0_h": p.t_day0_h, "hours": hours, "wall_s": round(time.time() - t0),
           "violations": violations, "flow_mismatch_max": mismatch,
           "loop_energy": {"in_minus_out_J": e_in, "stored_change_J": loop.heat_content() - h0,
                           "error_J": loop_err,
                           "error_pct_of_throughput": None}, "tags": {}}
    thr = sum(abs(x) for x in [e_in]) or 1.0
    out["loop_energy"]["error_pct_of_throughput"] = 100.0 * abs(loop_err) / thr if thr else None
    for nm in names:
        x = np.asarray(cols[nm], float)
        st = {"n": int(len(x)), "nonfinite": int((~np.isfinite(x)).sum())}
        xf = x[np.isfinite(x)]
        if len(xf):
            st.update({"min": float(xf.min()), "p5": float(np.percentile(xf, 5)),
                       "median": float(np.median(xf)), "p95": float(np.percentile(xf, 95)),
                       "max": float(xf.max()), "flat": bool(xf.max() - xf.min() < 1e-9)})
            med = float(np.median(xf))
            above = xf > med
            ups = np.flatnonzero(above[1:] & ~above[:-1]) + 1
            if len(ups) >= 11:
                iv = np.diff(ups).astype(float)
                cv = float(iv.std() / iv.mean()) if iv.mean() > 0 else None
                st["rhythm"] = {"crossings": int(len(ups)), "period_s": float(iv.mean()), "cv": cv}
        if nm.endswith(".voltage_v") and nm.startswith("psu-") and len(xf):
            st["v_out_s"] = int((np.abs(xf / 433.0 - 1.0) > V_LIMIT_PU).sum())
        if nm.endswith(".load_pct") and nm.startswith("tr-") and len(xf):
            st["over_100_s"] = int((xf > 100.0).sum())
        out["tags"][nm] = st
    return out


def report_md(meta, runs):
    L = [f"# Plant verification, {meta['date']}", "",
         f"{len(runs)} runs of {meta['hours']} h at a {meta['dt']} s tick, seeds {meta['seeds']}. "
         f"Statistics after a {WARMUP_S / 60:.0f} min warm-up, one sample per second.", ""]
    L += ["## Physics checks", ""]
    any_v = False
    for r in runs:
        for k, v in r["violations"].items():
            any_v = True
            L.append(f"- seed {r['seed']}: {k} ({v['count']} s, first at {v['first_s']:.0f} s)")
    if not any_v:
        L.append("No check failed in any run.")
    L += ["", "## Load flow and loop energy", "", "| Seed | Start (h of day) | Load-flow mismatch, max | "
          "Loop energy error, % of heat through | Wall time (s) |", "|---|---|---|---|---|"]
    for r in runs:
        le = r["loop_energy"]
        L.append(f"| {r['seed']} | {r['t_day0_h']:.1f} | {r['flow_mismatch_max']:.2e} | "
                 f"{le['error_pct_of_throughput']:.3f} | {r['wall_s']} |")
    names = sorted(runs[0]["tags"])
    bad = []
    for nm in names:
        for r in runs:
            st = r["tags"].get(nm, {})
            if st.get("nonfinite"):
                bad.append(f"- {nm}: {st['nonfinite']} non-finite samples (seed {r['seed']})")
            if st.get("v_out_s"):
                bad.append(f"- {nm}: {st['v_out_s']} s outside +-10 % (seed {r['seed']})")
            if st.get("over_100_s"):
                bad.append(f"- {nm}: {st['over_100_s']} s above 100 % (seed {r['seed']})")
    L += ["", "## Tag limits", ""] + (bad or ["No tag outside its limits."])
    flat = [nm for nm in names if all(r["tags"].get(nm, {}).get("flat") for r in runs)]
    L += ["", "## Flat in every run", ""] + ([f"- {nm}" for nm in flat] or ["None."])
    exact = []
    for nm in names:
        rs = [r["tags"].get(nm, {}).get("rhythm") for r in runs]
        rs = [x for x in rs if x and x["cv"] is not None]
        if rs and all(x["cv"] < 0.005 for x in rs):
            exact.append(f"- {nm}: period {np.mean([x['period_s'] for x in rs]):.1f} s, "
                         f"spread {100 * max(x['cv'] for x in rs):.2f} %")
    L += ["", "## Exact machine periods (review)", ""] + (exact or ["None."])
    L += ["", "## Every tag (all runs pooled: min, 5 %, median, 95 %, max)", "",
          "| Tag | Min | 5 % | Median | 95 % | Max |", "|---|---|---|---|---|---|"]
    for nm in names:
        sts = [r["tags"][nm] for r in runs if "median" in r["tags"].get(nm, {})]
        if not sts:
            continue
        L.append(f"| {nm} | {min(s['min'] for s in sts):.4g} | {np.median([s['p5'] for s in sts]):.4g} | "
                 f"{np.median([s['median'] for s in sts]):.4g} | {np.median([s['p95'] for s in sts]):.4g} | "
                 f"{max(s['max'] for s in sts):.4g} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--dt", type=float, default=0.1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(HERE, "verify_results"))
    a = ap.parse_args(argv)
    seeds = list(range(a.seed0, a.seed0 + a.seeds))
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        runs = list(ex.map(run, seeds, [a.hours] * len(seeds), [a.dt] * len(seeds)))
    meta = {"hours": a.hours, "dt": a.dt, "seeds": seeds,
            "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    out = os.path.join(a.out, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump({"meta": meta, "runs": runs}, fh, indent=1)
    md = report_md(meta, runs)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md[:6000] + "\nwritten to " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
