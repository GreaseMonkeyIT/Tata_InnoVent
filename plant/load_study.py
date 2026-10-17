#!/usr/bin/env python3
"""Transformer load study of the whole plant (the sizing rule of catalog.py TR_FAB / TR_ASM).

Runs the plant with the site weather and the material flow for --hours per seed (the seeds start on
different days of the year), samples each transformer's load in kVA every second, and gives the mean,
the largest 15 min average (maximum demand) and the 1 s peak. Then applies the rule: the smallest
IS 1180 standard rating with the 15 min maximum demand at most 80 % of the rating and the 1 s peak at
most 100 %.

    python plant/load_study.py --hours 24 --seeds 4
"""
import argparse
import concurrent.futures as cf
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from model.plant import UtilitiesPlant          # noqa: E402

PF_TARGET = 0.98
IS1180 = (100, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500)


def md15(kva, sample_s=1.0):
    w = int(900 / sample_s)
    if len(kva) < w:
        return max(kva)
    s = sum(kva[:w])
    best = s
    for i in range(w, len(kva)):
        s += kva[i] - kva[i - w]
        best = max(best, s)
    return best / w


def run(seed, hours, dt=0.1, apfc=True):
    rng = random.Random(f"load:{seed}")
    p = UtilitiesPlant(seed=seed, t_day0_h=rng.uniform(0.0, 24.0), weather="site", with_apfc=apfc)
    every = int(round(1.0 / dt))
    k1, k2, q1, q2 = [], [], [], []
    tan98 = math.tan(math.acos(PF_TARGET))
    for k in range(int((hours * 3600 + 1800) / dt)):
        p.step(dt)
        if p.t >= 1800.0 and k % every == 0:
            k1.append(p.tr1.k * p.tr1.rating.kva)
            k2.append(p.tr2.k * p.tr2.rating.kva)
            for q, tr in ((q1, p.tr1), (q2, p.tr2)):
                s = tr.child.s_down
                q.append((s.imag - max(s.real, 0.0) * tan98) / 1000.0)
    out = {"seed": seed, "tr-1": (sum(k1) / len(k1), md15(k1), max(k1)),
           "tr-2": (sum(k2) / len(k2), md15(k2), max(k2))}
    for name, q in (("q-1", q1), ("q-2", q2)):
        qs = sorted(q)
        out[name] = (qs[len(qs) // 2], qs[int(0.95 * len(qs))], qs[-1])
    return out


def rule(md, peak):
    for s in IS1180:
        if md <= 0.80 * s and peak <= s:
            return s
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-apfc", action="store_true",
                    help="run without the APFC panels: the reactive power each LV main needs (APFC sizing)")
    a = ap.parse_args(argv)
    seeds = list(range(1, a.seeds + 1))
    n = len(seeds)
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(run, seeds, [a.hours] * n, [0.1] * n, [not a.no_apfc] * n))
    if a.no_apfc:
        for name in ("q-1", "q-2"):
            print(f"LV-{name[-1]}: kVAr to reach PF {PF_TARGET}: median {max(r[name][0] for r in res):.0f}, "
                  f"95 % {max(r[name][1] for r in res):.0f}, max {max(r[name][2] for r in res):.0f}")
        return 0
    for tr in ("tr-1", "tr-2"):
        mean = max(r[tr][0] for r in res)
        md = max(r[tr][1] for r in res)
        peak = max(r[tr][2] for r in res)
        print(f"{tr}: mean up to {mean:.0f} kVA, 15 min maximum demand {md:.0f} kVA, 1 s peak {peak:.0f} kVA"
              f" -> rating {rule(md, peak)} kVA")
        for r in res:
            print(f"   seed {r['seed']}: mean {r[tr][0]:.0f}, MD15 {r[tr][1]:.0f}, peak {r[tr][2]:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
