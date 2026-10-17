#!/usr/bin/env python3
"""SUPERSEDED 2026-10-15 by eval/nodes.py. Kept as the record of the first machine scores (LOG-111).

Why superseded (operator, 2026-10-15: no per-machine fixes, no invented faults): each bench here
has a hand-written input list, its own model size, filter set and phase input, and scores faults
that are not on the agreed list (ideas.md 11.10). eval/nodes.py builds every node from the plant's
documents with one rule.

Score each machine on its own small model (ideas.md 17.6). CP-1 and CP-2 made into a harness.

For one machine:
  1. Train its small model (twin/node_model.py) on normal running only: random supply, cooling
     water, air demand or operator handling, and material batches. The sim is never read by the model.
  2. Run random trials: normal runs, victim runs (the machine only suffers: supply sag, warm water,
     more air demand), control runs (a load or material change, no machine fault) and fault runs
     (each machine fault at a random magnitude and onset).
  3. Detect with today's engine statistics on the residuals (ideas.md 12.3 step 5): per output,
     z = residual / its normal spread; a channel alarms when the 35th percentile of |z| over the last
     120 s is above 3.5 (the box's GATE_Q 35 and DEV_K 3.5). press-1 also has a cycle-part duration
     channel (CP-2: pump wear shows only in the time of the working part of a cycle).
  4. Score: detection rate and delay per fault, false alarms per hour in normal, victim and control
     time (and before each onset), and which channel alarmed first. Every rate with its n and a 95 %
     Wilson interval.

    python -m eval.machines --machine compressor-1 --runs 30
    python -m eval.machines --machine press-1 --runs 30

Output: eval/results/<UTC time>-machine-<name>/report.json and report.md (gitignored).
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time

# One math thread per process: the campaign runs one process per core, and a multithreaded BLAS in
# each of them oversubscribes the CPU (forge, 2026-10-15: load 29 on 16 threads). Set before numpy.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from model import catalog as C                                      # noqa: E402
from model.compressor import P_ATM, ScrewCompressor                 # noqa: E402
from model.press_brake import PressBrake                            # noqa: E402
from twin.node_model import NodeModel                               # noqa: E402
from twin.rhythm import OnlinePhase, segment_durations              # noqa: E402
from eval.metrics import wilson                                     # noqa: E402

K_DEV, Q_GATE, WIN_S = 3.5, 0.35, 120.0       # the box's DEV_K, GATE_Q and the 2 min gate window
WARMUP_S = 900.0                              # the model's filters and the plant settle first
SEED_BASE = {"tuning": 20_000, "test": 920_000}


# ------------------------------------------------------------------------------ compressor-1 --
class CompressorBench:
    """compressor-1 as in the plant: Atlas Copco GA 37 W class on the 37 kW motor, 0.9 kg/s water."""
    name = "compressor-1"
    DT, SAMPLE = 0.1, 1.0
    INPUTS = ["v_ll", "water_in_c", "water_kgs", "header_barg", "st_loaded", "st_unloaded", "st_running"]
    OUTPUTS = ["current_a", "element_c", "water_out_c"]
    # kind, name, setter(c, m), magnitude range (project choices around the CP-1 levels)
    FAULTS = {
        "oil_cooler_fouling": (lambda c, m: setattr(c, "cooler_fouling", m), (0.5, 2.0)),
        "bearing_drag": (lambda c, m: setattr(c.motor, "friction_nm", m * c.motor.rating.t_n), (0.04, 0.15)),
        "transducer_low": (lambda c, m: setattr(c, "pt_fault_bar", m), (4.5, 6.5)),
        "intake_filter": (lambda c, m: setattr(c, "filter_dp_bar", m), (0.08, 0.25)),
    }
    VICTIMS = ("supply_sag", "warm_water", "more_demand")
    CONTROLS = ()

    def simulate(self, hours, seed, event=None):
        """event: (kind, name, onset_s, magnitude) or None. Returns U, Y (at SAMPLE)."""
        rng = np.random.default_rng(seed)
        r = C.GA37W_7_5
        c = ScrewCompressor("compressor-1", r, C.ABB_37KW)
        c.leak_m3s_bar = 0.20 * r.fad_m3min / 60.0 / (P_ATM + 7.0)
        st = {"demand": 0.0}
        c.demands.append(lambda p: st["demand"] * min(1.0, max(0.0, (p - P_ATM) / 5.0)))
        v_walk = t_walk = tools = 0.0
        d_level, d_next = 0.45, 0.0
        sag_end = rng.uniform(600.0, 1800.0)
        U, Y = [], []
        every = int(self.SAMPLE / self.DT)
        for k in range(int(hours * 3600 / self.DT)):
            t = k * self.DT
            dt = self.DT
            v_walk += rng.normal(0, 0.002) * math.sqrt(dt) - 0.002 * v_walk * dt
            v = 428.0 * (1 + max(-0.03, min(0.03, v_walk)))
            t_walk += rng.normal(0, 0.03) * math.sqrt(dt) - 0.0003 * t_walk * dt
            tw = 30.5 + 0.8 * math.sin(2 * math.pi * t / 540.0) + max(-3.5, min(3.5, t_walk))
            mw = 0.9 * (1 + 0.03 * math.sin(2 * math.pi * t / 1300.0))
            if t >= d_next:
                d_level, d_next = rng.uniform(0.25, 0.65), t + rng.uniform(600.0, 2400.0)
            tools += rng.normal(0, 0.08) * math.sqrt(dt) - 0.05 * tools * dt
            lvl = d_level
            if event and t >= event[2]:
                kind, nm, _, m = event
                if nm == "supply_sag" and t < event[2] + sag_end:
                    v *= m
                elif nm == "warm_water":
                    tw += m * min(1.0, (t - event[2]) / 600.0)
                elif nm == "more_demand":
                    lvl = m
                elif kind == "fault" and not getattr(c, "_applied", False):
                    self.FAULTS[nm][0](c, m)
                    c._applied = True
            st["demand"] = max(0.0, min(0.80, lvl + tools)) * r.fad_m3min / 60.0
            c.step(dt, v, 50.0, tw, mw)
            if k % every == 0:
                s = c.state
                U.append([v, tw, mw, c.reading_barg(), s == "loaded", s == "unloaded",
                          s in ("loaded", "unloaded", "star")])
                Y.append([c.motor.amps, c.t_elem, c.t_water_out])
        return np.array(U, float), np.array(Y, float), None

    @staticmethod
    def victim_magnitude(nm, rng):
        return {"supply_sag": rng.uniform(0.88, 0.95), "warm_water": rng.uniform(2.0, 6.0),
                "more_demand": rng.uniform(0.60, 0.75)}[nm]

    def make_model(self):
        return NodeModel(self.INPUTS, self.OUTPUTS, hidden=(32, 32))

    def inputs(self, U, Y, extra):
        return U

    def phase_bins(self, U, Y, extra):
        return np.zeros(len(U), int)                 # one spread per output (CP-1)


# ----------------------------------------------------------------------------------- press-1 --
class PressBench:
    """press-1 as in the plant: LVD PPEB 320/40 class on the 37 kW motor, oil cooler on cool-1."""
    name = "press-1"
    DT, SAMPLE, FAST = 0.05, 0.5, 0.1
    OUTPUTS = ["current_a", "pressure_bar", "oil_c"]
    FAULTS = {
        "F1_guide_friction": (lambda p, m: setattr(p, "friction_extra_n", m * C.PPEB_320_40.force_kn * 1e3), (0.02, 0.08)),
        "F2_pump_wear": (lambda p, m: setattr(p, "leak_factor", m), (2.0, 6.0)),
        "oil_cooler_fouling": (lambda p, m: setattr(p.cooler, "fouling", m), (1.0, 4.0)),
    }
    VICTIMS = ("supply_sag", "warm_water")
    CONTROLS = ("F15_hard_plate",)

    def simulate(self, hours, seed, event=None):
        rng = np.random.default_rng(seed)
        p = PressBrake("press-1", C.PPEB_320_40, C.ABB_37KW, t_oil0=45.0)
        p.motor.w = p.w_n
        v_walk = tw_walk = 0.0
        bends_seen, uts = -1, 510.0
        sag_end = rng.uniform(600.0, 1800.0)
        rows, fast = [], []
        every, every_fast = int(self.SAMPLE / self.DT), int(self.FAST / self.DT)
        applied = False
        for k in range(int(hours * 3600 / self.DT)):
            t = k * self.DT
            dt = self.DT
            v_walk += rng.normal(0, 0.002) * math.sqrt(dt) - 0.002 * v_walk * dt
            v = 428.0 * (1 + max(-0.03, min(0.03, v_walk)))
            tw_walk += rng.normal(0, 0.03) * math.sqrt(dt) - 0.0003 * tw_walk * dt
            tw = 30.5 + max(-3.5, min(3.5, tw_walk))
            hard = None
            if event and t >= event[2]:
                kind, nm, _, m = event
                if nm == "supply_sag" and t < event[2] + sag_end:
                    v *= m
                elif nm == "warm_water":
                    tw += m * min(1.0, (t - event[2]) / 600.0)
                elif nm == "F15_hard_plate":
                    hard = m
                elif kind == "fault" and not applied:
                    self.FAULTS[nm][0](p, m)
                    applied = True
            if p.bends != bends_seen and p.phase == p.IDLE and p.t_phase < dt * 1.5:
                bends_seen = p.bends
                p.handling_s = rng.uniform(15.0, 40.0)
                if p.bends % 20 == 0:
                    uts = rng.uniform(470.0, 630.0)              # S355J2 batches
                p.plate.uts_mpa = hard or uts
            p.step(dt, v, 50.0, tw, C.PPEB_320_40.water_kgs)
            if k % every_fast == 0:
                fast.append(p.motor.amps)
            if k % every == 0:
                rows.append([v, tw, p.motor.amps, p.p_bar, p.t_oil])
        a = np.array(rows)
        return a[:, :2], a[:, 2:], np.array(fast)

    @staticmethod
    def victim_magnitude(nm, rng):
        return {"supply_sag": rng.uniform(0.90, 0.95), "warm_water": rng.uniform(2.0, 4.0),
                "F15_hard_plate": rng.uniform(640.0, 700.0)}[nm]

    def learn_rhythm(self, Y):
        self.rp = OnlinePhase.learn(Y[:, 0], self.SAMPLE)

    def make_model(self):
        names = ["v_ll", "water_in_c"] + [f"phase_rbf{i}" for i in range(12)]
        return NodeModel(names, self.OUTPUTS, hidden=(48, 48), taus=(2.0, 10.0, 60.0, 600.0))

    def inputs(self, U, Y, extra):
        P = OnlinePhase(self.rp.mid, self.rp.period).run(Y[:, 0], self.SAMPLE)
        return np.hstack([U, P])

    def phase_bins(self, U, Y, extra):
        """The discovered phase (the strongest basis function): a spread per phase, from the data only."""
        P = OnlinePhase(self.rp.mid, self.rp.period).run(Y[:, 0], self.SAMPLE)
        return P.argmax(axis=1)


BENCHES = {"compressor-1": CompressorBench, "press-1": PressBench}


# -------------------------------------------------------------------------------- detection --
def gate_alarm(z, sample_s):
    """Today's deviation gate on a residual z series: the 35th percentile of |z| over the last 120 s
    above 3.5. Returns a boolean per sample."""
    w = max(1, int(WIN_S / sample_s))
    a = np.abs(z)
    out = np.zeros(len(a), bool)
    for i in range(w, len(a) + 1):
        out[i - 1] = np.quantile(a[i - w:i], Q_GATE) > K_DEV
    return out


def episodes(alarm, sample_s, t0_s=0.0):
    """(start_s, end_s) of each contiguous alarm."""
    out, start = [], None
    for i, a in enumerate(alarm):
        if a and start is None:
            start = i
        elif not a and start is not None:
            out.append((t0_s + start * sample_s, t0_s + i * sample_s))
            start = None
    if start is not None:
        out.append((t0_s + start * sample_s, t0_s + len(alarm) * sample_s))
    return out


class Scorer:
    """The trained model of one machine plus the normal spreads of its residuals."""

    def __init__(self, bench, train_hours=12.0, seed=1):
        self.b = bench
        t0 = time.time()
        U, Y, extra = bench.simulate(train_hours, seed)
        if hasattr(bench, "learn_rhythm"):
            bench.learn_rhythm(Y)
        self.m = bench.make_model()
        X = bench.inputs(U, Y, extra)
        self.m.fit(X, Y, bench.SAMPLE, epochs=200, patience=20)
        r = self.m.residuals(X, Y, bench.SAMPLE)
        split = int(0.8 * len(U))
        bins = bench.phase_bins(U, Y, extra)
        self.sd = {}
        for g in np.unique(bins):
            mask = bins[split:] == g
            self.sd[int(g)] = (r[split:][mask].std(axis=0) if mask.sum() > 20 else r[split:].std(axis=0)) + 1e-9
        self.sd_all = r[split:].std(axis=0) + 1e-9
        self.dur = None
        if extra is not None:
            d = segment_durations(extra, bench.rp.mid, bench.FAST)
            med = float(np.median(d))
            self.dur = (med, max(1.4826 * float(np.median(np.abs(d - med))), bench.FAST))
        self.train_s = time.time() - t0
        self.n_weights = self.m.n_weights()

    def channels(self, U, Y, extra):
        """Alarm series per channel name, at the model's sample rate (and the duration channel)."""
        b = self.b
        X = b.inputs(U, Y, extra)
        r = self.m.residuals(X, Y, b.SAMPLE)
        bins = b.phase_bins(U, Y, extra)
        sd = np.stack([self.sd.get(int(g), self.sd_all) for g in bins])
        z = r / sd
        out = {name: gate_alarm(z[:, j], b.SAMPLE) for j, name in enumerate(b.OUTPUTS)}
        if self.dur is not None:
            # the working part of each cycle; the channel alarms when the median of the last 5
            # cycles is more than 3.5 spreads away (a cycle-level form of the same gate)
            mid, fast = b.rp.mid, b.FAST
            above = extra > mid
            starts = np.flatnonzero(above[1:] & ~above[:-1]) + 1
            alarm = np.zeros(len(U), bool)
            durs = []
            for s in starts:
                e = s
                while e < len(above) and above[e]:
                    e += 1
                if e >= len(above):
                    break
                durs.append((e * fast, (e - s) * fast))
            med, mad = self.dur
            for i in range(len(durs)):
                last = [d for _, d in durs[max(0, i - 4):i + 1]]
                if len(last) == 5 and abs(np.median(last) - med) / mad > K_DEV:
                    t_end = durs[i][0]
                    t_next = durs[i + 1][0] if i + 1 < len(durs) else len(U) * b.SAMPLE
                    alarm[int(t_end / b.SAMPLE):int(t_next / b.SAMPLE)] = True
            out["cycle_duration"] = alarm
        return out


def run_trial(args):
    """One trial in a worker: (machine, kind, name, seed, scorer_state) -> scored record."""
    machine, kind, name, seed, scorer = args
    b = scorer.b
    rng = random.Random(f"{machine}:{kind}:{name}:{seed}")
    onset = rng.uniform(1200.0, 2400.0)
    watch = 1800.0
    if kind == "normal":
        event, hours = None, 2.0
    else:
        mag = b.FAULTS[name][1] if kind == "fault" else None
        m = rng.uniform(*mag) if mag else b.victim_magnitude(name, np.random.default_rng(seed))
        event, hours = (kind, name, onset, m), (onset + watch) / 3600.0
    U, Y, extra = b.simulate(hours, seed, event)
    ch = scorer.channels(U, Y, extra)
    any_alarm = np.zeros(len(U), bool)
    for a in ch.values():
        any_alarm |= a
    i0 = int(WARMUP_S / b.SAMPLE)
    rec = {"machine": machine, "kind": kind, "name": name, "seed": seed,
           "magnitude": event[3] if event else None}
    if kind == "normal":
        eps = episodes(any_alarm[i0:], b.SAMPLE, WARMUP_S)
        rec.update({"calm_s": len(U) * b.SAMPLE - WARMUP_S, "false_alarms": len(eps)})
        return rec
    io = int(onset / b.SAMPLE)
    pre = episodes(any_alarm[i0:io], b.SAMPLE, WARMUP_S)
    post = episodes(any_alarm[io:], b.SAMPLE, onset)
    rec.update({"onset_s": onset, "pre_calm_s": onset - WARMUP_S, "pre_false_alarms": len(pre)})
    if kind == "fault":
        rec["detected"] = bool(post)
        rec["delay_s"] = (post[0][0] - onset) if post else None
        first = None
        if post:
            t_first = post[0][0]
            k = int(t_first / b.SAMPLE)
            first = sorted(n for n, a in ch.items() if a[k:k + 2].any())
        rec["first_channels"] = first
    else:                                        # victim or control: every alarm after the onset is false
        rec["false_alarms"] = len(post)
        rec["calm_s"] = watch
        rec["channels"] = sorted(n for n, a in ch.items() if a[io:].any())
    return rec


_SCORER = None


def _init_worker(machine, train_seed):
    global _SCORER
    _SCORER = Scorer(BENCHES[machine](), seed=train_seed)


def _run(job):
    machine, kind, name, seed = job
    return run_trial((machine, kind, name, seed, _SCORER))


def summarize(recs, scorer_info):
    out = {"model": scorer_info, "faults": {}, "false_alarms": {}}
    for name in sorted({r["name"] for r in recs if r["kind"] == "fault"}):
        rs = [r for r in recs if r["kind"] == "fault" and r["name"] == name]
        k = sum(r["detected"] for r in rs)
        d = sorted(r["delay_s"] for r in rs if r["detected"])
        firsts = {}
        for r in rs:
            for c in r["first_channels"] or []:
                firsts[c] = firsts.get(c, 0) + 1
        out["faults"][name] = {"k": k, "n": len(rs), "ci95": list(wilson(k, len(rs))),
                               "delay_median_s": d[len(d) // 2] if d else None,
                               "first_channel_counts": firsts}
    for kind in ("normal", "victim", "control"):
        rs = [r for r in recs if r["kind"] == kind]
        for name in sorted({r["name"] for r in rs}):
            sub = [r for r in rs if r["name"] == name]
            ep = sum(r["false_alarms"] for r in sub)
            hrs = sum(r["calm_s"] for r in sub) / 3600.0
            runs_any = sum(1 for r in sub if r["false_alarms"])
            out["false_alarms"][f"{kind}:{name}"] = {
                "episodes": ep, "hours": hrs, "per_hour": ep / hrs if hrs else None,
                "runs_with_any": runs_any, "runs": len(sub), "ci95_runs": list(wilson(runs_any, len(sub))),
                "channels": sorted({c for r in sub for c in r.get("channels", [])})}
    pre = [r for r in recs if "pre_false_alarms" in r]
    ep, hrs = sum(r["pre_false_alarms"] for r in pre), sum(r["pre_calm_s"] for r in pre) / 3600.0
    out["false_alarms"]["before_onsets"] = {"episodes": ep, "hours": hrs, "per_hour": ep / hrs if hrs else None}
    return out


def report_md(meta, s):
    m = s["model"]
    L = [f"# Machine score: {meta['machine']}", "",
         f"Split **{meta['split']}**. {meta['date']}. Small model: {m['n_weights']} weights, trained on "
         f"{m['train_hours']} h of normal running in {m['train_s']:.0f} s. Detector: |z| above {K_DEV} for the "
         f"{int(Q_GATE * 100)}th percentile of the last {int(WIN_S)} s (today's engine gate, on residuals).", "",
         "| Fault | Detected (95 % CI) | Delay median (s) | First channel(s) |", "|---|---|---|---|"]
    for name, f in s["faults"].items():
        lo, hi = f["ci95"]
        d = f"{f['delay_median_s']:.0f}" if f["delay_median_s"] is not None else "-"
        ch = ", ".join(f"{c} {n}" for c, n in sorted(f["first_channel_counts"].items(), key=lambda x: -x[1]))
        L.append(f"| {name} | {f['k']} / {f['n']} = {100 * f['k'] / f['n']:.0f} % ({100 * lo:.0f} to {100 * hi:.0f} %) | {d} | {ch or '-'} |")
    L += ["", "| Calm, victim or control | Runs with a false alarm (95 % CI) | False alarms per hour | Channels |",
          "|---|---|---|---|"]
    for name, f in s["false_alarms"].items():
        if name == "before_onsets":
            continue
        lo, hi = f["ci95_runs"]
        ph = f"{f['per_hour']:.2f}" if f["per_hour"] is not None else "-"
        L.append(f"| {name} | {f['runs_with_any']} / {f['runs']} ({100 * lo:.0f} to {100 * hi:.0f} %) | {ph} | {', '.join(f['channels']) or '-'} |")
    b = s["false_alarms"]["before_onsets"]
    L += ["", f"Before the onsets: {b['episodes']} false alarm episodes in {b['hours']:.1f} h."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", choices=sorted(BENCHES), required=True)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--split", choices=("tuning", "test"), default="tuning")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results"))
    a = ap.parse_args(argv)
    if a.split == "test" and not a.freeze:
        ap.error("the test split is held out: run it only at the freeze, with --freeze")
    bench = BENCHES[a.machine]()
    base = SEED_BASE[a.split]
    jobs = [(a.machine, "normal", "normal", base + i) for i in range(a.runs)]
    jobs += [(a.machine, "fault", f, base + 1000 * (j + 1) + i) for j, f in enumerate(bench.FAULTS) for i in range(a.runs)]
    jobs += [(a.machine, "victim", v, base + 50_000 + 1000 * j + i) for j, v in enumerate(bench.VICTIMS) for i in range(a.runs)]
    jobs += [(a.machine, "control", v, base + 80_000 + 1000 * j + i) for j, v in enumerate(bench.CONTROLS) for i in range(a.runs)]
    t0 = time.time()
    ref = Scorer(BENCHES[a.machine](), seed=1)        # the same seed in every worker: the same model
    info = {"n_weights": ref.n_weights, "train_hours": 12.0, "train_s": ref.train_s}
    recs = []
    ctx = mp.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx, initializer=_init_worker,
                                initargs=(a.machine, 1)) as ex:
        for i, rec in enumerate(ex.map(_run, jobs, chunksize=2), 1):
            recs.append(rec)
            tag = ("detected %s" % rec["detected"]) if rec["kind"] == "fault" else "false %d" % rec["false_alarms"]
            print("[%3d/%d] %-7s %-20s seed %-7d %s" % (i, len(jobs), rec["kind"], rec["name"], rec["seed"], tag), flush=True)
    s = summarize(recs, info)
    meta = {"machine": a.machine, "split": a.split, "runs": a.runs, "wall_s": round(time.time() - t0),
            "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    out = os.path.join(a.out, datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                       + f"-machine-{a.machine}")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump({"meta": meta, "summary": s, "runs": recs}, fh, indent=1, default=float)
    md = report_md(meta, s)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md + "\nwritten to " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
