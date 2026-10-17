#!/usr/bin/env python3
"""Equivalence gate for plant speed changes (2026-10-16).

The plant is the ground truth of every score, so a change made only for speed must not change what
the plant does. This tool runs the same days with two copies of the plant code (a reference copy,
for example `git show HEAD:...` or a copy taken before the change, and the working tree) and
compares every tag at every sample.

    python plant/equivalence.py --ref <folder with model/> --hours 6 --seeds 40000,40001 --faults ,F9,F6

Each case runs in its own process (the two copies share the package name `model`). The default
tolerance is zero: every value equal, bit for bit (NaN equal to NaN). A change that is not exact
(multi-rate stepping, a tolerance in a solver) gives --atol and --rtol, and the report states the
largest differences per tag; such a change must also pass plant/verify_plant.py.

The runs are those of eval/nodes.py: the site weather, the day of the year from the seed, a 30 min
warm-up, one sample per second, a fault from the registry at a severity and onset from the seed.
"""
import argparse
import concurrent.futures as cf
import math
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

RUNNER = r'''
import json, math, random, sys, time
sys.path.insert(0, sys.argv[1])
from model.plant import UtilitiesPlant
from model import faults as FR, fault_ranges as RANGES
seed, hours, fid, out = int(sys.argv[2]), float(sys.argv[3]), sys.argv[4], sys.argv[5]
p = UtilitiesPlant(seed=seed, t_day0_h=(seed * 7.37) % 24.0, weather="site")
WARM, DT = 1800.0, 0.1
n = int((hours * 3600.0 + WARM) / DT)
rng = random.Random(seed)
on_at = off_at = None
if fid:
    sev, onset = rng.uniform(0.05, 1.0), rng.uniform(0.2, 0.6) * hours * 3600.0
    mag = 1.0 if fid in FR.BINARY else RANGES.magnitude(fid, sev, RANGES.ends(p))
    on_at = WARM + onset
    d = FR.duration_s(fid, mag)
    off_at = on_at + d if d else None
applied = cleared = False
rows, t0 = {}, time.perf_counter()
m = 0
for k in range(n):
    if on_at is not None and not applied and p.t >= on_at:
        FR.inject(p, fid, mag); applied = True
    if off_at is not None and applied and not cleared and p.t >= off_at:
        FR.clear(p, fid); cleared = True
    p.step(DT)
    if p.t >= WARM and k % 10 == 0:
        tags = p.tags()
        for t in tags.keys() - rows.keys():
            rows[t] = [None] * m
        for t, col in rows.items():
            col.append(tags.get(t))
        m += 1
wall = time.perf_counter() - t0
with open(out, "w") as fh:
    json.dump({"wall_s": wall, "rows": rows}, fh)
'''


def run_case(code_dir, seed, hours, fid, out):
    t = time.time()
    subprocess.run([sys.executable, "-c", RUNNER, code_dir, str(seed), str(hours), fid or "", out], check=True)
    return out, time.time() - t


def same(a, b, atol, rtol):
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b), 0.0
        d = abs(a - b)
        return d <= atol + rtol * abs(a), d
    return a == b and type(a) is type(b), 0.0


def compare(ref, new, atol, rtol):
    """(number of values compared, mismatching values, first mismatch per tag, largest |diff| per tag)."""
    import json
    with open(ref) as fh:
        A = json.load(fh)
    with open(new) as fh:
        B = json.load(fh)
    ra, rb = A["rows"], B["rows"]
    out = {"n": 0, "bad": 0, "first": {}, "maxdiff": {}, "tags_only_in_one": sorted(ra.keys() ^ rb.keys()),
           "wall_ref": A["wall_s"], "wall_new": B["wall_s"]}
    for t in sorted(ra.keys() & rb.keys()):
        xa, xb = ra[t], rb[t]
        if len(xa) != len(xb):
            out["first"][t] = ("length", len(xa), len(xb))
            out["bad"] += 1
            continue
        md = 0.0
        for i, (a, b) in enumerate(zip(xa, xb)):
            out["n"] += 1
            ok, d = same(a, b, atol, rtol)
            md = max(md, d)
            if not ok:
                out["bad"] += 1
                out["first"].setdefault(t, (i, a, b))
        out["maxdiff"][t] = md
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="folder that holds the reference copy of model/")
    ap.add_argument("--new", default=HERE, help="folder that holds the changed model/ (default: this plant/)")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--seeds", default="40000,60000")
    ap.add_argument("--faults", default=",F9", help="fault ids per case, empty for a normal day")
    ap.add_argument("--atol", type=float, default=0.0)
    ap.add_argument("--rtol", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--tmp", default=os.path.join(HERE, "verify_results", "equivalence"))
    a = ap.parse_args(argv)
    os.makedirs(a.tmp, exist_ok=True)
    cases = [(int(s), f.strip()) for s in a.seeds.split(",") for f in a.faults.split(",")]
    jobs = {}
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        for seed, fid in cases:
            for side, d in (("ref", a.ref), ("new", a.new)):
                out = os.path.join(a.tmp, f"{seed}-{fid or 'normal'}-{side}.json")
                jobs[ex.submit(run_case, d, seed, a.hours, fid, out)] = (seed, fid, side)
        done = {jobs[f]: f.result()[0] for f in cf.as_completed(jobs)}
    ok_all, w_ref, w_new = True, 0.0, 0.0
    for seed, fid in cases:
        r = compare(done[(seed, fid, "ref")], done[(seed, fid, "new")], a.atol, a.rtol)
        w_ref += r["wall_ref"]
        w_new += r["wall_new"]
        worst = sorted(r["maxdiff"].items(), key=lambda kv: -kv[1])[:5]
        ok = r["bad"] == 0 and not r["tags_only_in_one"]
        ok_all &= ok
        print(f"seed {seed} {fid or 'normal'}: {r['n']} values, {r['bad']} outside tolerance, "
              f"sim {r['wall_ref']:.0f} s -> {r['wall_new']:.0f} s; {'PASS' if ok else 'FAIL'}")
        if worst and worst[0][1] > 0:
            print("  largest |diff|: " + ", ".join(f"{t} {d:.3g}" for t, d in worst))
        for t, f in list(r["first"].items())[:8]:
            print(f"  first mismatch {t}: {f}")
        if r["tags_only_in_one"]:
            print(f"  tags in one run only: {r['tags_only_in_one']}")
    print(f"{'PASS' if ok_all else 'FAIL'}: tolerance atol {a.atol} rtol {a.rtol}; "
          f"plant time {w_ref:.0f} s -> {w_new:.0f} s ({w_ref / max(w_new, 1e-9):.2f} x)")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
