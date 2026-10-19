# twin: the learned plant model (engine overhaul)

This package holds the learned twin of the engine overhaul (`ideas.md` section 12). A node's small
model predicts the node's signals from its inputs. The residual (measured minus predicted) goes
to the engine's statistics. The twin learns from signals only. It never reads the sim code.

| File | Content |
|---|---|
| `node_model.py` | The small model of one node: an exponential filter bank on all inputs, a linear path (ridge) and a small tanh network on what the linear path leaves. It also gives the coverage of the training range. |
| `rhythm.py` | Rhythm discovery v0 (step 2): the dominant period by autocorrelation, the cycle starts and a phase per sample, from one signal alone. A stand-in for the Matrix Profile. |
| `twin.py` | The twin (step 8): the trained node models joined by the plant's documents. A free replay (the normal plant) and a reset replay (the counterfactual of step 10). |
| `tests/test_twin.py` | The twin on small linear nodes: a chain, a reset that keeps the downstream residual, a cycle that converges, the edges that are not chained, the order of the components. |
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

## CP-3: the twin and the counterfactual root (2026-10-19, LOG-122)

This check covers steps 8, 9 and 10 of `ideas.md` 12.3 on the full plant. The node models are the
models of `eval/nodes.py` with the defaults of LOG-118. No model changes for this check.

### The twin (step 8)

`twin.py` joins the node models along the documents (`plant/model/wiring.py`). An edge A -> B
exists for each tag that node A measures and node B takes as a feed. Each edge has the field
`basis` of `ideas.md` 18.3. Every edge of this version has the basis "documented".

An edge is chained when its tag is a number and not an output of the source machine's own PLC
control loops. On a chained edge, the replay gives node B the prediction of node A, not the meter.
PLC commands, controller outputs and the tags that no node measures (the site load, the weather)
stay as measured. They are the exogenous inputs of the twin.

The models are causal, so a replay starts from the measured history and runs forward. The cooling
water loop (compressor-1 and cool-1) is a cycle of chained edges. The replay solves it with 4 passes
of waveform relaxation. In the last pass the largest change was 0.036 (units of the signal).

Two replays:

- **Free:** every value is a prediction. This is the normal plant for the measured exogenous inputs.
- **Reset:** the nodes of one machine take their predictions (their residual set to 0). Every node
  downstream keeps its own residual and takes the change of its inputs through its model. This is the
  counterfactual of step 10.

The free replay error against the one-step error (the residual), on 32 windows of 17 min on the
normal tuning days: 1.00 for every node with no chained input, as it must be. 0.98 to 1.01 for
compressor-1, press-1, lmp-1, press-2 and conveyor-1. 1.17 to 1.24 for cool-1. 1.29 and 1.31 for
plasma-1 (the air header pressure comes from the compressor-1:air model). The loop nodes are higher:
2.2 to 2.6 for the cooler valves and the ced-1 burners, 6.6 for the compressor-1 load state. A small
error in a predicted temperature or pressure moves the predicted switching time of an on and off
controller.

### The counterfactual root (step 10)

`eval/cf.py` scores it on the saved models of a campaign (`eval/nodes.py --save-models`). The
method follows Budhathoki, Minorics, Bloebaum and Janzing, "Causal structure-based root cause
analysis of outliers", ICML 2022. Each node is a mechanism plus its own noise. Here the mechanism is
the node model and the noise is its residual.

1. The window starts 900 s before the first alarm and ends 120 s after it.
2. The trouble is the squared deviation of each node output from the free replay, divided by the free
   replay variance of that output on normal days, summed over the window. Only the samples inside the
   node's training range count, as for the alarms.
3. The targets are the machines that alarm within 120 s of the first alarm (the candidates of the
   graph rule).
4. For each machine (a suspect), the twin replays the window with that machine reset. The root is the
   suspect whose reset removes the most trouble at the targets.

A target that only reacts to a change upstream keeps its trouble when it is reset. It loses that
trouble when the upstream machine is reset. So the root can be a machine upstream of the first alarm,
also one that has not alarmed yet.

### Results (single faults, root named of 130, tuning / validation)

| Factory | Faults found | Graph rule (120 s) | Counterfactual |
|---|---|---|---|
| 0 (`20261018T215624Z-nodes`, `cf-c3`) | 107 / 110 | 94 / 100 | 98 / 106 |
| 1 (`20261018T231048Z-nodes`, `cf-c4`) | 105 / 104 | 90 / 93 | 98 / 100 |
| 2 (`20261019T000312Z-nodes`, `cf-c5`) | 106 / 110 | 94 / 99 | 98 / 106 |

Factory 0, per fault: the counterfactual gains F8 (cool-1 pump wear, 8 / 6 to 10 / 10: the
cooling water cycle that the graph rule cannot order), F6 (7 to 9 on tuning), F4 and F12 (one more
on validation each). It loses no fault against the graph rule. The variant with every machine as a
suspect and a standardized score gives 94 / 98: a machine that is not near the alarm then wins
with a chance excursion. The suspects must reach the alarmed machines.

Two flaws of the first version (c1: 86 / 95) and their fixes:

- The trouble counted samples outside the training range. A victim whose inputs leave the range
  (press-1 and lmp-1 when the cooling water fails) gave very large scores. Now only covered samples
  count.
- The trouble left out the controller outputs. A control fault shows mainly there (F13, the washer
  heater control: 1 / 10). Now every output counts, as for the CUSUM on the mean.

### Fault pairs

A pair passes when every real root is named and detected. With the c4 windows (factory 0), the
counterfactual passes 35 / 35 of 50, the graph rule 41 / 38. It names fewer wrong machines (46 / 56
against 52 / 59), but it fails F7 + F8 in 5 / 3 of 10 runs. The first fault's cascade makes the
victims cross their limits again and again. These crossings take the windows before the second
fault starts, and a window where the targets are outside their training range names a chance
machine. Two changes did not help (c5): a gap of 30 min between the crossing windows of one machine
(34 / 33), and a name only above the suspect's largest removal on a normal window (21 / 19). For
pairs, the graph rule stays.

### Two-step training (step 9)

`eval/joint.py` refits the 15 nodes that take a chained input (one pass, 13 min on forge). The twin
with these models (`cf.py --twin-models`, `cf-c6joint`) names the same roots, fault by fault. The
free replay error (root mean square, joint pass over step 1, normal tuning windows) is lower for
10 nodes: the compressor-1 load state 0.38, the press-1 cooler valve 0.67, the ced-1 burners 0.68
and 0.83, plasma-1 0.74 and 0.79, compressor-1 0.92. It is higher for 4: cool-1 1.28 to 1.30, its
fan loop 1.24, the conveyor-1 current 1.37 and the lmp-1 oil temperature 1.08. cool-1 is in the
cooling water cycle: in one pass each model learns against the step 1 models upstream of it, and in
the cycle both sides change. The joint pass is therefore not the default. Next: more passes, or one
gradient through the chain.

### Limits

- The twin chains numbers only. PLC commands and controller outputs stay measured, so a replay does
  not close a control loop.
- One check run of the method on our own sim, and one reference check against DoWhy-GCM (below,
  `eval/dowhy_check.py`).
- The window lengths (900 s before the alarm, 120 s after) are project choices. They were not
  tuned.

### Reference check against DoWhy-GCM (2026-10-19, LOG-124)

DoWhy-GCM is the reference implementation of the method (`ideas.md` 15). `eval/dowhy_check.py` runs
it in two settings.

Synthetic models (8 nodes, an outlier in the noise of one known node, 100 trials per kind; scored:
the trials where a node alarms). Both methods use the mechanisms that DoWhy fits:

| Mechanisms | Scored trials | Our rule names the root | DoWhy-GCM names the root | The two agree |
|---|---|---|---|---|
| linear | 53 | 52 | 52 | 53 |
| nonlinear | 40 | 39 | 38 | 39 |

The logic of our rule gives the reference's answer. The first version of the static port missed
every outlier in a source node (36 and 23 right): its normal values kept the source nodes as
observed, as the twin does for its exogenous inputs. In these models a source node has a noise of its
own, so the check now puts it at its median.

Plant, the 116 validation fault runs with an alarm (`20261018T215624Z-nodes`; DoWhy on one sample at
the largest deviation in the 120 s after the first alarm, no time history), roots named:

| Method | Right of 116 |
|---|---|
| DoWhy-GCM as published (static) | 102 |
| Our rule, static form, on DoWhy's mechanisms | 99 |
| Our counterfactual on the twin (`cf-c3`) | 106 |
| Graph rule | 100 |

DoWhy-GCM and our counterfactual name the same machine in 109 of 116 runs. In every run where DoWhy
is right, our counterfactual is right too. Our counterfactual is right where DoWhy is wrong in 4 runs
(F2, F8 twice, F12), and both are wrong in 10. The static form of our rule loses F6 (3 of 10): the
evidence of the air leak is in the compressor's load state, a source node that its normal values keep
as observed. The twin models that state with the loop node, so the dynamic counterfactual has it.
DoWhy samples at random, so a second run can move its counts by about 1.

Adopted (operator, 2026-10-19): the counterfactual is the engine's root of a single fault
(`eval/nodes.py --root cf`, the default). The graph rule is the fallback, the cross-check and the
rule for fault pairs.
