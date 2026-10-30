#!/usr/bin/env python3
"""The score harness (ideas.md 16): run a campaign of random trials, score them, write a report.

    python -m eval.harness --runs 30 --calm 10                  # tuning split, today's engine
    python -m eval.harness --split test --freeze --runs 100     # held-out split, only at the freeze

Rules (ideas.md 16.2):
  - Two seed ranges. The tuning split (seeds from 10,000) is for development. The test split (seeds
    from 900,000) is held out: the harness runs it only with --freeze, and the report says so.
  - Each report records the git commit, whether the tree had changes, and a hash of the engine
    settings and source. A score without them does not count.
  - Every rate has its n and a 95 % Wilson interval.

Output: eval/results/<UTC time>-<split>/ with runs.jsonl (raw timelines), report.json and report.md.
The results folder is gitignored: the numbers go into the docs by hand, with their run folder named.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time

from eval import legacy as L
from eval.metrics import VERDICT_PASSES, Pass, score_calm_run, score_fault_run, summarize

SEED_BASE = {"tuning": 10_000, "test": 900_000}


def git_state():
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=L.ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--", "correlation", "plant/sim", "eval"))}


def score(rec):
    ps = [Pass(x["t"], x["ranking"], [tuple(c) for c in x["cards"]]) for x in rec["passes"]]
    if rec["truth"] is None:
        r = score_calm_run(ps)
    else:
        r = score_fault_run(ps, rec["onset_t"], rec["truth"], rec["watch_s"], rec["trips"])
    r.update({"scenario": rec["scenario"], "seed": rec["seed"], "magnitude": rec["magnitude"]})
    return r


def fmt_rate(x):
    if not x or x.get("n", 0) == 0:
        return "n = 0"
    lo, hi = x["ci95"]
    return "%d / %d = %.0f %% (95 %% CI %.0f to %.0f %%)" % (x["k"], x["n"], 100 * x["k"] / x["n"], 100 * lo, 100 * hi)


def report_md(meta, s):
    L_ = ["# Engine score: %s" % meta["engine"], "",
          "Split **%s**%s. %s. Commit `%s` on `%s`%s. Settings hash `%s`. Verdict: a root that holds rank 1 for %d passes." % (
              meta["split"], " (held out, frozen)" if meta["split"] == "test" else " (development)",
              meta["date"], meta["git"]["commit"][:10], meta["git"]["branch"],
              ", tree with uncommitted changes" if meta["git"]["dirty"] else "", meta["settings_hash"], VERDICT_PASSES),
          "", "| Scenario | True root | Detection | Top-1 | Top-3 | MRR | Delay median / p95 (s) | Wrong roots |",
          "|---|---|---|---|---|---|---|---|"]
    for sc, r in s["by_scenario"].items():
        d = ("%.0f / %.0f" % (r["delay_median_s"], r["delay_p95_s"])) if r["delay_median_s"] is not None else "-"
        L_.append("| %s | %s | %s | %s | %s | %.2f | %s | %s |" % (
            sc, r["truth"], fmt_rate(r["detection"]), fmt_rate(r["top1"]), fmt_rate(r["top3"]), r["mrr"], d,
            ", ".join(r["wrong_roots"]) or "-"))
    if s["overall"]:
        o = s["overall"]
        L_ += ["", "Overall top-1: %s. Top-3: %s. MRR %.2f." % (fmt_rate(o["top1"]), fmt_rate(o["top3"]), o["mrr"])]
    fa = s["false_alarms"]
    per_h = "%.2f" % fa["per_hour"] if fa["per_hour"] is not None else "-"
    L_ += ["", "False alarms: %d episodes in %.1f calm hours = %s per hour (%d of %d calm runs had any)." % (
        fa["episodes"], fa["calm_hours"], per_h, fa["calm_runs_with_any"], fa["calm_runs"])]
    c = s["cards"]
    L_ += ["", "Trip cards: precision %s, recall %s, median lead %s s, ETA mean absolute error %s s." % (
        fmt_rate(c["precision"]), fmt_rate(c["recall"]),
        "%.0f" % c["lead_median_s"] if c["lead_median_s"] is not None else "-",
        "%.0f" % c["eta_mae_s"] if c["eta_mae_s"] is not None else "-")]
    L_ += ["", "Scope: the plant families only (rails, cooling loop). PS3, PS4A, PS4B and PS6 run in other "
               "layers on the box and are not in this score."]
    return "\n".join(L_) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=("tuning", "test"), default="tuning")
    ap.add_argument("--freeze", action="store_true", help="required for the held-out test split")
    ap.add_argument("--runs", type=int, default=30, help="runs per fault scenario")
    ap.add_argument("--calm", type=int, default=10, help="calm runs (1 h each)")
    ap.add_argument("--scenarios", default="PS1,PS2,PS5,PS7")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default=os.path.join(L.ROOT, "eval", "results"))
    a = ap.parse_args(argv)
    if a.split == "test" and not a.freeze:
        ap.error("the test split is held out: run it only at the freeze, with --freeze")
    base = SEED_BASE[a.split]
    plans = [L.plan(sc, base + i) for sc in a.scenarios.split(",") for i in range(a.runs)]
    plans += [L.plan("PS0", base + 50_000 + i) for i in range(a.calm)]
    meta = {"engine": "v1 (the box engine, correlation/service.py)", "split": a.split,
            "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "git": git_state(), "settings_hash": L.settings_hash(), "runs": a.runs, "calm": a.calm,
            "scenarios": a.scenarios, "seed_base": base}
    out = os.path.join(a.out, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + a.split)
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    scored = []
    ctx = mp.get_context("spawn")
    with open(os.path.join(out, "runs.jsonl"), "w") as fh, \
            cf.ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx, max_tasks_per_child=1) as ex:
        futs = {ex.submit(L.run_in_child, p): p for p in plans}
        for i, f in enumerate(cf.as_completed(futs), 1):
            p = futs[f]
            try:
                rec = f.result()
            except Exception as e:                                   # a crashed trial is a result too
                print("trial %s seed %d FAILED: %r" % (p["scenario"], p["seed"], e), flush=True)
                continue
            fh.write(json.dumps(rec) + "\n")
            r = score(rec)
            scored.append(r)
            print("[%3d/%d] %-4s seed %-7d %s" % (
                i, len(plans), p["scenario"], p["seed"],
                ("verdict=%s %s" % (r.get("verdict"), "ok" if r.get("top1") else "WRONG" if r.get("verdict") else "MISS"))
                if r["truth"] else "false_alarms=%d" % r["false_alarms"]), flush=True)
    s = summarize(scored)
    meta["wall_s"] = round(time.time() - t0)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump({"meta": meta, "summary": s, "runs": scored}, fh, indent=1, default=str)
    md = report_md(meta, s)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md + "\nwritten to " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
