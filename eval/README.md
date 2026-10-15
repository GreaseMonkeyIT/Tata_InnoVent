# eval: the score harness

This package scores the engine against ground truth (`ideas.md` section 16). Before it, VISR had one
pass or fail per scenario and no accuracy number. The harness runs many random trials, scores each
one against the truth that the simulator knows, and reports every rate with its sample size and a
95 % Wilson interval.

| File | Content |
|---|---|
| `metrics.py` | The metric definitions: verdict, detection, top-1, top-3, mean reciprocal rank, delay, false alarms per hour, trip card precision, recall, lead time and ETA error. Pure functions. |
| `legacy.py` | One trial of today's engine (v1, the box engine) on the box's plant simulator (`plant/sim/main.py`), through `correlation/service.py` as on the box. |
| `harness.py` | The campaign: plans the trials, runs each one in a fresh process, scores them and writes the report. |
| `machines.py` | The machine score (`ideas.md` 17.6): each machine's small model, trained on normal running only, against random normal, victim, control and fault runs on the new plant model. |
| `tests/test_metrics.py` | Unit checks of the metrics on hand-made timelines. |
| `tests/test_machines.py` | Unit checks of the machine detector (the gate on residuals, the episodes). |

## Run

From the repository root, with the Python that has numpy (see the test runner note in the plant
README):

```
python -m eval.harness --runs 30 --calm 10
python -m pytest eval/tests -q
```

Each run writes `eval/results/<UTC time>-<split>/`: `runs.jsonl` (the raw pass timelines),
`report.json` and `report.md`. The results folder is gitignored. Copy a number into a document only
with the name of its run folder.

## Rules

1. **Two seed ranges.** The tuning split (seeds from 10,000) is for development. The test split
   (seeds from 900,000) is held out. The harness runs it only with `--freeze`, at the freeze of
   phase 4 (`ideas.md` 13.1). Nobody looks at the test split before then.
2. **Name what was scored.** Each report records the git commit, whether the tree had changes, and
   a hash of the engine settings, the engine source and the simulator.
3. **Intervals.** Every rate has its n and a 95 % Wilson interval.
4. **One process per trial.** `service.py` keeps its memory in module state, so a second trial in
   the same process would inherit the first one's baselines.

## How a trial runs

1. **Learn:** the plant runs normally for 30 min. The engine runs a pass every 10 s (the box's
   `ENGINE_INTERVAL`), and its baselines learn and mature.
2. **Lock:** every baseline is locked, as the operator does on the box after a soak (LOG-089).
3. **Calm:** a random calm stretch of 5 to 20 min, so that the fault onset is not at a fixed time.
4. **Fault:** the scenario's fault at a random magnitude.
5. **Watch:** 15 min (25 min for PS2, whose cascade is slow). The harness emulates OpenPLC's thermal
   latch and records each trip.

A calm run (PS0) watches 1 h with no fault. The calm stretch before each onset also counts as calm
time for the false alarm rate.

| Scenario | Fault | True root | Magnitude range (scripted value) |
|---|---|---|---|
| PS1 | press-1 bearing friction | press-1 | 1.5 to 2.3 (1.9) |
| PS2 | compressor-1 pressure transducer fails low | compressor-1 | reads 4.5 to 6.5 bar (5.5) |
| PS5 | cool-1 pump wear | chiller-1 | pump health 0.35 to 0.60 (0.45) |
| PS7 | supply dip | incomer-1 | 80 to 90 % of nominal (85 %) |

The ranges are project choices: about 20 to 35 % around the scripted fault.

## Definitions

- **Verdict:** the first root that holds rank 1 for 3 consecutive passes (30 s). An operator acts
  on a root that holds, not on one pass.
- **Detection:** a verdict inside the watch window.
- **Top-1:** the verdict is the true root. **Top-3:** the true root is in the top 3 of the verdict
  pass. **MRR:** the mean of 1 / rank of the true root in the verdict pass (0 when it is absent).
- **Delay:** seconds from the onset to the verdict pass.
- **False alarm:** a verdict while the plant is calm. One episode counts once.
- **Trip card:** true when its machine trips inside the watch window. A trip is covered when a card
  for that machine came before it. Lead time and ETA error are measured on the first card.

## Scope and limits

- Only the plant families run here (rail voltage, coolant temperature). PS3 (network), PS4A and PS4B
  (SCADA integrity) and PS6 (edge plane) run in other layers on the box and need a box campaign.
- The API's slow-drift trip rule (BOOK.md chapter 9) is not in the engine, so it is not scored here.
- This is a tuning-split score. The engine was tuned on these scenarios, so it is still an optimistic
  number. The held-out split and the public datasets (`ideas.md` 16.4) give the honest one.

## Machine score (`machines.py`)

    python -m eval.machines --machine compressor-1 --runs 30
    python -m eval.machines --machine press-1 --runs 30

1. **Train:** the machine's small model (`twin/node_model.py`) learns 12 h of normal running on the new
   plant model: a random supply drift, cooling water, air demand or operator handling, and S355
   material batches. The model sees only signals, never the simulator.
2. **Trials:** 30 runs each of normal running, every machine fault at a random magnitude and onset,
   every victim case (the machine only suffers: a supply sag, warmer water, more air demand) and every
   control case (press-1: a plate above the S355 range, a material change and no machine fault).
3. **Detect:** today's engine statistics on the residuals. Per output, z = residual / its normal
   spread (press-1: a spread per discovered cycle phase). A channel alarms when the 35th percentile of
   |z| over the last 120 s is above 3.5, the box's `GATE_Q` and `DEV_K`. press-1 also has a cycle-part
   duration channel (CP-2): it alarms when the median of the last 5 working parts is more than 3.5
   spreads from normal.
4. **Score:** detection rate and median delay per fault, the first channel that alarmed, and false
   alarms in normal, victim and control time and before each onset. Rates have 95 % Wilson intervals.

The fault, victim and control magnitude ranges in `machines.py` are project choices around the CP-1
and CP-2 levels. The tuning split uses seeds from 20,000, the test split from 920,000 (`--freeze`).

On forge the campaigns run outside the cluster at low priority, from the synced worktree
`~/VISR_Overhaul`, with a user venv `~/visr-venv` (numpy, pytest):

    screen -dmS visr-eval bash -c "cd ~/VISR_Overhaul && nice -n 10 ~/visr-venv/bin/python -m eval.machines --machine press-1 --runs 30 > /var/tmp/visr-eval-press.log 2>&1"
