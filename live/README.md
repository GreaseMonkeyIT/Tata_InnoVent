# live: the plant and the engine in real time

This package runs VISR live: the plant runs on the wall clock, and the engine reads it as it runs.
It is the direct path. The plant gives its readings straight to the engine, with no PLC layer
between them. The PLC layer comes after this path works.

| Step | What | Status |
|---|---|---|
| 1 | The plant service: the plant on the wall clock, its readings over HTTP | Done (`plant_service.py`) |
| 2 | The streaming engine: the node models one sample at a time, the same alarms and roots as the batch engine | Done (`engine.py`, `engine_service.py`) |
| 3 | The console in live mode, with Grafana on the live readings | Done (the console's live mode, the floor recording of the plant service, the replay exporter's live mode) |
| 4 | Containers on forge, apart from the proof-of-concept stack | Ready, not yet run on forge: `LIVE=1 deploy/console-preview.sh` runs the console, Grafana, the exporter and the plant and engine services as containers on `visr-preview`; the two services share one image, `visr-overhaul/live:<tag>` (`live/Dockerfile`: Python 3.11, numpy, scipy, networkx and numba from PyPI, pinned; a non-root user). Tested in a sandbox (2026-10-29): `live/tests` in the image, `--check` in the image equal, the plant at 1,000 times real time, the engine on a synthetic pack. `LIVE_VENV=1` runs the services from forge's venv, as before |
| 5 | The soak: 24 h of normal running, then one live run per fault | Started: the live score of the incidents (`score.py`), a soak on forge's preview |

## The plant service (`plant_service.py`)

The service runs one plant run of the score harness (`eval/nodes.py`): `new_plant(seed)`, the
warm-up, the lead, the step `DT` and one sample every `SAMPLE_S`. It reads the tags of the plant
documents (`plant/model/wiring.py`) through the virtual instruments, as `record` reads them. So the
live readings of a seed are the readings that the harness scores, value for value.

The plant warms up at full speed (9 h of plant time in about 50 s on a laptop core). From the end of
the warm-up, plant time follows the wall clock, times the speed. The plant steps at about 700 times
real time on one laptop core, so the speed has a limit of 1,000. If the plant cannot keep up,
`/clock` shows the lag.

The engine reads the readings only. The fault log is the ground truth. It is for the operator and the
scorer, never for the engine.

```
python -m live.plant_service --seed 60000 --speed 1 --port 9110
python -m live.plant_service --seed 60000 --fault F6:0.5:600              # a fault 600 s after the warm-up
python -m live.plant_service --check 1 --fault F6:0.5:600                 # the equivalence check, no server
```

The default seed is the first validation day, which the engine never trained on. The service refuses
a seed of the test split: the test split runs only at the freeze (`ideas.md` 16.2). The service binds
to 127.0.0.1 by default.

### HTTP

| Request | Answer |
|---|---|
| `GET /info` | The run: seed, split, day of the year, warm-up, lead, sample period, tags, commands and the documents (wiring) |
| `GET /clock` | Plant time, time of day, speed, anchor, lag, warm-up state, the last sample index |
| `GET /samples?from=i&limit=n` | The readings from sample `i`, at most 3,600 rows: `[i, plant s, [one value per tag]]`, in the order of `tags` |
| `GET /metrics` | Prometheus text: `visr_value{tag, station}` for each number of the last sample (the metric of the console preview), `visr_plant_seconds`, `visr_plant_speed` |
| `GET /faults` | The fault log: fault, severity, magnitude, onset, state, the plant times on and off |
| `POST /fault` | `{"fid": "F6", "severity": 0.5}`, and `"onset_s"` (seconds after the warm-up) if the fault is for later. Without it, the fault starts now. |
| `POST /fault/clear` | `{"fid": "F6"}`: an active fault goes back to healthy, a planned fault is cancelled |
| `POST /speed` | `{"speed": 20}`: the new speed starts from the current plant time |
| `GET /healthz` | `ok` |

The service keeps the last 4 h of readings (`--keep-h`): the lead of 2 h that the engine's filters
need, the replay window of the counterfactual root, and a margin. The JSON keeps Python's `NaN` token,
so a value goes through unchanged to a Python client.

A fault takes its magnitude from the range rule of every fault (`plant/model/fault_ranges.py`) and its
duration from the fault registry, as in a harness run. Only the faults of the registry (F1 to F16)
exist.

### The equivalence with the batch run

`compare(seed, hours, faults)` steps the live plant offline and compares its readings with `record`
of the same run, simulated again without the cache. The comparison is exact: every tag, every sample,
the documents, the lead and the fault magnitudes.

| Check | Runs | Result (2026-10-21, laptop) |
|---|---|---|
| `live/tests/test_plant_service.py`, 2 min warm-up | no fault, F6, F3, F11, the pair F6 + F13 | equal |
| `--check 1`, the full 9 h warm-up, seed 60000 (10,800 samples) | no fault, F6, the pair F11 + F8 | equal |

The per-sample instrument reader (`Instruments.stream` in `plant/model/instruments.py`) gives the
readings of `read_column` bit for bit. It shares the instrument rule with `read_column`, and it draws
each instrument's noise from the same stream in blocks.

One batch detail is copied on purpose. `simulate` applies the range rule to the plant that it runs,
before the first step, and the rule moves the cooling pump's operating point. This gives a small
start transient: up to 2.5 kW on the incomer in the first seconds, under 1e-9 kW after 5 min, and at
most 1e-10 of a value after the 9 h warm-up, far under the instruments' resolution. A fault
that is planned at the start does the same here, so the run equals the batch run. A fault that the
operator adds during a run takes the rule from a separate plant and leaves the running plant alone.

## The streaming engine (`engine.py`)

The engine of `eval/nodes.py`, one sample at a time. `StreamNode` takes a trained node of a campaign
and keeps the state that `Node.alarms` builds over a whole run: the filters of the inputs, the
standardized residual, the two-sided CUSUM sums on the mean, the CUSUM on the variance, the alarm
latches and the change points. After the residual, every step is the batch arithmetic in the same
order. The residual equals the batch residual to rounding only (the batch filters run as a block scan,
and the batch model multiplies whole matrices): the largest difference on the V30 runs is 2e-9 of a
residual spread. The rules `cusum`, `cusumk` and `cusumkv` (the default) stream. The gate rule does not.

The batch scores a run from the true onset of its fault. The live engine does not know the onset, so
it works with incidents. Each machine (a machine alarms when one of its nodes does) that starts to
alarm does one of four things:

1. It stays in its own incident if that incident is still open (an alarm that goes and comes back).
2. It joins the incident whose root is not named yet. These are the candidates of the graph rule: the
   machines that alarm within 120 s of the first.
3. It is a victim of an incident when a machine upstream of it in the documents (not in a cycle with
   it) alarms now or started to alarm in the last 120 s, or when a machine in a cycle with it (the
   cooling water loop) alarms in an open incident. The documents cannot order two machines of a cycle.
4. Else it opens a new incident.

When the engine names the root of an incident and an older open incident has the same root, the twin
explains the new alarms by that root: the new incident's machines join the older incident as victims,
and its record keeps `merged_into`. On the 42 runs of the check, the cycle rule and the merge cut the
incidents that follow the first one in the 26 single-fault runs from 24 to 17, and change no other
number.

An option, off by default (`Engine(second_roots=True)`, `check --second-roots`): a machine of a named
incident whose CUSUM sum crosses its limit with a change that began after the incident's 120 s, and
no machine upstream of it with an alarm or a crossing in the 120 s before that change, opens its own
incident (the onset-free form of the batch's pair rule). On the 42 runs it passed one more pair (4 / 5
against 3 / 5) and added 17 incidents to the single-fault runs, 11 of them naming another machine, so
it stays off.

120 s after an incident opens, the engine names its root by the batch rules, on the alarms since the
incident opened: the counterfactual of `eval/cf.py` (the engine's rule since LOG-124) on the samples
it has kept, and the graph rule of `score_fault` as the cross-check. Without a counterfactual answer it
takes the graph rule's root. An incident closes 10 min after the last alarm of its machines (a project
choice). Several incidents can be open at a time.

The engine keeps the samples of the last 8 h for the counterfactual, the length of a batch run (the
lead and 6 h). Up to that length the counterfactual sees the whole run, as in a batch run. After it the
oldest samples go, and the first 2 h of the kept samples warm the filters, as the lead of a batch run.

The counterfactual needs the free-replay variance of each output (`sig2`), which `eval/cf.py` takes
from the calibration days of the campaign. The model pack holds it with the trained nodes:

```
python -m live.engine pack eval/results/<campaign>                        # writes live-pack.pkl there
python -m live.engine check eval/results/<campaign> --workers 14          # streaming against batch
```

### The check against the batch engine

`check` runs both engines on the saved runs of a campaign: the alarms of every node at every sample,
the change points, the roots of the incident that opens at the batch's first alarm after the onset,
and the live engine's own answer (the first incident that opens at or after the onset).

| Runs | Alarms | Roots at the batch's first alarm | True root named |
|---|---|---|---|
| 42 runs of V30 (5 normal days, 2 runs of each fault, 5 pairs), laptop, 2026-10-22 | equal in every run | runs with no alarm on at the onset: graph rule 23 / 23, counterfactual 20 / 20 equal | batch 21 / 26, live 22 / 26 |
| Every scored run of V30 (660), forge | see LOG-127 | | |

In 3 of the 26 fault runs a false alarm was on at the onset. There the batch counts the machine in
false alarm as the first to alarm after the onset, and the live engine opens a separate incident for
the fault. In one of these runs (F2, seed 62001) the batch names the washer and the live engine names
press-1, the true root. The live engine also shows its false alarms: 11 incidents before an onset in
the 37 fault and pair runs, and 4 in the 5 normal days of 6 h.

## The engine service (`engine_service.py`)

```
python -m live.engine_service --pack eval/results/<campaign>/live-pack.pkl --plant http://127.0.0.1:9110 --port 9111
```

The service reads the plant service's `/info` and `/samples` only, never its fault log. It refuses a
plant whose documents (feeds, commands, measures and asset of every node) differ from the documents of
the models. It steps the engine through every sample in order. If it starts after the plant service
has dropped the start of the run, the first 2 h of the samples it gets are its lead, and `/state` says
so (`late_start`).

| Request | Answer |
|---|---|
| `GET /state` | The last scored sample and its plant time, every node's alarm and coverage, the machines in alarm, the open incidents, an error if the feed stopped |
| `GET /incidents` | Every incident: when it opened (sample and plant time), its root (`named`, from `named_cf` or `named_graph`), the counterfactual's top scores, the candidates and victims, when it closed |
| `GET /metrics` | Prometheus text: `visr_alarm{node, station}`, `visr_machine_alarm{station}`, `visr_incidents_open`, `visr_engine_sample` |
| `GET /healthz` | `ok` |

The counterfactual runs inside the engine's step (tens of seconds on a long run). The engine then
falls behind the plant and catches up at about 1,000 samples per second. The HTTP answers come from a
copy of the state, so they never wait for it.

A trial on the laptop (2026-10-22): seed 60000 with F6 (an air leak, severity 0.6) 600 s after the
warm-up, the plant at 100 times real time, the engine with the V30 pack. The plant warmed up in 51 s.
The engine opened an incident 21 s of plant time after the onset and named compressor-1, the true
root, by both rules. It ran about 8 s of plant time behind the plant.

## The console on the live plant (steps 3 and 4)

The plant service keeps the console's floor recording when it runs with `--floor`: the station
states (a change list), the tracking table of the frames and engines, and the values every 2 s from
the start of the lead (`FloorRecorder` in `plant/floor/replay.py`, which `record()` uses too; the
recording that `replay.py` writes is byte for byte the same as before). `GET /floor?since=t` gives
what came after plant time `t`, in the form of `replay.json`, so the console appends it to its player
(`Replay.append` in `dashboard/app/floor/replay.js`). The tracking table only reads the plant, so the
readings of `/samples` do not change (test). The service keeps 4 h of value rows, and all states and
tracking events.

The console finds the live plant at `/api/live/plant` and the engine at `/api/live/engine`
(`dashboard/README.md`, live mode). Its verdict is the engine's newest open incident with a named
root: the root, the candidates and victims as the affected stations, the engine's reason in one
sentence (the counterfactual, or the graph rule), and a strength (the share of the root's
counterfactual score in the two best scores). Grafana gets the live values from the replay exporter
in live mode (`VISR_LIVE`): it appends the same floor recording and maps wall time to plant time with
the plant's clock.

On forge, `LIVE=1 bash ~/VISR_Overhaul/deploy/console-preview.sh` runs the whole chain:

| Part | Where |
|---|---|
| Plant service (`--floor`) | container `visr-live-plant` (image `visr-overhaul/live:<tag>`), `visr-live-plant:9110` on `visr-preview`, published on 127.0.0.1:9110 of forge only (the operator's `POST /fault`), `data/weather/` mounted read-only, log `docker logs visr-live-plant` |
| Engine service | container `visr-live-engine` (the same image), `visr-live-engine:9111`, published on 127.0.0.1:9111 of forge only, the newest `live-pack.pkl` with its campaign folder mounted read-only, log `docker logs visr-live-engine` |
| Replay exporter (live) | container `visr-replay`, Grafana's datasource |
| Grafana | container `visr-grafana` |
| Console | container `visr-console-preview`, port 3121 on the Tailscale address; its nginx proxies `/api/live/*` to the two service containers by name, GET only (any other method gets 403) |
| All containers | no restart policy; the two service containers check `GET /healthz` every 10 s (`docker ps` shows the state) |
| Fallback, `LIVE=1 LIVE_VENV=1` | the two services in the screens `visr-live-plant` and `visr-live-engine` from `~/visr-venv`, on 172.17.0.1:9110 and 172.17.0.1:9111, logs `/var/tmp/visr-live-*.log`; nginx turns to 172.17.0.1 when the container names do not resolve |

The services listen on the docker0 address only, so only forge and its containers reach them. A
fault comes from a shell on forge (`POST /fault` to 172.17.0.1:9110); the console's proxy refuses
every request except GET (403).

## The live score and the soak (step 5)

The batch score reads a run from the true onset. `score.py` scores the live engine's incidents
instead, on the rows of `check` and the labels of the campaign:

```
python -m live.engine check eval/results/<campaign> --workers 14 --out rows.json
python -m live.score rows.json eval/results/<campaign> --out live-score.md
```

| Measure | Rule |
|---|---|
| Root named | the first incident that opens at or after the onset names the true root |
| Root in an incident | the true root is a candidate, a victim or the root of an incident from the onset |
| Delay | from the onset to that first incident |
| False incidents | every incident of a normal run, and every incident before an onset, per hour |
| Controls | runs of F14 to F16 with an incident after the onset |
| Pairs | every real root named by an incident after the first onset |
| Extra incidents | the incidents of a single-fault run after the first one from the onset |

Every rate has its n and a 95 % Wilson interval. The soak runs the live chain at 1 times real time on
forge (`LIVE=1 deploy/console-preview.sh`, no planned fault): the incidents of a day of normal running
(`GET /incidents` of the engine service), the engine's lag behind the plant (`/state`) and the memory
of the two services.

## Tests

```
python -m pytest live/tests -q
```

- `test_plant_service.py`: the equivalence of the plant service above, a fault added during the run
  (the plant before the onset stays as it was, clear and cancel work, bad requests fail), the HTTP
  service on a free port at 200 times real time (the served readings equal an offline run), the floor
  recording (the readings stay the same, the appended segments give the whole recording), and the
  refusal of a test-split seed.
- `test_engine.py`: a trained node streamed against `Node.alarms` (both CUSUM rules, a confirmation
  time, a gap in a meter, a text command and a text output, and with command changes across a
  counter reset), with the same
  change points; the incidents with scripted alarms (a candidate, a victim, new roots, the closing
  times, the same-root merge, the cycle victim); the kept samples against the run, also past the cap;
  the refusal of the gate rule.
- `test_score.py`: the live score on hand-made incident rows.
- `test_engine_service.py`: the engine service against a stand-in plant service (every sample taken,
  the incidents at the batch's alarms, the metrics), and the refusal of other documents.
