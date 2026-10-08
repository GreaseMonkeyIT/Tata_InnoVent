"""Concept check CP-2: a cyclic machine with no state signal (ideas.md 12.1 requirement 4).

press-1 (plant/model/press_brake.py) has no load/unload state like the compressor. Its small model
gets the phase from rhythm discovery (twin/rhythm.py): the period learned from the motor current
of the training data, then a causal "time since the last cycle start" in a generic basis.

Questions:
  (a) normal running it has not seen: quiet
  (b) victims: a psu-a sag, warmer cooling water: quiet
  (c) machine faults: ram guide friction (F1) and pump wear (F2): the residual rises
  (d) control case F15, a harder plate: the bend load rises, the return does not. Does the
      residual tell the material from the machine? F1 raises the return phase too, F15 does not.

Training data: 12 h with the psu-a voltage drifting inside +-3 %, the loop water at 27 to 34 C, the
operator's part handling 15 to 40 s per bend, and the plate UTS changing per batch of 20 bends
inside S355's 470 to 630 MPa (the material is not measured: real shops do not measure it per plate).

Run:  python twin/experiments/cp2_press.py      (from the repo root)
"""
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from model import catalog as C                                      # noqa: E402
from model.press_brake import PressBrake                            # noqa: E402
from twin.node_model import NodeModel                               # noqa: E402
from twin.rhythm import OnlinePhase, segment_durations              # noqa: E402

DT = 0.05
SAMPLE = 0.5        # the small model's data rate
FAST = 0.1          # the fast current channel for the cycle-part durations (ideas.md 12.3 step 1)
OUTPUTS = ["current_a", "pressure_bar", "oil_c"]


def simulate(hours, seed, sag=None, warm=None, fault=None, uts_fixed=None):
    rng = np.random.default_rng(seed)
    p = PressBrake("press-1", C.PPEB_320_40, C.ABB_37KW, t_oil0=45.0)
    p.motor.w = p.w_n
    v_walk, tw_walk = 0.0, 0.0
    bends_seen, uts = -1, 510.0
    rows, phases, fast = [], [], []
    every = int(SAMPLE / DT)
    every_fast = int(FAST / DT)
    for k in range(int(hours * 3600 / DT)):
        t = k * DT
        v_walk += rng.normal(0, 0.002) * math.sqrt(DT) - 0.002 * v_walk * DT
        v = 428.0 * (1 + max(-0.03, min(0.03, v_walk)))
        if sag and sag[0] <= t < sag[1]:
            v *= sag[2]
        tw_walk += rng.normal(0, 0.03) * math.sqrt(DT) - 0.0003 * tw_walk * DT
        tw = 30.5 + max(-3.5, min(3.5, tw_walk))
        if warm and t >= warm[0]:
            tw += warm[1]
        if p.bends != bends_seen and p.phase == p.IDLE and p.t_phase < DT * 1.5:
            bends_seen = p.bends
            p.handling_s = rng.uniform(15.0, 40.0)
            if p.bends % 20 == 0:
                uts = uts_fixed or rng.uniform(470.0, 630.0)
            p.plate.uts_mpa = uts_fixed or uts
        if fault and t >= fault[0]:
            fault[1](p)
        p.step(DT, v, 50.0, tw, C.PPEB_320_40.water_kgs)
        if k % every_fast == 0:
            fast.append(p.motor.amps)
        if k % every == 0:
            rows.append([v, tw, p.motor.amps, p.p_bar, p.t_oil])
            phases.append(p.phase)
    a = np.array(rows)
    return a[:, :2], a[:, 2:], np.array(phases), np.array(fast)


def main():
    t0 = time.time()
    U, Y, ph, Yf = simulate(12.0, seed=1)
    print(f"CP-2 press: 12 h sim in {time.time() - t0:.1f} s, {len(U)} samples")
    rp = OnlinePhase.learn(Y[:, 0], SAMPLE)
    print(f"  rhythm: period {rp.period:.1f} s from the current alone")

    def inputs(U2, Y2):
        P = OnlinePhase(rp.mid, rp.period).run(Y2[:, 0], SAMPLE)
        return np.hstack([U2, P])

    names = ["v_ll", "water_in_c"] + [f"phase_rbf{i}" for i in range(12)]
    m = NodeModel(names, OUTPUTS, hidden=(48, 48), taus=(2.0, 10.0, 60.0, 600.0))
    t0 = time.time()
    m.fit(inputs(U, Y), Y, SAMPLE, epochs=150, patience=15)
    print(f"  trained {m.n_weights()} weights in {time.time() - t0:.1f} s")
    r = m.residuals(inputs(U, Y), Y, SAMPLE)
    split = int(0.8 * len(U))
    d_train = segment_durations(Yf, rp.mid, FAST)
    d_med = np.median(d_train)
    d_mad = max(1.4826 * np.median(np.abs(d_train - d_med)), FAST)     # floor: one fast sample
    print(f"  working part of a cycle: median {d_med:.1f} s (MAD {d_mad:.2f} s) over {len(d_train)} cycles")
    sd = {g: r[split:][ph[split:] == g].std(axis=0) + 1e-9 for g in ("bend", "return", "idle")}

    def report(name, U2, Y2, ph2, Yf2, t_from):
        r2 = m.residuals(inputs(U2, Y2), Y2, SAMPLE)
        i0 = int(t_from / SAMPLE)
        out = []
        for g in ("bend", "return", "idle"):
            mask = ph2[i0:] == g
            z = (r2[i0:][mask] / sd[g]).mean(axis=0) if mask.any() else np.zeros(3)
            out.append(z)
        d = segment_durations(Yf2[int(t_from / FAST):], rp.mid, FAST)
        dz = (np.median(d) - d_med) / d_mad if len(d) else 0.0
        print(f"  {name:<34} " + "  ".join(
            f"{g}: I {z[0]:+5.1f} p {z[1]:+5.1f} oil {z[2]:+5.1f}" for g, z in zip(("bend", "return", "idle"), out))
              + f"  | work part {np.median(d) if len(d) else 0:.1f} s, z {dz:+5.1f}")
        return out + [dz]

    res = {}
    res["normal"] = report("(a) normal, unseen", *simulate(2.0, 11), 600)
    res["sag"] = report("(b) victim: psu-a sag to 92 %", *simulate(1.5, 12, sag=(1800, 2700, 0.92)), 1800)
    res["warm"] = report("(b) victim: loop water +3 K", *simulate(2.0, 13, warm=(1800, 3.0)), 3000)
    res["f1"] = report("(c) F1 ram guide friction +5 %F", *simulate(1.5, 14, fault=(
        1800, lambda p: setattr(p, "friction_extra_n", 0.05 * C.PPEB_320_40.force_kn * 1e3))), 1800)
    res["f2"] = report("(c) F2 pump wear (leak x4)", *simulate(2.0, 15, fault=(
        1800, lambda p: setattr(p, "leak_factor", 4.0))), 1800)
    res["f15"] = report("(d) F15 harder plate (UTS 680)", *simulate(1.5, 16, uts_fixed=680.0), 600)
    return m, res


if __name__ == "__main__":
    main()
