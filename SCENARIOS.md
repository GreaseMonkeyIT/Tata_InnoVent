# SCENARIOS: the PS fault set and its interface contract

**Status:** built and tested on the laptop, 2026-09-19 (LOG-068). Live on the edge box since 2026-09-19 17:39, and
the PS0 soak passed there (LOG-069). The first box proof run (LOG-070) passed PS3, PS4A, PS4B, PS6 and the refusals.
PS1, PS2 and PS5 lost a race with the OpenPLC thermal trips. After the fix (section 2.0 and the 2A merge vote in
section 4.4), the second box proof run (LOG-071) passed PS0, PS1 with Execute, PS3, PS4A, PS4B, PS5, PS6 and the
refusals. PS2 still fails: its two hops do not show at the same time. The code follows this file. If the code and
this file disagree, fix one of them in the same change.

**Box, 2026-09-22 (LOG-078, LOG-079):** the images with LOG-073 to LOG-077 went live at 14:08, and the PS0
soak passed at 14:54. The third box proof run passed all seven scenarios and every refusal. PS2 passed for the
first time: root `compressor-1` with the loop hop after 304 s.

**Supply boundary (2026-09-20, this change).** The plant had no supply above its rails. `Rail.step` used a
constant source voltage, so every sag in the model started inside the plant. A power quality meter at the
distribution board measures the supply above the plant load, and that measurement separates an external cause
from an internal one. This change adds the supply object, the PS7 scenario, and the common-mode rule that roots
an external cause. Sub-second dip events stay out of scope. See section 11.

**PS7 does not pass yet.** The physics, the domain, and the common-mode rule are built and unit-tested.
The offline replay (section 10) still roots `press-1` under PS7, not `incomer-1`. The cause is named in
section 4.5 under "Known gap": a constant-power machine answers a dip with more current, and the source
path reads that answer as an aggressor. The next change fixes the source path. Until then, treat PS7 the
way this file treats PS2: built, honest about failing, and not in the deck.

Each scenario shows one way a plant fails between machines. Each scenario has a real incident as its
anchor. The plant plane is a physics model and says so. The inference on top of it is real.

## 1. The set

| ID | Name | Fault owner | Plane | Real anchor | What VISR must show |
|---|---|---|---|---|---|
| PS0 | Steady plant | none | all | none | No root, no findings, no integrity finding |
| PS1 | Rail-sag cascade | plant-sim | plant | Milford Haven refinery, 1994: 275 alarms in the last 11 minutes | Root `press-1` along rail `psu-a` |
| PS2 | A stuck load trips the chiller | plant-sim | plant | Azure Australia East, 2023: the chillers tripped and the cooling cascade followed. PS2 reproduces the cascade. PS7 reproduces the trigger. | Root `compressor-1`. Chain: rail `psu-b`, then `chiller-1` trips, then loop `cool-1`, then the cooled machines. Trip forecasts. |
| PS3 | Control network storm | plant-sim (segment model) | network | Browns Ferry Unit 3, 2006: network traffic stopped both recirculation pump drives | Root `hmi-gw` along segment `field-1`. The stamping cell link lags and drops. |
| PS4A | Setpoint write with no record | `rogue-ews` pod | integrity | Stuxnet 2010, FrostyGoop 2024, Ukraine grid 2015 | Integrity finding `unsigned_write` on `FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT` |
| PS4B | Current report contradicts the feeder | plant-sim | integrity | Stuxnet replayed normal values. Buncefield 2005: a stuck gauge. | Integrity finding `current_balance` on rail `psu-a`, channel `FLEET.PLC_STAMPING.PRESS_1.AMPS` |
| PS5 | Coolant pump degradation | plant-sim | plant | LG Polymers, Visakhapatnam, 2020: the tank heated with no sensor at the top | Root `chiller-1` (its cooling shortfall leads the loop), and trip forecast cards before the 78 °C trip |
| PS6 | The monitor runs out of memory | tag-server | edge | Toyota, 2023: a full disk stopped 12 plants. Northeast blackout, 2003: the alarm system stopped with no warning. | Forecast card class `leak` on `tag-server`. After the kill, the console shows that the SCADA view is blind. |
| PS7 | The supply dips and the plant loses cooling | plant-sim | plant | Azure Australia East, 2023: an external supply disturbance tripped the chillers. | Root `incomer-1`, never a machine. Chain: supply `incomer-1`, then every rail, then `chiller-1` trips, then loop `cool-1`, then the cooled machines. |

**IDs:** upper case, `^PS[0-9]+[A-Z]?$`. The API and plant-sim change a lower-case id to upper case.
**Display names (LOG-078):** the console and the deck show "Scenario 1" for PS1. The fault rows show the
number only. The API, the scripts, the ledger, and this file keep the PS IDs.

**Refusals beat:** not a PS id. The API refuses a state change with 401, 403, or 409 and writes a ledger row.
`deploy/refusals.sh` shows all three (section 9).

## 2. Scenario mechanics

### 2.0 Thermal time constants

OpenPLC trips a cooled machine at 78 C and latches the trip. The engine holds a plant root until a signal stays
out of band for most of 2 min (`GATE_Q=35`), so a root takes about 80 s. A scenario must leave that time before
the first trip. The time constants are press-1 120 s, press-2 165 s, cnc-1 90 s, and furnace-1 270 s (LOG-070).
At the old 30 to 90 s, press-1 tripped 77 s after PS1, before the verdict. The temperature noise per tick is
`0.05 * sqrt(40 / tau)` C, so every machine keeps a stationary spread of about 0.22 C. Steady temperatures do
not change.

Offline with these constants: PS1 trips press-1 at about +220 s when nobody acts, and a derate at +100 s
prevents the trip. PS2 shows the loop hop at +130 s, before the first trip at +160 s. In PS5 every tripped
machine gets its own card first (furnace-1 86 s ahead, press-1 117 s ahead).

### 2.1 PS1 (unchanged)

`press-1.friction` goes to 1.9. Reset puts it back to 1.0.

### 2.2 PS2: power sag trips the chiller

**Fault:** `compressor-1` duty becomes 1.0 (stuck on), as before.

**New physics in plant-sim:**

1. `chiller-1` gets a motor overload relay (class `Overload`). The relay uses no random numbers.
   - `pickup`: current above `1.02 × 22.0 A` (the rated current).
   - Above pickup, `heat += 1.0 × dt`. Else `heat = max(0, heat - 0.5 × dt)`.
   - The relay trips at `heat >= 90`. The trip latches until `/reset`.
   - Env overrides: `CHILLER_OL_PICKUP=1.02`, `CHILLER_OL_UP_PER_S=1.0`, `CHILLER_OL_DOWN_PER_S=0.5`, `CHILLER_OL_TRIP=90`.
   - A normal 60 s compressor window adds about 61 heat, then drains in the idle 240 s. It never trips.
   - PS2 keeps rail B low, so the relay trips about 30 s to 90 s after the fault, by cycle phase.
2. A trip sets `chiller-1.tripped = True`. The existing tripped branch cuts its current.
3. `CoolantLoop` gets a `driver` (`chiller-1`). While the driver is tripped,
   `flow = max(flow_nominal × pump_health × CHILLER_RESIDUAL_FLOW + noise, 5.0)`, with
   **`CHILLER_RESIDUAL_FLOW=0.60`** (was 0.45 until 2026-09-20, LOG-073).
   Keep one `gauss` draw per step, so the random stream does not change.
4. `plant_pump_health_ratio` stays equal to `pump_health`. In PS2, flow falls while pump health stays 1.0.
5. `/reset` clears the relay heat and the trip.

**Why the residual flow is 0.60.** At 0.45 the loop fell to 54 L/min, three cooled machines reached the 78 C
latch, and their contactors opened. That unloaded rail B, the sag went away, and the rail hop died before the
loop hop settled. That is the PS2 failure in one sentence. `correlation/tests/ps2_lab.py` measures the window
where the root is `compressor-1` AND the loop hop is up, one process per setting:

| `CHILLER_RESIDUAL_FLOW` | first pass | window | machines that latched | max temp |
|---|---|---|---|---|
| 0.45 (old) | t+60 | 120 s | press-1, press-2, furnace-1 | 78.0 |
| 0.50 | t+60 | 140 s | press-1, furnace-1 | 77.5 |
| 0.55 | t+60 | 160 s | furnace-1 | 78.0 |
| **0.60** | **t+60** | **180 s** | **furnace-1** | **77.7** |
| 0.65 | t+60 | 180 s | furnace-1 | 77.8 |
| 0.70 | t+60 | 180 s | none | 76.5 |
| 0.75 | t+80 | 160 s | none | 74.0 |

0.60 sits at the start of the plateau and keeps one real trip, so the trip forecast still predicts an event
that arrives. Above 0.70 nothing latches and the cards promise a trip that never comes. **The lab is not the
box:** it runs two signals where the box runs six, and PS2 passes in the lab even at 0.45. The table measures
the improvement between settings. Only a box proof run settles the box. On 2026-09-22 it did: PS2 passes at 0.60 (LOG-079).

**Load budget:** extra running load on `psu-b` above about 30 A can trip the relay in normal operation.
Keep new cells on `psu-c`. The fleet API default rail is `psu-c`.

**Anchor note.** At Azure Australia East the trigger was an external supply disturbance. PS2 starts inside the
plant, so it reproduces the cascade after the chiller trip and not the trigger. PS7 reproduces the trigger.
State both claims this way on the console and in the deck.

### 2.3 PS3: control network storm

**Model:** a `Segment` in plant-sim. The queue model is simulated. Its effect on the real Modbus field link is real.

- Segment `field-1`: capacity 1000 frames/s, buffer 50 frames, retransmit timeout 0.2 s that doubles per loss.
- Members: every cell whose PLC is on the field network (default: `plc-stamping`), plus the talker `hmi-gw`.
- The OpenPLC trip link is **not** on the segment. The safety network stays separate.
- Env: `FIELD_SEGMENTS="field-1|hmi-gw|1000|50"` (segment name, talkers, capacity, buffer). Empty disables the model.
- Each cell sync costs 8 frames (4 requests, 4 responses).
- `hmi-gw` offers about 20 frames/s at rest, with small noise from its own `random.Random(seed)`.
- Utilization `ρ = Σ offered / capacity`. The model gives M/M/1/K drop probability and queue delay.
- The segment wraps the real `ModbusTcpClient` of each member cell. Before each real request, the wrapper waits
  the queue delay plus the retransmit delay. If the total delay reaches `CELL_TIMEOUT_S`, the wrapper raises
  `ConnectionError("simulated segment drop")`. The cell then disconnects as it does today.
- All waits use `cell.stop.wait()`. Nothing sleeps under `_lock`.

**Fault:** `hmi-gw` offered load ramps from 20 to 1500 frames/s over 45 s. Reset sets it back to 20.

**Knock-on:** a cell link that drops goes to its fallback (fail-open or idle). With `DERATE_PCT` at 100, the
stamping presses run at the same speed either way. With an active derate, the storm undoes the relief.

### 2.4 PS4A: setpoint write with no record

**Injector:** Deployment `rogue-ews` in namespace `plant`. It runs `vplc/ews.py` on the `skn/vplc` image, which has
python-snap7. Label `app: rogue-ews`, `visr/role: fault-injector`. It is not a PLC and has no `app: vplc` label.

- Control HTTP on port 8090. Every POST needs header `X-Scada-Token` equal to `SCADA_WRITE_TOKEN`.
  An empty token disables the endpoints (403).
- `POST /fault/PS4A`: one S7comm `db_write` to `plc-stamping:102`, DB1 byte 292 (`%MW10`, press-1 `DERATE_PCT`),
  value 30. It does not use the SCADA write path, and it writes no ledger row.
- `POST /reset`: clears the `active` flag. It writes nothing to the PLC.
- `GET /state`: `{active, target, address, value, last_write_ts, last_error}`.
- Env: `EWS_TARGET=plc-stamping.fleet.svc.cluster.local`, `EWS_PORT=102`, `EWS_DB=1`, `EWS_BYTE=292`, `EWS_VALUE=30`.

**Physics:** press-1 runs at 30 %. Its current falls to about 13 A, and rail A rises.

**API reset of PS4A:** the API writes the task default (100) through the SCADA write path and appends a `restore`
row with evidence `reason: "scenario reset"`. Then it calls `rogue-ews /reset`.

### 2.5 PS4B: current report contradicts the feeder

**Fault in plant-sim:**
- `press-1.friction` goes to 1.4. The real current rises to about 61 A. press-1 stays below the trip.
- `press-1` field AMPS channel to its PLC replays the last 30 s of real current, recorded when the fault starts.
  The replay is indexed by wall time. It changes only the value in `cell_frames` (`%IW0` of `plc-stamping`).
- The OpenPLC base channel (`MW8`) and every `plant_*` metric keep the true value.
- Reset clears the replay and puts friction back to 1.0.

**Feeder meter:** plant-sim adds an independent feeder meter per rail.
- `/state` → `rails.<name>.amps`: the sum of device currents on the rail, computed at export time under `_lock`.
- Metric `plant_feeder_current_amps{namespace="plant",pod="<rail>"}`.

### 2.6 PS5 (unchanged)

`LOOP.pump_health` goes to 0.45. Reset puts it back to 1.0.

### 2.7 PS6: the monitor runs out of memory

**Injector:** the tag server.
- `POST /chaos/leak` body `{"mib_per_s": 0.5}`. Header `X-Scada-Token`. An empty token gives 403.
- A thread appends written bytes (`b"\x01" * n`, never `bytearray(n)`) every 0.25 s. The flag lives in memory
  only, so an OOM kill clears it and the pod restarts clean.
- `POST /chaos/reset`: stops the thread and frees the buffer.
- `GET /chaos`: `{active, leaked_mib, mib_per_s, started_at}`.

**Expected:** the working set climbs from about 31 MiB toward the 128 MiB limit. The engine forecast card appears
after the working set passes half of the limit. At 0.5 MiB/s the kill comes about 3 minutes after the fault.

**Honesty:** before the tag-server ServiceMonitor cutover, the engine plant plane reads plant-sim directly.
The claim is "the SCADA view and the historian go blind, and the console says so". It is not "VISR goes blind".

### 2.8 PS7: the supply dips and the plant loses cooling

**Why this scenario exists.** PS1 and PS2 both start inside the plant. `Rail.step` used a constant source
voltage, so the model could not make an external cause, and the engine could never root one. A power quality
meter at the distribution board measures the supply above the plant load. Indian plants meter the board this
way far more often than they expose a PLC there, so this is the common instrument, not the rare one.

**Fault:** the supply target falls to `SUPPLY_DIP_PCT` of nominal and holds until reset.

**New physics in plant-sim:**

1. A `Supply` object named `incomer-1` holds the board voltage. Nominal `SUPPLY_NOMINAL_V`, default 400.0 V.
2. `Supply.step` slews the voltage toward the target at `v_nom / SUPPLY_RAMP_S` volts per second (default
   3.0 s, so 133 V/s). The voltage never jumps inside a tick, but the 15 % PS7 dip (60 V) lands within one
   1 s tick. The engine sees the onset on its 5 s grid either way. A sub-second dip is out of scope
   (section 11). Until 2026-09-21 this line said that the dip took 3 s (LOG-075).
3. Each `Rail` reads `supply.voltage` for its source. The rail equation does not change:
   `voltage = supply.voltage - i_total * r_src + noise`.
4. `Rail.v_nom` holds the fixed nominal. Every threshold and every ratio uses `v_nom`, never the live supply.
   The brownout branch in `Device.step` must compare against `v_nom`. A comparison against the dipped supply
   makes the branch dead code.
5. The supply carries the plant total current: the sum of the device currents over every rail.
6. Reset puts the target back to 1.0.

**What follows, with no new physics:**

- Every rail sags together, by the same volts.
- Constant-power loads answer with more current. The existing brownout branch does this.
- The `chiller-1` overload relay sees the higher current and trips, the same as PS2.
- The loop flow falls to the residual, and the cooled machines heat.

**Expected:** at `SUPPLY_DIP_PCT=0.85` the chiller relay trips 30 s to 90 s after the fault, by cycle phase,
the same band as PS2. The engine needs about 80 s to hold a root at `GATE_Q=35`. PS7 therefore carries the
same two-hop timing risk as PS2 (section 4.4).

**Measured, 2026-09-20 (laptop).** The physics work. A dip to 0.85 drops every rail together, `psu-c` by the
board's own drop of 60.0 V, and the chiller relay trips inside 100 s from every cycle phase tested. The
verdict does not work yet: the offline replay roots `press-1`. See section 4.5, "Known gap".

**What VISR must show:** root `incomer-1`, never `compressor-1` and never `chiller-1`. A verdict that names a
machine is a failure of this scenario, not a partial pass.

**Honesty:** the supply model is a physics model and says so, the same as the rest of the plant plane. The
public anchor is the Azure Australia East incident. No field data drives this scenario or sets its values.

### 2.9 Model rules from the review before the sim code freeze (LOG-075)

The sim code is frozen from 2026-09-21. The review fixed six rules. None of them changes the healthy steady
state, a PS verdict, or the random stream. The offline replay and the PS2 lab give the same results as before.

1. **A repeat fault does nothing.** `POST /fault/<id>` for an active fault answers 200 "already active" and
   changes nothing. A second PS4B apply recorded the replay from the faulted current, so the vPLC word matched
   the truth and the PS4B evidence was gone. The console can send one fault twice.
2. **Throughput recovers at any voltage.** A machine that the voltage does not slow climbs back 2 points per
   tick after a trip, at any rail voltage. Rail A idles under the brownout line, so the old rule kept press-1
   near 0 % after a trip while it drew full current. This showed only when the press ran without its PLC.
3. **A current is never below 0 A.** The noise put an idle 4 A labeler below 0 A on about 1 tick in 160.
4. **A stall is never one step.** The physics step is the wall time since the last pass, capped at 5 s
   (`MAX_DT_S`). One 120 s step overshot the thermal model, and in a compressor window it would give the
   chiller relay more than a trip's worth of heat at once. A cell machine needs a `tau` of 10 s or more
   (`MIN_TAU_S`), so the thermal step stays stable.
5. **A restart keeps the plant warm.** A cooled base machine starts at its healthy steady temperature. A cold
   start put a warm-up ramp of up to 20 minutes on four machines after every plant-sim restart. A new cell
   machine still starts at 35 C, because it starts idle.
6. **One silent client cannot block the sim.** The HTTP server runs one thread per request, with a 10 s read
   timeout. The old single-threaded server let one silent connection freeze `/metrics` and `/healthz`.

**Kept on purpose (calibration, not bugs):**
- Rail A idles at about 360 V, under the 368 V brownout line (0.92 of 400 V, LOG-032). cnc-1 and qa-scanner-1
  therefore run at about 89 % throughput with no fault, and the rail A machines draw about 2 % more current.
  Every PS threshold and every box result is calibrated on this.
- `plant_heat_load_watts` is `heat_k x current`, a heat index and not real watts (press-1 shows about 23).
  The engine is scale-free, so the value works. A rename touches the aggregator, the tag server, and the engine.
- The plant runs 24/7 with no shifts and no planned stops.

## 3. Plant-sim interface additions

### 3.1 `/state`

- `rails.<name>.amps` (feeder meter).
- `supply`: `{"name": "incomer-1", "volts", "nominal_volts", "amps", "target_pct", "dipped"}`.
  `dipped` is true while `target_pct` is below 1.0.
- `devices.chiller-1.trip_reason`: `"overload"` while tripped, else null. Other devices: null.
- `segments`: `{"field-1": {"capacity_fps", "utilization", "latency_ms", "drop_ratio",
  "members": {"hmi-gw": {"kind": "talker", "offered_fps", "latency_ms"},
  "plc-stamping": {"kind": "plc", "cell": "stamping", "offered_fps", "latency_ms", "sync_age_s", "failures"}}}}`.
- `active_faults` lists every active plant-sim fault, including `PS2`, `PS3`, `PS4B`.

### 3.2 `/domains`

- `loop:cool-1` adds `chiller-1` (the loop driver) before `cool-1`.
- New `net:field-1`: `["hmi-gw", "plc-stamping", "field-1"]`.
- New `rail:incomer-1`: `["incomer-1", "psu-a", "psu-b", "psu-c"]`. The supply and its rails share one
  electrical medium, so the prefix stays `rail`. The domain suffix names the medium, as it does for `psu-a`.

### 3.3 `/metrics` (all with `namespace="plant"`)

| Metric | pod | Meaning |
|---|---|---|
| `plant_feeder_current_amps` | each rail | Independent feeder meter |
| `plant_bus_voltage_volts` | `incomer-1` | Board voltage above the plant load. Same family as the rails. |
| `plant_current_draw_amps` | `incomer-1` | Plant total current. The only source signal in `rail:incomer-1`. |
| `plant_supply_nominal_volts` | `incomer-1` | Fixed nominal. Display and ratio only. Never a forecast pair. |
| `plant_motor_tripped` | `chiller-1` | 1 while the overload relay holds the chiller off. Not an OpenPLC trip. |
| `plant_overload_ratio` | `chiller-1` | Relay heat / trip level. Display only. Never a forecast pair. |
| `plant_cooling_shortfall_watts` | `chiller-1` | Heat the loop fails to remove: `Σ_cooled heat_k × I × (1/share - 1)`, never below 0 |
| `plant_net_offered_fps` | each segment member | Frames per second that the member offers |
| `plant_net_latency_ms` | each segment member | Latency that the member sees, with retransmits |
| `plant_net_utilization_ratio` | each segment | `ρ` |
| `plant_net_drop_ratio` | each segment | Model drop probability |

`plant_fault_active` picks up new `FAULTS` keys on its own.

## 4. Telemetry and engine

### 4.1 `aggregator/queries.yaml`

- **Fix the `mem` query.** On the box, both the `kubelet` job and the `cadvisor-fast` job scrape cAdvisor, so the
  current `sum by` doubles the working set. The tag server showed 63 MiB against a real 31 MiB (2026-09-17).
  New query: `sum by (namespace, pod) (container_memory_working_set_bytes{job="cadvisor-fast",namespace=~"aiops|observability|plant|fleet",container!=""})`.
  `mem` feeds only forecast pairs, so learned baselines do not change.
- Do not change the other plane-1 queries. The learned baselines use their current values.
- New keys: `feeder_current: plant_feeder_current_amps`, `cooling_shortfall: plant_cooling_shortfall_watts`,
  `motor_tripped: plant_motor_tripped`, `net_offered: plant_net_offered_fps`, `field_latency: plant_net_latency_ms`,
  `net_util: plant_net_utilization_ratio`.
- PS7 needs no new query. `incomer-1` rides the existing `bus_voltage` and `current_draw` queries, because
  the exporter labels it `pod="incomer-1"` like any other plant entity. Add display-only
  `supply_nominal: plant_supply_nominal_volts`.

### 4.2 `deploy/engine.yaml`

- `ENGINE_SIGNALS`: add `field_latency`. `psi_io` stays first.
- `PLANT_FAMILIES`: add `field_latency:net`.
- `PLANT_SOURCES`: a family can name more than one source, separated by `|`. A member uses the first source signal
  that it exports. Value: `bus_voltage:current_draw,coolant_temp:heat_load|cooling_shortfall,field_latency:net_offered`.
- `PLANT_DOMAINS`: `loop:cool-1` adds `chiller-1`. Add `net:field-1=hmi-gw,plc-stamping,field-1`.
  Add `rail:incomer-1=incomer-1,psu-a,psu-b,psu-c`.
- PS7 adds no signal and no family. `incomer-1` is a `bus_voltage` victim and a `current_draw` source, so
  the existing family covers it. Only the domain list and the common-mode settings change (section 4.5).

### 4.3 Engine behavior

- The `net:` prefix is a coupling family, the same as `rail:` and `loop:`.
- **Two-hop chain (PS2):** when the root of one family is a victim in another family, the verdict keeps the
  earliest upstream root. For PS2 the root is `compressor-1`, and the edges include
  `compressor-1 → chiller-1` (rail evidence) and `chiller-1 → <cooled machine>` (loop evidence).
- PS1 must still root `press-1`. PS0 must stay silent. Fixture tests prove both.

### 4.4 Engine fixes from the September soak

A 24 h PS0 soak on the box (2026-09-17/18) had 1 quiet line in 1,438. Two causes, both fixed in the engine:

| Cause | Fix | Setting |
|---|---|---|
| The engine restarted while the aggregator ring was nearly empty. The zero padding taught every plant baseline a median and MAD of 0.0, and the storm rule then locked them. | A baseline learns only from a pod whose window has real samples on at least 90 % of the grid. | `BASELINE_MIN_COVERAGE=0.9` |
| The deviation gate used the 90th percentile of the last 2 minutes. Every normal 60 s compressor ON window cleared it, so rail B was a finding in every cycle. | The gate quantile is configurable. At 35, a pod must stay out of band for most of the tail. A duty cycle stays quiet, and a stuck-on load does not. | `GATE_Q=35` |

Also:
- Memory keys keep declared plant names whole. `qa-scanner-1` was stored as `qa` and never matched its own baseline.
- A trip forecast fires only for a pod whose recent level sits above its learned band
  (`FORECAST_BASELINE_FLOOR=1`). Steady coolant temperatures sit at 75 to 85 % of the trip limit, so the
  fraction-of-limit rule alone gave trip cards in a calm plant.
- A plant family pairs its source-only members, so `chiller-1` (no temperature, only a cooling shortfall) shares
  the loop with the machines it cools.
- The merge (`engine/merge.py`) lets a held memory edge vote for root only when its source is a finding of the
  same signal. In the first box proof run, a bus-voltage finding on `cnc-1` let a held coolant edge
  `cnc-1 -> press-1` vote, and the merged root moved to `cnc-1` while both signals ranked `press-1` (LOG-070).

Tests: `correlation/tests/test_enablers.py`, and `test_merge.py` for the merge vote. The offline replay (the real plant physics through the real
service loop, engine started before the ring is full) is the acceptance check before a box run.

### 4.5 The common-mode rule (PS7)

The plant coupling rule pairs a victim with a source that leads it. `PLANT_SOURCES` says
`bus_voltage:current_draw`, so a rail sag needs a machine load that rises first. An external disturbance has
no such load. The rule as it stands cannot root a cause above the plant, and a config change alone does not
fix that. PS7 needs one new rule.

**Trigger.** In one pass, for one family and one domain, all of:

1. Every member of the domain that carries the victim signal has an open deviation, the medium included.
2. The member count is at least `COMMON_MODE_MIN_MEMBERS` (default 3).
3. The onsets fall inside `COMMON_MODE_WINDOW_S` of each other (default 15 s, three grid steps).
4. **(a)** No member other than the medium is the source of a **source-evidenced** (`write`) edge inside the
   domain. **(b)** No member other than the medium has a source-signal onset inside the window.

**Result.** For each deviating member, the rule puts an edge from the medium to that member, with evidence
`["stat", <prefix>, "common_mode"]` and a `common_mode` block that carries the domain, the member count, and
the onset spread. The root then falls out of the existing ranking. The medium is the entity whose name matches
the domain suffix, so `rail:incomer-1` gives `incomer-1`, the same convention that names `psu-a` inside
`rail:psu-a`.

**What the rule may rewrite.** When a whole medium moves at once, every member vector looks alike, so the gate
finds `r` near 1.0 at lag 0 for every pair and picks a direction by tie-break. Between the medium and its own
member that direction is a coin flip. The rule replaces every lag-0 bare edge on a medium-to-member pair,
whichever way the tie-break sent it, so the verdict always carries the honest label. It never touches an edge
that has evidence for its own direction, which means a `write` edge or a non-zero lag. `COMMON_MODE=0` leaves
the pass byte-identical to the pre-rule engine.

**Why the root is honest.** Conditions 4a and 4b are the inference. They rule out every internal cause the
engine can express, and every member that even looks like one. Conditions 1 to 3 say the disturbance is common
to the whole medium. That the medium sits above its members is declared topology, not inference, and the
evidence chip says so.

**One root, not one per rail.** Under PS7 the machines on each rail sag as well, so `rail:psu-a` and
`rail:psu-b` also satisfy conditions 1 to 3. Condition 4b stops them: a machine carries `current_draw`, and a
constant-power load answers the dip, so every machine domain vetoes itself. A rail carries no `current_draw`
at all, so the supply domain stays clean. PS7 therefore gives exactly one root.

**Plane 1 never gets the rule.** The `psi_*` domains are inferred (same node, shared disk), not declared, so
"the medium itself" is not an entity an operator can point at. `common_mode_arg()` returns None for any signal
with no `PLANT_FAMILIES` prefix.

**PS0, PS1, and PS2 must not change.** PS0 opens no deviation, so condition 1 fails. PS1 and PS2 have a
leading machine load, so 4a and 4b both fail. The fixture tests prove all three.

**Settings:** `COMMON_MODE_MIN_MEMBERS=3`, `COMMON_MODE_WINDOW_S=15`, `r_min=0.6` (in code). `COMMON_MODE=0`
turns the rule off.

**Code:** `correlation/engine/common_mode.py`, called from `run_pass` through the `common_mode` argument.
Tests: `correlation/tests/test_common_mode.py`.

**Known gap (2026-09-20): the rule is not enough on its own.** In the offline replay the rule fires and puts
`incomer-1` above `psu-a` and `psu-c`, and `incomer-1 -> psu-b` forms on its own with a real 5 s lag. The
verdict still names `press-1`. A constant-power machine answers the dip with more current, so the source path
sees a rising load on every rail and builds twelve source edges, `press-1 -> psu-a` and `compressor-1 -> psu-b`
among them. Those edges are false. The current rose BECAUSE the voltage fell. Condition 4b stops those machine
domains from claiming common mode, but nothing stops them from outranking the board.

**The fix, and why it is a separate change.** A source edge must not form when the source's current rise is
fully explained by the voltage drop it is supposed to be causing. For a constant-power load the rise tracks
the drop, and the model caps the answer at 1.15 times. A real aggressor goes far past that: PS1 takes press-1
from 42 A to about 80 A, and no sag on that rail explains 1.9 times. This test belongs in `_writer_edge`,
which is the source path that PS1, PS2, and PS4B all depend on, so it needs its own change and its own
regression run. It is also the inference that a board meter exists to make: it separates a load that answers
a disturbance from a load that causes one.

## 5. API interface additions

### 5.1 Scenario catalogue

`GET /api/scenarios` returns a list. Each item:

`{id, name, mechanism, anchor, plane, owner, triggerable, expect, expect_s, active}`

- `owner`: `plant` | `ews` | `scada`. `active` comes from the owner: plant-sim `/state.active_faults`,
  `rogue-ews /state.active`, tag-server `/chaos.active`. An unreachable owner gives `active: null`.
- `expect_s`: seconds to the expected verdict, for the console message.
- Trigger and reset use a dispatch table, not a hard-coded id tuple. Unknown or non-triggerable ids give 501.
- `POST /api/scenarios/reset-all` (operator gate) resets every owner and writes one `reset` row with target `ALL`.
- A reset that fails with a network error writes an `error` row and answers 503.
- Env: `EWS_URL` (default `http://rogue-ews.plant.svc.cluster.local:8090`).

### 5.2 Integrity checks

A new module `api/integrity.py` holds pure functions. A background thread runs them every `RECONCILE_S` (5 s).
The thread starts at app startup when `INTEGRITY_BACKGROUND` is not `0`.

**Unsigned setpoint change:**
- Source: tag-server `GET /fleet`, read directly (a failure marks the pass `blind` and keeps the old state).
- Watch writable rows with quality GOOD. The first sighting is a baseline.
- A change is explained by one of these:
  - an in-memory write intent that the API records just before each SCADA write,
  - an `execute` (`executed`) or `restore` (`restored`) ledger row with the same `evidence.plc`, `evidence.tag`,
    and `evidence.to`, within 60 s before to 15 s after the change,
  - a reset to the task default together with a PLC restart (`runtime.started_at` changed), a new registration,
    or a `load-task`, `run`, or `fleet-create` row for that PLC in the window.
- An unexplained change waits `GRACE_S` (35 s), then opens a finding. A value outside `min`/`max` opens a finding at once.
- One open finding per `(plc, tag)`. The finding closes when the value returns to a ledgered value.

**Current balance:**
- Inputs: plant-sim `/state` (`rails.*.amps`, `devices.*.rail`), tag-server `/fleet` (controller AMPS for cell
  machines), tag-server `/tags` (base `PLANT.<ASSET>.AMPS` for the others).
- `reported = Σ controller-channel AMPS on the rail`. `gap = feeder - reported`.
- A finding opens when `|gap| > max(2 A, 3 % of feeder)` for 3 passes in a row, with every input tag GOOD.
- **Naming the channel:** when an asset has a controller channel and a base channel that disagree by more than the
  threshold, the finding names the controller channel. Otherwise it names the rail only.
- The finding closes after 3 passes back inside the threshold.

**Ledger rows** (actor `visr`, verbs at most 8 characters):

| verb | status | evidence |
|---|---|---|
| `unsigned` | `detected`, then `cleared` | `{plc, asset, tag, address, from, to, observed_at, reason, clients}` |
| `balance` | `mismatch`, then `cleared` | `{rail, feeder_amps, reported_amps, gap_amps, channel}` |

`clients` is best effort: Caretta `caretta_links_observed` clients of the PLC on port 102 other than `tag-server`.

**Exposure:** `GET /api/integrity` returns `{source, checked_at, findings: [{id, kind, status, ...evidence}]}`.
`GET /api/graph` adds an `integrity` key with the open findings.

**Act loop:** `GET /api/actions` adds `blocked: [{asset, plc, reason}]`. An asset whose controller channel has an
open integrity finding gets no proposal. Each `active` derate gets `signed: true|false`.

### 5.3 Health

`/api/health` adds `services.scada` (tag-server `/healthz`) and `services.plant` (plant-sim `/healthz`).
`ok` still means aggregator and engine.

### 5.4 Refusals that write rows

- `DELETE /api/fleet/plcs/<static PLC>`: row `fleet-delete <name> "refused: base PLC"`, then 403.
- `POST /api/fleet/plcs` with an existing name: row `fleet-create <name> "refused: exists"`, then 409.
- `PUT .../task` with a different layout: row `load-task <name> "refused: layout"`, then 409.

## 6. Tag server additions

- `/chaos/leak`, `/chaos/reset`, `GET /chaos` (section 2.7).
- `/tags` → `historian` adds `queue_depth`, `queue_max`, `dropped_batches`.

## 7. Console behavior

- **Fault injection (LOG-081):** the console has no fault controls and no "injected" banner. The fault shell
  `deploy/faults.sh` runs on the box and calls the same endpoints: `trigger`, `reset`, and `reset-all`, with
  the operator token from the Secret `aiops/visr-auth` and the actor `fault-shell`. Its status reads
  `GET /api/scenarios`. The event log says "Scenario 1" (`scenarioName` in `lib/format.js`).
- **Verdict:** an integrity block above the state band when `graph.integrity` has open findings. The block does not
  replace a root. A blind band takes priority when `/api/tags` answers `source: "unavailable"`:
  "SCADA view blind · last good HH:MM:SS · physics tap live".
- **Chain:** the chain line walks the accepted edges from the root through rails, loops, segments, and machines.
- **Event log:** verbs `unsigned` and `balance` render red. `format.js` gives each a detail line.
- **Actions:** an unsigned hold shows a red pill. A blocked asset shows its reason.
- **Assets:** a segment group (latency, drops, members). `chiller-1` shows "tripped · overload".
- **Command bar:** an `integrity` lamp. The refresh clock turns amber after 15 s and red after 30 s without an update.
- **Palette:** no new color. Red means an alarm or an integrity finding. Amber means a warning or STALE.
- **Mocks:** add variants `chain` (PS2), `network` (PS3), `integrity` (PS4A and PS4B), `blind` (PS6).

## 8. Soak

- Default `SCENARIOS="PS1 PS2 PS3 PS4A PS4B PS5"`, unchanged. **PS7 stays out of the default until it
  passes** (section 4.5, "Known gap"): a soak before a submission must not report a known failure.
  Run it on its own with `SCENARIOS=PS7`. Add it to the default in the same change that fixes it.
  `deploy/proof-run.sh` names its scenarios one by one and does not carry PS7 either.
- Per-id windows: `OBSERVE_<ID>` and `COOLDOWN_<ID>` override `OBSERVE_S` and `COOLDOWN_S`.
- Samples add `/api/scenarios` and `/api/plant`. The graph sample carries `integrity`.
- `record.py` scores each id with its own rule:

| ID | A hit in an observe sample |
|---|---|
| PS1 | root `press-1` |
| PS2 | root `compressor-1` |
| PS3 | root `hmi-gw` |
| PS4A | an open `unsigned_write` finding |
| PS4B | an open `current_balance` finding |
| PS5 | an incipient of class `trip` |
| PS6 | an incipient of class `leak` on `tag-server`, or the SCADA view blind |
| PS7 | root `incomer-1`. A root that names a machine counts as a miss, not a partial hit. |

- The report adds a false-positive count from baseline and cooldown samples: any root, integrity finding, or incipient.

## 9. Deploy

- `deploy/rogue-ews.yaml`: the Deployment and a ClusterIP Service `rogue-ews:8090` in `plant`.
  `SCADA_WRITE_TOKEN` from Secret `visr-fleet`. Resources: 25m/64Mi requests, 100m/96Mi limits.
- `deploy/golive.sh` applies it, restarts it, and checks: the catalogue lists every id, plant-sim `/state` rails
  carry `amps`, `segments.field-1` exists, `rogue-ews /state` answers, tag-server `/chaos` answers.
- `deploy/factory-up.sh`: `ONLY` default adds `plant-sim correlation-engine vplc`. The PS0 watcher counts integrity
  findings and incipient cards in its QUIET rule, and it logs a `chiller-1` trip. Step 2b upgrades the `alloy`
  release with the fixed `deploy/values/alloy.yaml` (`ALLOY=0` skips it). Its old config had semicolons, so the pod
  restarted in a loop inside `observability`, a namespace that the engine watches. Step 5 moves an old soak log aside.
- `deploy/refusals.sh`: shows a 401 (no token), a 409 (execute with a stale proposal id), and a 403 (delete the
  static base PLC, only after it confirms the label `visr/managed=static`). It prints the new ledger rows and
  `chain_ok`. It reads the operator token from the cluster Secret, like `factory-up.sh`.
- After any signal-set change, run `factory-up.sh` with the default `WIPE=1`, then soak PS0 again.

## 10. Tests

- plant: the relay never trips over 3600 s of normal cycles with 10 seeds. PS2 trips it within 100 s from any
  cycle phase. The PS3
  segment drops frames only above `ρ ≈ 1`. PS4B changes the vPLC AMPS channel only. Feeder amps equal the device sum.
  `test_cells.py` equality guard updated for the relay.
- plant PS7: at nominal, every rail voltage matches the pre-change model for the same seed. A dip to 0.85
  drops every rail together and trips the chiller relay inside 100 s from any cycle phase. The brownout
  branch still fires during a dip, which proves that the threshold uses `v_nom` and not the live supply.
- plant freeze review (section 2.9): a repeat PS4B POST keeps the healthy recording, press-1 recovers to 100 %
  throughput under the brownout line, an idle load never reads below 0 A, `tick_dt` caps a stall at 5 s,
  cooled machines start warm, a cell `tau` under 10 s answers 400, and one silent client does not block
  `/healthz`. The `test_cells.py` equality guard follows the new throughput rule.
- engine: fixtures for the `net` family root, PS2 two-hop root `compressor-1`, PS1 root `press-1`, PS0 silence.
- engine PS7: a common-mode fixture roots `incomer-1` and not a rail. A PS1 fixture with a leading machine
  load must not fire the common-mode rule. `COMMON_MODE=0` restores the old verdict on both fixtures.
- **PS2 two-hop lab:** `correlation/tests/ps2_lab.py` runs both plant families through the real GraphMemory,
  the real merge, and the real forecaster, one pass every 20 s, and it emulates the OpenPLC 78 C latch. It
  reports the window where `deploy/proof-run.sh`'s own PS2 predicate holds. Run one process per setting. It
  measures relative improvement between settings and never predicts a box pass. See section 2.2.
- **Offline replay:** `correlation/tests/replay_offline.py` drives the real plant model, samples it on the
  engine's 5 s grid, and runs the real `run_pass`. It prints one row per scenario with the expected root and
  the measured root, and it exits non-zero on any miss. It runs UNGATED (no learned baselines), so it compares
  roots under a fault and never judges silence. Run it before any box run. Today: PS1 PASS, PS2 PASS, PS7 FAIL.
- api: catalogue dispatch, reset-all, unsigned change (explained by intent, by row, by restart, and not explained),
  balance open and close, blocked proposals, refusal rows.
- scada: `/chaos` token gate and bounded behavior, historian queue fields.
- vplc: `ews.py` token gate and the S7 write against a local test PLC.
- dashboard: `npm run build` passes.

## 11. Out of scope for this change

- The data security to-do items S1 to S19 (listed in the local research notes). They stay separate work.
- The explained-load test in `_writer_edge` (section 4.5, "Known gap"). PS7 needs it. It changes the source
  path that three passing scenarios depend on, so it gets its own change and its own regression run.
- The sub-second event channel. A power quality meter reports each dip as a timestamped event with a depth
  and a duration. The aggregator polls every 5 s and the engine resamples onto a 5 s grid, so a sub-second
  dip leaves no sample. Correlation cannot see it, and precedence inside a short window is a different
  primitive. PS7 uses a held dip instead, which the 5 s grid does see.
- Any change to the Stage 2 deck. The deck reads this file.
