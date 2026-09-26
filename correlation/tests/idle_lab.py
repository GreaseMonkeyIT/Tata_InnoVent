#!/usr/bin/env python3
"""Idle lab: does a long fault teach the engine to flag a normal plant?

On 2026-09-25 two long PS2 runs (11 min and 17.5 min) left forge in a state where every normal
compressor-1 cycle raised a compressor-1 root. The rail B baselines had learned the flat 374 V
plateau that PS2 leaves after furnace-1 trips, because the p90 storm test never let them learn
from a normal duty cycle (SCENARIOS.md 4.4).

This lab reuses `ps2_lab.py` (the real plant model, the OpenPLC latch, the real engine pass) but
runs an engine pass every 10 s from the start, so the baselines learn the way they do on the box:
  1. normal plant for WARM_S (baselines mature),
  2. PS2 for FAULT_S, then the plant reset (`reset_plant`, what POST /reset does),
  3. normal plant for AFTER_S.
It counts the passes that show a root while no fault is active, before and after the fault.
A clean engine shows none after the clear time.

The operator locks the baselines after the soak (LOG-089), so the lab locks them at the end of
the warm-up. IDLE_LAB_LOCK=0 keeps learning on, which reproduces the 2026-09-25 box state.

Run (one process per setting, as with ps2_lab.py):
    python correlation/tests/idle_lab.py                    # learn, then lock (the fix)
    IDLE_LAB_LOCK=0 python correlation/tests/idle_lab.py    # learning never stops (the bug)
"""
import collections, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ps2_lab as L                                   # noqa: E402  (sets the engine env first)

sim, S = L.sim, L.S
WARM_S, FAULT_S, AFTER_S = 7200, 1050, 3600
PASS_EVERY_S, CLEAR_S = 10, 600                       # after the reset, allow the verdict to clear
LOCK = os.environ.get("IDLE_LAB_LOCK", "1") != "0"


def run(seed=99):
    L.reset(seed)
    hist = collections.defaultdict(lambda: collections.deque(maxlen=L.RING))
    t = 61.0
    end = WARM_S + FAULT_S + AFTER_S
    roots = {"before": [], "after": []}
    passes = {"before": 0, "after": 0}
    for k in range(int(end / L.GRID)):
        now = k * L.GRID
        if now == WARM_S:
            if LOCK:
                for m in S._memory.values():
                    m.locked = True                   # what deploy/engine-baselines.sh lock does
            sim.FAULTS["PS2"]["apply"](); sim.ACTIVE.add("PS2")
        if now == WARM_S + FAULT_S:
            sim.reset_plant()
        for _ in range(int(L.GRID / L.TICK)):
            t = L.step_plant(t)
        L.sample(hist, k)
        if now < L.RING * L.GRID or now % PASS_EVERY_S:
            continue                                  # wait for a full ring, then pass every 10 s
        g = L.one_pass(hist)
        top = (g.get("root_cause_ranking") or [{}])[0].get("pod")
        if WARM_S - 1800 <= now < WARM_S:
            phase = "before"
        elif now >= WARM_S + FAULT_S + CLEAR_S:
            phase = "after"
        else:
            continue
        passes[phase] += 1
        if top:
            roots[phase].append((int(now - WARM_S - FAULT_S), top))
    return passes, roots


if __name__ == "__main__":
    passes, roots = run()
    thr = S._memory["bus_voltage"].baseline_threshold("psu-b")
    print("baselines: %s" % ("learn, then lock at the fault" if LOCK else "learning never stops"))
    print("psu-b sag threshold after the run: %s" % (("%.2f" % thr) if thr is not None else None))
    for phase in ("before", "after"):
        hits = roots[phase]
        print("%-6s the fault: %d of %d idle passes show a root %s"
              % (phase, len(hits), passes[phase],
                 sorted(collections.Counter(r for _, r in hits).items())))
    if roots["after"]:
        print("first idle roots after the reset (s after the reset): %s" % roots["after"][:8])
