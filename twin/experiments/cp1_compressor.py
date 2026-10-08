"""Concept check CP-1: one machine, one small model, residuals (ideas.md 12.3 steps 3 to 5).

The question: does a small model, learned from normal running only, give a residual that
  (a) stays flat for normal running it has not seen,
  (b) stays flat when the machine is only a VICTIM: a supply voltage sag, warm cooling water,
      a change of air demand (the raw signals move, the inputs explain it),
  (c) grows for a real fault IN the machine: oil cooler fouling, motor bearing friction, a failed
      pressure transducer, a clogged intake filter?
And for contrast: does a raw-signal band (today's engine: median +- k * MAD on the raw value)
flag the victim cases?

Machine: the screw compressor of plant/model/compressor.py on the verified 22 kW motor
(PROVISIONAL compressor size for the concept; the final compressor-1 gets its own data).

Run:  python twin/experiments/cp1_compressor.py      (from the repo root; numpy needed)
"""
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from model.catalog import ABB_M3BP_180MLB4_22KW                     # noqa: E402
from model.compressor import CompressorRating, ScrewCompressor, P_ATM  # noqa: E402
from twin.node_model import NodeModel                                # noqa: E402

DT = 0.1            # physics step, s
SAMPLE = 1.0        # data rate for the model, s
COMP = CompressorRating(
    fad_m3min=3.6, p_work_barg=7.5, p_max_barg=8.0, p_shaft_kw=20.5, unloaded_frac=0.25,
    blowdown_s=20.0, oil_flow_kgs=0.6, oil_set_c=60.0, oil_mass_kg=20.0, t_trip_c=110.0,
    water_flow_kgs=0.6, water_in_c=28.0, receiver_m3=1.5, safety_barg=9.0,
    load_barg=6.9, unload_barg=7.5, source="PROVISIONAL concept size")

INPUTS = ["v_ll", "water_in_c", "water_kgs", "header_barg", "st_loaded", "st_unloaded", "st_running"]
OUTPUTS = ["current_a", "element_c", "water_out_c"]


class World:
    """Everything outside the compressor node: the grid, the cooling water and the air users.
    All of it is random but smooth, with a seed per run."""

    def __init__(self, seed, sag=None, warm_water=None, demand_step=None):
        self.rng = np.random.default_rng(seed)
        self.v_walk = 0.0
        self.t_walk = 0.0
        self.d_level = 0.45
        self.d_next = 0.0
        self.tools = 0.0
        self.sag, self.warm, self.dstep = sag, warm_water, demand_step

    def at(self, t, dt):
        r = self.rng
        # grid: slow random walk inside +-3 %, plus an optional sag (start, end, residual)
        self.v_walk += r.normal(0.0, 0.002) * math.sqrt(dt) - 0.002 * self.v_walk * dt
        v = 400.0 * (1.0 + max(-0.03, min(0.03, self.v_walk)))
        if self.sag and self.sag[0] <= t < self.sag[1]:
            v *= self.sag[2]
        # cooling water: a chiller cycle (period about 9 min, +-0.8 K) and a slow drift of the loop
        # with the plant's heat load and the weather, +-3.5 K (project choice: a process loop at
        # 25 to 32 C, inside the 25 to 35 C range of SCENARIOS.md section 12)
        self.t_walk += r.normal(0.0, 0.03) * math.sqrt(dt) - 0.0003 * self.t_walk * dt
        tw = 28.0 + 0.8 * math.sin(2 * math.pi * t / 540.0) + max(-3.5, min(3.5, self.t_walk))
        if self.warm and self.warm[0] <= t:
            tw += min(self.warm[2], self.warm[2] * (t - self.warm[0]) / max(self.warm[1], 1.0))
        mw = 0.6 * (1.0 + 0.03 * math.sin(2 * math.pi * t / 1300.0))
        # air demand: a shift level that changes every 10 to 40 min, and tools that come and go.
        # Users take 25 to 65 % of the compressor's delivery; with the 20 % leaks the plant needs
        # 45 to 85 %: a compressor sized for its plant (DOE sourcebook: size storage for the peak).
        if t >= self.d_next:
            self.d_level = r.uniform(0.25, 0.65)
            self.d_next = t + r.uniform(600.0, 2400.0)
        self.tools += (r.normal(0.0, 0.08) * math.sqrt(dt) - 0.05 * self.tools * dt)
        lvl = self.d_level
        if self.dstep and self.dstep[0] <= t:
            lvl = self.dstep[1]
        demand = max(0.0, min(0.75, lvl + self.tools)) * COMP.fad_m3min / 60.0
        return v, tw, mw, demand


def simulate(hours, seed, faults=None, **world_kw):
    """Run the compressor. faults: list of (t_start, setter(comp)). Returns (U, Y, raw dict)."""
    w = World(seed, **world_kw)
    c = ScrewCompressor("compressor-1", COMP, ABB_M3BP_180MLB4_22KW)
    c.leak_m3s_bar = 0.20 * COMP.fad_m3min / 60.0 / (P_ATM + 7.0)   # 20 % leaks at 7 bar (DOE: 20-30 %)
    state = {"demand": 0.0}
    c.demands.append(lambda p: state["demand"] * min(1.0, max(0.0, (p - P_ATM) / 5.0)))
    faults = sorted(faults or [], key=lambda f: f[0])
    n = int(hours * 3600 / DT)
    every = int(SAMPLE / DT)
    U, Y = [], []
    t0 = time.time()
    for k in range(n):
        t = k * DT
        while faults and faults[0][0] <= t:
            faults.pop(0)[1](c)
        v, tw, mw, state["demand"] = w.at(t, DT)
        c.step(DT, v, 50.0, tw, mw)
        if k % every == 0:
            st = c.state
            U.append([v, tw, mw, c.reading_barg(), st == "loaded", st == "unloaded",
                      st in ("loaded", "unloaded", "star")])
            Y.append([c.motor.amps, c.t_elem, c.t_water_out])
            bad = c.check()
            if bad:
                raise RuntimeError(f"physics check failed at t={t:.1f}: {bad}")
    wall = time.time() - t0
    return np.array(U, float), np.array(Y, float), {"wall_s": wall, "sim_s": hours * 3600,
                                                     "state": c.state}


def band_flags(y_train, y, k=4.0):
    """Today's engine on a raw signal: median +- k * 1.4826 * MAD of the training data."""
    med = np.median(y_train, axis=0)
    mad = 1.4826 * np.median(np.abs(y_train - med), axis=0) + 1e-9
    return np.abs(y - med) > k * mad


def main():
    print("CP-1 compressor concept check")
    U, Y, info = simulate(12.0, seed=1)
    print(f"  train data: 12 h sim in {info['wall_s']:.1f} s wall "
          f"({info['sim_s'] / info['wall_s']:.0f}x real time), {len(U)} samples")
    m = NodeModel(INPUTS, OUTPUTS, hidden=(32, 32))
    t0 = time.time()
    val = m.fit(U, Y, SAMPLE, epochs=300, patience=25)
    print(f"  trained {m.n_weights()} weights in {time.time() - t0:.1f} s, val mse (std units) {val:.4f}")
    res_train = m.residuals(U, Y, SAMPLE)
    sd = res_train[int(0.8 * len(U)):].std(axis=0)
    print("  residual sd on validation: " + ", ".join(f"{o} {s:.3f}" for o, s in zip(OUTPUTS, sd)))

    def report(name, U2, Y2, t_from_s=0.0):
        r = m.residuals(U2, Y2, SAMPLE)
        i0 = int(t_from_s / SAMPLE)
        z = r[i0:] / sd
        zm = np.abs(z).mean(axis=0)
        z_mean = z.mean(axis=0)
        flags = band_flags(Y, Y2[i0:]).mean(axis=0)
        cov = m.covered(U2, SAMPLE)[i0:].mean()
        print(f"  {name:<38} |z| " + " ".join(f"{v:5.2f}" for v in zm)
              + "   mean z " + " ".join(f"{v:+6.2f}" for v in z_mean)
              + "   raw-band flags " + " ".join(f"{100 * v:5.1f}%" for v in flags)
              + f"   covered {100 * cov:5.1f}%")
        return z_mean, zm, flags

    print("  columns: " + ", ".join(OUTPUTS))
    out = {}
    out["normal"] = report("(a) normal, unseen seed", *simulate(3.0, seed=11)[:2])
    out["sag"] = report("(b) victim: supply sag to 90 % (20 min)",
                        *simulate(1.5, seed=12, sag=(1800.0, 3000.0, 0.90))[:2], 1800.0)
    out["warm"] = report("(b) victim: cooling water +4 K", *simulate(2.0, seed=13,
                         warm_water=(1800.0, 600.0, 4.0))[:2], 2400.0)
    out["warm6"] = report("(b) victim: cooling water +8 K (outside)", *simulate(2.0, seed=13,
                          warm_water=(1800.0, 600.0, 8.0))[:2], 2400.0)
    out["demand"] = report("(b) victim: air demand to 70 %",
                           *simulate(2.0, seed=14, demand_step=(1800.0, 0.70))[:2], 2400.0)
    out["foul"] = report("(c) fault: oil cooler fouling 2x", *simulate(2.0, seed=15, faults=[
        (1800.0, lambda c: setattr(c, "cooler_fouling", 1.0))])[:2], 2700.0)
    out["fric"] = report("(c) fault: motor bearing drag 10 % TN", *simulate(1.5, seed=16, faults=[
        (1800.0, lambda c: setattr(c.motor, "friction_nm", 0.10 * c.motor.rating.t_n))])[:2], 1800.0)
    out["pt"] = report("(c) fault: transducer fails low", *simulate(1.0, seed=17, faults=[
        (1800.0, lambda c: setattr(c, "pt_fault_bar", 5.5))])[:2], 1800.0)
    out["filter"] = report("(c) fault: intake filter dp 0.15 bar", *simulate(1.5, seed=18, faults=[
        (1800.0, lambda c: setattr(c, "filter_dp_bar", 0.15))])[:2], 1800.0)
    return m, out


if __name__ == "__main__":
    main()
