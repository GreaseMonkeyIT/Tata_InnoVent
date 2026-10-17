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

From the repository root, with the project venv `.venv` (laptop: Python 3.11.9 from pyenv-win, made
with uv from `correlation/requirements.txt` plus pytest, numba and pymodbus 3.6.9; forge:
`~/visr-venv`, Python 3.14). The venv is in `.gitignore` and `.stignore`, so each machine has its own:

    uv venv --python <pyenv 3.11.9 python.exe> .venv
    uv pip install --python .venv/Scripts/python.exe -r correlation/requirements.txt pytest numba pymodbus==3.6.9

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

## Node score, one method for every node (`nodes.py`, 2026-10-15)

`machines.py` is superseded: its benches had a hand-written input list, model size, filter set and
phase input per machine, and scored faults that are not on the agreed list. `nodes.py` builds every
node from the plant's documents (`plant/model/wiring.py`: a node's feeds, PLC commands and
measures) with one rule: the same model size and filters for all, categorical commands one-hot,
the residual spread per command state. The data are whole-plant runs with the site weather and the
material flow, on separate days of the year per split:

| Split | Seeds | Use |
|---|---|---|
| train | from 30,000 | the models learn here |
| tuning | from 40,000 | development |
| validation | from 60,000 | a change stays only if it holds here too |
| test | from 940,000 | only at the freeze (`--freeze`) |

Days of the year (`day_of_year`, since LOG-114): the year is cut into blocks of 7 days, and the
blocks go to train, tuning, validation and test in turn. No run starts on the last day of a block,
so a run that crosses midnight stays in its own block. Each split has 78 days, with 6 to 9 days in
every month. Run k of a split goes to the month `MONTHS[k % 12]`: 4 training days fall in January,
April, July and October, and 12 training days cover all 12 months. The seed still sets the start
hour, the noise and the material flow. A run can be at most 24 h long, warm-up included.

Warm-up and lead (since LOG-116): the plant runs 9 h before a run is scored. That is 3 x its
slowest time constant, the transformer top oil (180 min), so about 5 % of the gap from the start
state is left. It was 30 min, and the oil still heated through the scored hours. The last 2 h of the
warm-up (the longest filter time constant) are recorded as the run's lead. The lead runs through the
model's filters, so they start from history, as in a plant that runs all the time. The lead is not
fitted, calibrated or scored. The onset times of the faults count from the end of the warm-up.

Training days (`fit_and_calibration`, since 2026-10-17): the training runs come in rounds of 12,
one day per month. Every third round sets the residual spreads, and the other rounds are the days
the models fit on. The default `--train 36` gives 24 fit days (2 per month) and 12 spread days
(1 per month), so both cover the year. With fewer than 36 runs, the last quarter sets the spreads,
and its months are then missing from the fit.

    python -m eval.nodes --hours 6 --train 36 --runs 8 --splits tuning,validation

Alarm rules (`--rule`, since 2026-10-17). The default is `cusumk` (LOG-115: it held on the
validation split). The other rules stay as options. All work on the standardized residuals z (the
residual divided by its spread for the PLC state):

- `gate`: the rule of today's engine. A node alarms when at least 79 of the last 120
  samples have |z| above 3.5 (the 35th percentile of the window above 3.5). The window forgets. A
  machine that works in short cycles (a press bend of 8 s in about 41 s) never fills it.
- `cusum`: a two-sided CUSUM on each output, S+ = max(0, S+ + z - 0.5) and S- = max(0, S- - z - 0.5).
  The node alarms when a sum passes its limit h, and the alarm ends when the sums fall back to 0.
  Samples outside the training range add nothing. Each node takes h from its calibration days: the
  split-conformal quantile of the per-day maxima at alpha 0.1 (with 12 days, the largest), never below
  the textbook 5. With 12 calibration days the limit can only hold a node to about one false alarm
  day in 13.
- `cusumk` (the default): CUSUM with a k per output from the slow bias of the calibration days: the
  0.99 quantile of |mean z| over 30 min windows, never below 0.5. Then h as for `cusum`.
- `both`: the gate or plain CUSUM (the combined Shewhart-CUSUM scheme).

LOG-115 has the scores of the four rules on the days of campaign 8.

The engine view (since 2026-10-17): `view()` saves each run once as the engine sees it, after the
documents and the instruments, in `eval/cache/view/` (`VISR_VIEW_DIR` moves it). Text states are
stored as integer codes with their names (`Text`). The main process of a campaign holds only the
paths. Each node worker loads only its own tags, and a scored run only while it scores that run.
The values are the ones `record()` gives, so a report does not change. The key holds a hash of the
plant code, `wiring.py`, `instruments.py` and the encoding, so a change to any of them makes new
views.

Loop nodes and the air header (since LOG-117): the documents (`plant/model/wiring.py`) list the
PLC control loops of the plant (`loops`): compressor-1 load and unload on the header pressure, the
cooler valves of press-1 and lmp-1 on their oil temperature, the tower fan on the basin
temperature, the washer-1 heater on its tank temperature, and the ced-1 burners on the tank and oven
temperatures. Each loop gives a node `<asset>:loop`, with the measured variable in and the controller
output out. A text output (the compressor's state) becomes one output per category. A machine's own
node takes its loop outputs as inputs. The air header is a node too (`compressor-1:air`): the
compressor's state and the modelled users in, the transducer at the receiver and the gauges at the
users out.

Coverage rule: an input that is an output of the asset's own control loops does not count for
coverage. When a fault drives a machine's own controller where it never went (compressor-1 loaded
for hours under F6 or F7), that is a symptom. Before, the node said "don't know" there.

Faults are scored per machine: a machine alarms when the first of its nodes alarms, and the root
must be that machine.

Each fault trial also reports the delay from the root's first activity: the first second after the
onset at which a state command of the root (a phase, a state) changes. A fault in an idle press acts
only when the press moves.

First job: false alarms per node in normal running. Fault trials follow when the magnitude rule of
the fault registry (`plant/model/faults.py`) is decided.

Details of the method (2026-10-16):

- Training: 12 days, 9 to fit and 3 held out for the residual spreads and the R² (in-sample
  residuals are small by construction and make every new day alarm). Spreads from the covered
  samples when at least 600 are covered, else from all; a node with no finite spread stops the run.
- "Don't know": no alarm where a node's inputs leave their training range (10 % margin); the report
  gives the share of time covered.
- One filter set for every node: 5 s, 30 s, 2 min, 10 min, 30 min and 2 h.
- The data are read through the virtual instruments (`plant/model/instruments.py`), with the ranges
  of the metered quantities from the plant's documents (`wiring.instrument_ranges`). Set
  `VISR_INSTRUMENTS=0` for exact signals.
- The gate is today's (35th percentile of |z| over 120 s above 3.5), computed as a rolling count: at
  least 79 of 120 samples above 3.5. That is exact except one sample short of it, where the count
  rule gives no alarm (5 of 290 alarm samples in a check on 5,000 random samples, never an extra one).
- Fault trials: `--faults F1,F2,...` with the magnitude rule of `plant/model/fault_ranges.py`. Per
  fault: detection on the true root node, the root alarming first (top-1), the delay, the number of
  nodes alarmed, and the alarms before the onset.

### Speed (2026-10-16)

These changes make an engine iteration faster. They do not change the method.

- **Simulated-day cache.** The tags of a run depend only on the plant code, the weather file, the
  seed, the length and the fault. `record()` saves every tag of a run, before the instruments, to
  `eval/cache/<seed>-<key>.npz`. A later campaign loads the run when the key matches. The key holds
  a hash of the files in `plant/model/` (except `wiring.py` and `instruments.py`), the weather file
  and the source of `simulate()`. A change to the plant makes new keys, so a stale day never loads.
  The documents (`wiring.py`) and the instruments are applied at load time, in the same order as
  before, so their noise is the same. A 2.7 h day takes about 2.7 MB. `VISR_CACHE=0` turns the
  cache off and `VISR_CACHE_DIR` moves it. The folder is in `.gitignore` and in `.stignore`, so
  each machine keeps its own cache.
- **Nodes in parallel.** Each node trains and then scores every run in its own process, with its
  own tags only. The nodes are independent, so the report is the same as in one process.
- **Memory.** A run's columns are numpy arrays, 8 bytes per value. As lists of Python floats they
  took about 32 bytes per value: campaign 6 (348 runs of 6 h) grew to 12 GB on forge.
- **Filter bank.** `FilterBank.run` uses block matrix products, not a loop over the samples
  (`twin/node_model.py`). It equals the loop to 1e-14 of the signal (test: 1e-9).
- **Proof.** A small campaign (`--hours 2.2 --train 2 --runs 1 --faults F1,F6 --fault-runs 1`) ran
  with the code before these changes, then fresh, from the cache, and from the cache with the nodes
  in parallel. The three new reports are identical. Against the old code, every alarm, episode and
  fault result is identical. The R² values differ in the 11th digit, from the rounding of the
  filter bank. Wall time: 111 s (old), 103 s (fresh), 20 s (cache), 7 s (cache and parallel nodes).
- **forge.** A 32-run campaign (`--hours 6 --train 12 --runs 4 --faults F1,F6,F9 --fault-runs 2
  --workers 14`): 289 s fresh, 50 s from the cache, identical reports. A 6.5 h run takes 86 s (143 s
  in campaign 6, before the plant speed paths of `plant/model/README.md`). About 7 MB per cached day.
