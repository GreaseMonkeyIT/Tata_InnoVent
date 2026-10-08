# twin: the learned plant model (engine overhaul)

This package holds the learned twin of the engine overhaul (`ideas.md` section 12). A node's small
model predicts the node's signals from its inputs. The residual (measured minus predicted) goes
to the engine's statistics. The twin learns from signals only. It never reads the sim code.

| File | Content |
|---|---|
| `node_model.py` | The small model of one node: an exponential filter bank on all inputs, a linear path (ridge) and a small tanh network on what the linear path leaves. It also gives the coverage of the training range. |
| `rhythm.py` | Rhythm discovery v0 (step 2): the dominant period by autocorrelation, the cycle starts and a phase per sample, from one signal alone. A stand-in for the Matrix Profile. |
| `experiments/cp1_compressor.py` | Concept check CP-1 on one machine (below). |
| `tests/test_node_model.py` | Gradient check, extrapolation, save and load, and a short form of CP-1. |
| `tests/test_rhythm.py` | A clean period, noise with no rhythm, and press-1's bend cycle found from its motor current alone (within 5 %). |

Run, from the repository root (numpy needed):

```
python -m pytest twin/tests -q
python twin/experiments/cp1_compressor.py
```

## Concept check CP-1: one machine (2026-10-08)

The question is whether a small model, learned from normal running only, gives a residual that:

- stays flat for normal running that it did not see
- stays flat when the machine is only a victim (a supply sag, warmer cooling water, more air
  demand), although the raw signals move
- grows for a real fault in the machine, on the signal that the fault physically changes

Machine: the screw compressor of `plant/model/compressor.py` on the verified 22 kW motor (a
provisional compressor size for this check). Data: 12 h of simulated normal running, sampled at
1 s, with a varying air demand, a grid voltage inside ±3 %, and cooling water at 25 to 32 °C. The
12 h took 13 s to simulate. The model has 2,660 weights and trained in about 6 s on one CPU core.
Inputs: supply voltage, cooling water temperature and flow, header pressure reading, and the
compressor's load state. Outputs: motor current, element outlet temperature, water outlet
temperature.

Result (mean |z| of the residual in units of its normal spread; the raw band is today's rule,
median ± 4 MAD on the raw value):

| Case | Residual \|z\| (current, element, water) | Raw band flags | Verdict |
|---|---|---|---|
| Normal, unseen | 0.5, 0.5, 0.5 | up to 25 % | quiet |
| Victim: supply sag to 90 % for 20 min | 1.7, 1.4, 1.3 | up to 32 % | quiet |
| Victim: cooling water +4 K | 0.9, 0.9, 0.8 | up to 30 % | quiet |
| Victim: cooling water +8 K (outside the training range) | 1.4, 1.4, 1.3 | up to 63 % | quiet |
| Victim: air demand to 70 % | 0.5, 0.5, 0.4 | up to 15 % | quiet |
| Fault: oil cooler fouling | 0.5, **12.1**, 4.7 | 78 % on the element | element temperature |
| Fault: motor bearing drag 10 % of rated torque | **6.7**, 0.5, 0.5 | 18 % | current |
| Fault: pressure transducer fails low | **27**, **26**, **25** | 0.1 % | all, raw band misses it |
| Fault: intake filter pressure drop 0.15 bar | **4.5**, 4.0, 3.8 (low) | 16 % | all, lower |

Each victim stays quiet and each fault shows on the signal that it physically changes. The raw
band flags normal running and every victim, and it misses the transducer fault. A plot of four
cases is made by the experiment notes (`twin/experiments/out/`, gitignored).

### What the check found and fixed

- The motor's speed step diverged at a star-delta changeover with a 0.1 s step. The motor now
  steps in substeps, implicit only on the stable slopes (regression test in
  `plant/tests/test_part_motor.py`).
- A network alone does not extend a linear relation past its training range (warm water gave a
  false residual). The linear path, fitted first, carries it now.
- The demand generator first asked for more air than the compressor gives. The header pressure
  then fell to zero, a state the model had not seen. The demand now fits a compressor sized for its
  plant.

### Limits of this check

- One machine, with its controller state as an input. The press (requirement 4: cyclic machines
  with no state signal) needs the rhythm discovery of step 2 first.
- The sim tags have no measurement noise or meter accuracy yet. Real meters add both.
- Short residual spikes at the load and unload edges (a few seconds, up to |z| 7 in the sag case)
  need the two-minute gate of today's statistics (step 5) before a verdict.
- The coverage test (training range of every filtered input, 10 % margin) is too strict: it marks
  warm-water cases as outside the range although the residual stays quiet. It needs a softer
  rule (for example a distance to the training data).
- The compressor size is provisional. The final compressor-1 uses the Atlas Copco GA 37 data and
  a 37 kW motor datasheet.

## Concept check CP-2: a cyclic machine with no state signal (2026-10-08)

press-1 (an LVD PPEB 320/40 class press brake) gives no load or state signal. Rhythm discovery takes
the period from the motor current alone (36.5 s; the cycles vary with the operator's handling
time, so the period is the median interval between cycle starts). The phase input is causal: the
time since the last cycle start in a bank of radial basis functions. The model has 6,120 weights.
Training: 12 h, psu-a voltage drifting ±3 %, loop water 27 to 34 °C, handling 15 to 40 s per bend,
plate UTS per batch anywhere in S355's 470 to 630 MPa (not measured, as in a real shop). A 10 Hz
current channel gives the duration of the working part of each cycle (step 1, faster data).

| Case | Residual by phase (mean z) | Working part of a cycle | Reading |
|---|---|---|---|
| Normal, unseen | all within ±0.5 | 3.9 s (normal 3.9 s, spread 0.15 s) | quiet |
| Victim: psu-a sag to 92 % | all within ±0.3 | 3.9 s | quiet |
| Victim: loop water +3 K | within ±1.3 | 3.9 s | quiet |
| F1 ram guide friction +5 % of force | **return: current +5.9, pressure +4.9**; bend +0.5 | 3.1 s (z -5.4, the cycle shape changes) | machine fault |
| F2 pump wear (leakage x4) | within ±1.0 | **4.4 s (z +3.4): the bend stretches** | machine fault |
| F15 harder plate (UTS 680 MPa) | bend +1.9 only, return quiet | 4.0 s (z +0.7) | material load: a healthy return and a normal cycle |

The split between F1 and F15 is physical: friction acts in every move of the ram, a harder plate
only loads the bend. F2 does not change any pressure; it shows only in the duration of a cycle
part, which is why step 2 makes the durations signals of their own.

Run: `python twin/experiments/cp2_press.py` (about 1 min). Short form: `test_cp2_press_short_form`.
