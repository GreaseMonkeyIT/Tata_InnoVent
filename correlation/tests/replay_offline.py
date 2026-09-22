#!/usr/bin/env python3
"""Offline replay: the real plant physics through the real correlation pass.

SCENARIOS.md 4.4 names this the acceptance check before a box run. A unit fixture plants a
synthetic vector and proves one rule. This drives the actual `plant/sim/main.py` model, samples
it on the engine's own 5 s grid, and runs the actual `run_pass`, so it catches what a fixture
cannot: a rule that is right on its own and wrong once the rest of the plant answers.

LIMIT, read this before trusting a line of the output. The replay runs UNGATED: it passes no
learned baselines, so the deviation gate that keeps a normal duty cycle quiet is not in play.
PS0 is therefore expected to name the compressor here, and that says nothing about the box. Use
this to compare ROOTS UNDER A FAULT, never to judge silence.

Run:  python correlation/tests/replay_offline.py        (from the repo root)
"""
import importlib.util
import os
import random
import sys

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "correlation"))
_SIM = os.path.join(_ROOT, "plant", "sim", "main.py")
_spec = importlib.util.spec_from_file_location("plant_sim", _SIM)
sim = importlib.util.module_from_spec(_spec)
sys.modules["plant_sim"] = sim
_spec.loader.exec_module(sim)

from engine.common_mode import EVIDENCE            # noqa: E402
from engine.gate import Witness                    # noqa: E402
from engine.pipeline import run_pass               # noqa: E402

TICK_S, GRID_S, N = 1.0, 5.0, 180                  # the 15-minute ring at POLL_S=5
INJECT_AT = 100                                    # grid sample where the fault lands
NOMINAL_V = 400.0                                  # PLANT_INVERT: sag = 400 - V


def _reset(seed):
    random.seed(seed)
    sim.SUPPLY.target, sim.SUPPLY.voltage = 1.0, sim.SUPPLY.v_nom
    for r in sim.RAILS:
        r.voltage = r.v_nom
    for d in sim.DEVICES:
        d.friction, d.temp, d.current, d.throughput, d.tripped = 1.0, 35.0, 0.0, 100.0, False
        if d.overload is not None:
            d.overload.reset()
    sim.BY_NAME["compressor-1"].duty_fn = sim.compressor_duty
    sim.LOOP.pump_health, sim.LOOP.flow = 1.0, sim.LOOP.flow_nominal


def replay(fault, seed=99):
    """Drive the model for 15 minutes, inject `fault` at sample 100, return the pass output."""
    _reset(seed)
    volts, amps, t = {}, {}, 61.0
    for k in range(N):
        if fault and k == INJECT_AT:
            sim.FAULTS[fault]["apply"]()
        for _ in range(int(GRID_S / TICK_S)):
            sim.SUPPLY.step(sum(d.current for d in sim.DEVICES), TICK_S)
            for r in sim.RAILS:
                r.step(sum(d.current for d in sim.DEVICES if d.rail is r))
            sim.LOOP.step()
            for d in sim.DEVICES:
                d.step(t, TICK_S)
            t += TICK_S
        volts.setdefault(sim.SUPPLY.name, []).append(sim.SUPPLY.voltage)
        amps.setdefault(sim.SUPPLY.name, []).append(sum(d.current for d in sim.DEVICES))
        for r in sim.RAILS:
            volts.setdefault(r.name, []).append(r.voltage)
        for d in sim.DEVICES:
            volts.setdefault(d.name, []).append(d.rail.voltage)
            amps.setdefault(d.name, []).append(d.current)
    if fault:
        sim.FAULTS[fault]["clear"]()

    vectors = {p: np.array([NOMINAL_V - v for v in s]) for p, s in volts.items()}
    write_vectors = {p: np.array(s) for p, s in amps.items()}
    rails = {k: set(v) for k, v in sim.domains()["domains"].items() if k.startswith("rail:")}
    pairs = set()
    for members in rails.values():
        ms = sorted(members)
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                pairs.add(frozenset((a, b)))
    return run_pass(
        vectors, Witness(shared_relation=pairs, relation_kind="rail"),
        window=90, write_vectors=write_vectors, gate_q=35, bare_src_must_deviate=True,
        common_mode={"domains": rails, "kind": "rail", "min_members": 3, "window_s": 15.0})


EXPECT = {"PS1": "press-1", "PS2": "compressor-1", "PS7": "incomer-1"}


def main():
    print("offline replay: real physics -> real pass (UNGATED, see the module docstring)\n")
    print("%-5s %-14s %-14s %s" % ("id", "expected", "root", "verdict"))
    bad = 0
    for fid, want in EXPECT.items():
        out = replay(fid)
        roots = out["root_cause_ranking"]
        got = roots[0]["pod"] if roots else "(none)"
        ok = got == want
        bad += 0 if ok else 1
        print("%-5s %-14s %-14s %s" % (fid, want, got, "PASS" if ok else "FAIL"))
        cme = sorted({(e["src"], e["dst"]) for e in out["edges"]
                      if EVIDENCE in e.get("evidence", [])})
        if cme:
            print("      common-mode edges: %s" % cme)
        if not ok:
            top = [(r["pod"], round(r["score"], 2)) for r in roots[:4]]
            print("      ranking: %s" % top)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
