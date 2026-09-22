# FLEET: the virtual PLC fleet (Phase 2H) and the act loop (3D verb 1)

This document is the design and the interface contract for the virtual PLC fleet. Every
component in this list implements its part against this file. When code and this file
disagree, fix the code or change this file on purpose, in the same change.

**Goal.** An operator adds a virtual PLC from the dashboard, loads a task (a Structured Text
program) into it, and watches it come online: pod, runtime, SCADA enrollment, live tags, the
plant physics, and the engine window. A verdict on a machine that a PLC controls gets an
**Execute** button. The operator confirms, SCADA writes a real setpoint over the PLC protocol,
and the audit ledger records the action with its citation and the measured relief.

**Honesty rules (carry into every label).**
- A virtual PLC is a soft-PLC runtime with a vendor *protocol profile*. It is not vendor
  firmware. The dashboard says "virtual" on every PLC card.
- The protocols are real: Modbus TCP frames and S7comm (ISO-on-TCP) frames on the pod network.
- The scan time is measured by the runtime around each real scan. SCADA reads it over the
  protocol, the way a real PLC exposes system diagnostics.
- Machine physics stays in plant-sim. The PLC commands machines. It never computes physics.
- Every onboarding phase on screen is a real, observed timestamp. Nothing animates on a timer.

## 1. Components and ownership

| Component | Path | Change |
|---|---|---|
| vPLC runtime | `vplc/` (new) | ST interpreter, scan engine, protocol servers, control HTTP, enrollment, task library |
| Plant physics | `plant/sim/main.py` | cells, spare feeder `psu-c`, field wiring per cell, `/cells`, `/domains` |
| SCADA | `scada/` | fleet registry, protocol drivers, enrollment, write path, `/fleet`, `/domains`, `/metrics/fleet` |
| Engine service | `correlation/service.py` | domains from `DOMAIN_SOURCES` at run time |
| API | `api/` | fleet endpoints, Kubernetes REST client, act loop, narrator lines |
| Dashboard | `dashboard/app/` | Fleet section, Execute and ledger strip, floor with N rails |
| Deploy | `deploy/`, `aggregator/queries.yaml`, `Makefile` | namespace, RBAC, Secrets, base PLC, ServiceMonitor |

## 2. Names, profiles, and ports

- Namespace: `fleet`. PLC name: `plc-<slug>`, regex `^plc-[a-z0-9]([-a-z0-9]{0,16}[a-z0-9])?$`.
- Kubernetes objects per PLC: Deployment and Service `<name>`, ConfigMap `<name>-task`
  (keys `task.st`, `task.json`), Secret `<name>-token` (key `token`). Labels:
  `app: vplc`, `visr/plc: <name>`, `visr/profile: <profile>`, `visr/managed: static|ui`.
- In-cluster host: `<name>.fleet.svc.cluster.local`.

| Profile id | Label | SCADA protocol | SCADA port | Field port | Control port |
|---|---|---|---|---|---|
| `siemens-s7-1200` | Siemens S7-1200 (virtual) | S7comm, rack 0, slot 1, DB1 | 102 | 5020 | 8080 |
| `generic-iec` | IEC 61131-3 soft PLC | Modbus TCP | 502 | 5020 | 8080 |

The **field port** is the remote-I/O emulation. Only plant-sim uses it. The **SCADA port** is
what the tag server reads, in the protocol of the profile. The **control port** serves HTTP.

## 3. The memory image

All profiles share one image. Types: BOOL, INT (16-bit signed, wraps), DINT, REAL, TIME (ms).

| Area | Addresses | Count | Written by |
|---|---|---|---|
| `%IX` discrete inputs | `%IX0.0`..`%IX7.7` (index = byte*8 + bit) | 64 | field port (plant-sim) |
| `%QX` coils | `%QX0.0`..`%QX7.7` | 64 | the task |
| `%IW` input words | `%IW0`..`%IW63` | 64 | field port (plant-sim) |
| `%QW` output words | `%QW0`..`%QW63` | 64 | the task |
| `%MW` memory words | `%MW0`..`%MW63` | 64 | the task, the runtime (system words), SCADA (setpoints) |

### 3.1 Cell wiring convention (fixed, machine index k = 0..7)

| Address | Signal | Unit, scale | Direction |
|---|---|---|---|
| `%IW(4k)` | AMPS | A x10 | sim -> PLC |
| `%IW(4k+1)` | TEMP | degC x10, 0 if not cooled | sim -> PLC |
| `%IW(4k+2)` | VOLTS (the rail the machine sees) | V x10 | sim -> PLC |
| `%IW(4k+3)` | THROUGHPUT | pct x10 | sim -> PLC |
| `%IX0.k` | READY (1 = not tripped) | bool | sim -> PLC |
| `%QX0.k` | RUN | bool | PLC -> sim |
| `%QW(k)` | SPEED_PCT | 0..100 | PLC -> sim |
| `%MW(10+k)` | DERATE_PCT setpoint, task default 100 | 0..100 | SCADA -> PLC |
| `%MW8` | CELL_ENABLE, task default 1 | bool as INT | SCADA -> PLC |

### 3.2 System words (the runtime writes them after every scan)

| Address | Meaning |
|---|---|
| `%MW0` | last scan time in units of 0.01 ms, clamped to 32767 |
| `%MW1` | scan counter modulo 32768 |
| `%MW2` | state: 0 STOP, 1 RUN, 2 FAULT |
| `%MW3` | scan overrun count modulo 32768 |
| `%MW4` | task checksum: CRC32 of the ST source, low 15 bits |

A task must not declare `%MW0`..`%MW4`. The compiler rejects it.

## 4. Protocol maps

### 4.1 Field port, Modbus TCP 5020 (plant-sim is the client, any unit id)

| Modbus object | Addresses | Image | Access |
|---|---|---|---|
| holding registers | 0..63 | `%IW0..63` | sim writes |
| coils | 0..63 | `%IX` index 0..63 | sim writes |
| holding registers | 100..163 | `%QW0..63` | sim reads |
| coils | 100..163 | `%QX` index 0..63 | sim reads |

### 4.2 SCADA port, Modbus TCP 502 (`generic-iec`)

| Modbus object | Addresses | Image | Access |
|---|---|---|---|
| discrete inputs (FC02) | 0..63 | `%IX` | read |
| coils (FC01) | 0..63 | `%QX` | read |
| input registers (FC04) | 0..63 | `%IW` | read |
| holding registers (FC03) | 0..63 | `%QW` | read |
| holding registers (FC03/06/16) | 1024..1087 | `%MW0..63` | read, write |

A write outside `%MW` changes nothing and increments the `denied_writes` counter.

### 4.3 SCADA port, S7comm 102 (`siemens-s7-1200`), DB1, 400 bytes

| Bytes | Image | Encoding |
|---|---|---|
| 0..7 | `%IX` | bit b of byte a = `%IXa.b` |
| 8..15 | `%QX` | bit b of byte 8+a = `%QXa.b` |
| 16..143 | `%IW0..63` | INT big-endian, word n at byte 16 + 2n |
| 144..271 | `%QW0..63` | INT big-endian, word n at byte 144 + 2n |
| 272..399 | `%MW0..63` | INT big-endian, word n at byte 272 + 2n |

The runtime applies external writes to bytes 272..399 before the next scan. The next scan
overwrites a write to any other byte.

## 5. Tasks

### 5.1 The Structured Text subset

A task is one `PROGRAM ... END_PROGRAM` plus an optional `CONFIGURATION` block. The runtime
reads the scan interval from `TASK name(INTERVAL := T#100ms, ...)`. The default is 100 ms.

- Declarations: `VAR ... END_VAR` blocks. `name AT %addr : TYPE [:= init];` or
  `name : TYPE [:= init];` or `name : FBTYPE;`.
- Statements: `:=` assignment, `IF / ELSIF / ELSE / END_IF`, `CASE x OF 1: ... ELSE ... END_CASE`,
  `FOR i := a TO b [BY c] DO ... END_FOR`, function-block calls `t1(IN := x, PT := T#5s);`,
  `RETURN;`, empty statement `;`.
- Expressions: literals (`TRUE`, `FALSE`, `123`, `-4`, `1.5`, `T#250ms`, `T#5s`, `T#1m30s`,
  `16#FF`), variables, FB outputs (`t1.Q`, `t1.ET`, `c1.CV`), parentheses, `NOT`, unary `-`,
  `**`, `* / MOD`, `+ -`, `< > <= >=`, `= <>`, `AND` (`&`), `XOR`, `OR`.
- Function blocks: `TON`, `TOF`, `TP`, `CTU`, `CTD`, `R_TRIG`, `F_TRIG`, `SR`, `RS`.
- Functions: `MIN`, `MAX`, `LIMIT(mn, in, mx)`, `ABS`, `SQRT`, `INT_TO_REAL`, `REAL_TO_INT`,
  `BOOL_TO_INT`, `INT_TO_BOOL`, `TIME_TO_INT`.
- Comments: `(* ... *)` and `// ...`. Keywords are case-insensitive.
- A compile error reports the line and column. A failed load keeps the old task running.
- `plc/program.st` (the OpenPLC trip program) must compile and behave the same on this runtime.

### 5.2 The task manifest (`vplc/tasks/<task>.json`)

```json
{
  "task": "packaging-cell",
  "title": "Packaging cell sequencer",
  "description": "Conveyor runs, the wrapper cycles 20 s on and 10 s off, the labeler follows.",
  "st": "packaging-cell.st",
  "profile_hint": "generic-iec",
  "cell": {
    "name": "packaging",
    "rail_default": "psu-c",
    "machines": [
      {"prefix": "pack-conveyor", "kind": "conveyor", "i_base": 9.0, "cooled": false},
      {"prefix": "pack-wrapper", "kind": "wrapper", "i_base": 14.0, "cooled": true, "tau": 50.0, "heat_k": 0.5},
      {"prefix": "pack-labeler", "kind": "labeler", "i_base": 4.0, "cooled": false, "v_sensitive": true}
    ]
  },
  "io_extra": [
    {"asset": "SYS", "signal": "PACK_COUNT", "address": "%MW20", "unit": "count"}
  ]
}
```

The cell wiring convention (3.1) and the system words (3.2) generate the standard tags. The
`io_extra` list adds task-specific tags. A base cell sets `"machines_fixed": ["press-1", "press-2"]`
instead of `machines`, because its machines already exist in plant-sim.

### 5.3 The task library (first set)

| Task | Profile hint | Cell | Purpose |
|---|---|---|---|
| `stamping-line` | `siemens-s7-1200` | base cell `stamping` (press-1, press-2), rail psu-a | continuous run, `SPEED_PCT := LIMIT(0, DERATE_PCT, 100)` when READY and CELL_ENABLE |
| `packaging-cell` | `generic-iec` | new cell, 3 machines | sequencer with TON timers and a CTU pack counter |
| `packaging-cell-rush` | `generic-iec` | same wiring as `packaging-cell` | rush cadence: wrapper 10 s on, 5 s off, so the electrical load changes |

`packaging-cell` and `packaging-cell-rush` share one cell layout, so one PLC can switch
between them with **Load task**.

## 6. The vPLC runtime (`vplc/`)

- Image `skn/vplc:v0.1`, base `python:3.11-slim`, deps `pymodbus==3.6.9`, `python-snap7==3.1.2`.
  The library tasks are baked in at `/app/tasks`.
- Env: `PLC_NAME`, `PROFILE`, `TASK_DIR` (a mounted dir with `task.st` and `task.json`, takes
  priority) or `TASK_NAME` (from `/app/tasks`), `DEVICE_TOKEN`, `ENROLL_URL`
  (default `http://tag-server.plant.svc.cluster.local:9300/enroll`), `PLC_HOST`
  (default `<PLC_NAME>.fleet.svc.cluster.local`), `FIELD_PORT`, `SCADA_PORT`, `CONTROL_PORT`.
- Scan cycle: copy external `%MW` writes in, run the program, write system words, publish the
  image to the protocol buffers. The runtime measures each scan with `time.perf_counter`. A
  scan longer than the interval increments the overrun count.
- On STOP the runtime sets every `%QX` and `%QW` to 0. On FAULT (a runtime error in the task)
  the runtime stops the outputs, sets `%MW2 = 2`, and reports the error in `/state`.
- Control HTTP on 8080:
  - `GET /healthz` returns 200.
  - `GET /state` returns `{name, profile, state, task: {name, title, sha256, interval_ms},
    scan: {last_ms, avg_ms, max_ms, count, overruns}, started_at, enrollment: {ok, ts, error},
    fault: null|string, denied_writes}`.
  - `PUT /task` with body `{"st": "...", "manifest": {...}}` compiles, then swaps the task at a
    scan boundary. It needs header `X-Device-Token`. Answer 400 with `{line, col, error}` on a
    compile error. A load applies the declared initial values, so setpoints return to the task
    defaults.
  - `POST /run`, `POST /stop` need `X-Device-Token`.
- Enrollment: `POST ENROLL_URL` with header `X-Device-Token` after the first RUN, after every
  task load, and every 30 s as a heartbeat. Body:
  `{name, profile, protocol: {kind: "s7comm"|"modbus", host, port, rack, slot, db},
    task: {name, title, sha256, interval_ms}, cell: {name, rail, machines: [names]},
    io_extra: [...], runtime: {version, started_at}}`.

## 7. plant-sim cells (`plant/sim/main.py`)

- **Critical:** the 8 base devices keep their fixed order in a separate `BASE_DEVICES` list.
  The OpenPLC map (MW8..15, MW24..31) and `scada/tags.py` depend on that order. Cell machines go
  into `CELL_DEVICES`. Physics, `/metrics`, and `/state` iterate over both.
- New rail `psu-c` (spare feeder, `v_nom` 400, `r_src` 0.5), idle at start.
- One `Supply` named `incomer-1` sits above every rail (SCENARIOS.md 2.8). A rail takes its source
  voltage from the supply and keeps `v_nom` for every threshold. A new cell on any rail inherits the
  supply with no extra wiring.
- Env `BASE_CELLS`, format `cell|host:port|failopen|machine,machine;...`. Default
  `stamping|plc-stamping.fleet.svc.cluster.local:5020|failopen|press-1,press-2`.
- `POST /cells` body `{cell, plc_host, field_port, rail, fail_open, machines: [{name, kind,
  i_base, cooled, tau, heat_k, v_sensitive}]}`. `DELETE /cells/<cell>` (not for base cells).
  `GET /cells`. `GET /domains` returns
  `{"domains": {"rail:incomer-1": [...], "rail:psu-a": [...], "rail:psu-b": [...], "rail:psu-c": [...],
  "loop:cool-1": [...]}}` (members include the rail or loop name itself). `rail:incomer-1` lists the supply
  and every rail, so the engine can place a cause above the plant.
- Machine current: `i_base * base_duty(t) * speed_frac * friction`. `speed_frac` = SPEED_PCT/100
  when RUN, 0.03 when not RUN. A fail-open cell with no PLC connection uses `speed_frac` = 1.0,
  so the base plant behaves exactly as before. A new cell with no PLC stays idle. The speed
  moves toward the command at most 10 percentage points per second (a drive ramp).
  Throughput = 100 * speed_frac while RUN, else 0.
- Field wiring: one thread per cell at `TICK_S`. Write holding 0..31 and coils 0..7, read
  holding 100..107 and coils 100..107. On failure: mark the cell disconnected, retry every 5 s.
- `/state`: every device gains `cell`, `controller`, `commanded: {run, speed_pct}`. The top
  level gains `cells: {cell: {plc, rail, connected, mode, fail_open, machines}}`.
- `/metrics`: cell machines use the same `plant_*` names. New: `plant_commanded_speed_pct`,
  `plant_cell_connected{pod="<cell>"}`.

## 8. SCADA fleet (`scada/`)

- `POST /enroll`: check `X-Device-Token` = hex HMAC-SHA256(`FLEET_ENROLL_KEY`, name). Answer 401
  on a mismatch. Build the tag table from the wiring convention, the system words, and
  `io_extra`. Start or refresh one poll thread per PLC (1 s) with the driver for
  `protocol.kind`. Answer `{enrolled: true, tags: N}`.
- Drivers: `drivers/modbus.py` (pymodbus client, the map in 4.2) and `drivers/s7.py`
  (python-snap7 client, DB1 read of 400 bytes, the map in 4.3). One interface:
  `read_image() -> image`, `write_mw(index, value)`.
- Tag names: `FLEET.<PLC>.<ASSET>.<SIGNAL>`, upper case, `-` becomes `_`. Examples:
  `FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT`, `FLEET.PLC_STAMPING.SYS.SCAN_MS`. Each tag carries
  address, unit, direction (`in`, `out`, `setpoint`, `system`), `writable`, value, quality.
- Quality per tag: the same GOOD, STALE, BAD rules as the plant tags.
- `GET /fleet` returns `[{name, profile, protocol, task, cell, enrolled_at, first_good_at,
  last_good_at, poll_rtt_ms, connected, tags: [...]}]`.
- `GET /domains` returns `{"domains": {"plc:<name>": [machines..., "<name>"]}}`.
- `GET /metrics/fleet` (its own path, so it never duplicates `plant_*`): `vplc_scan_time_ms`,
  `vplc_state`, `vplc_overruns_total`, `scada_poll_rtt_ms`, `scada_plc_connected`,
  `scada_tags_good`, all labeled `namespace="fleet", pod="<name>"`.
- `POST /fleet/<name>/write` body `{tag, value}` needs header `X-Scada-Token` =
  `SCADA_WRITE_TOKEN`. Only `writable` tags. Answer 403 for other tags.
- `DELETE /fleet/<name>` needs `X-Scada-Token`. It stops the poll and removes the tags.
- The historian stores fleet tags in the same `plant_tags` table.
- An empty `FLEET_ENROLL_KEY` disables enrollment (fail closed) and `/fleet` says so.

## 9. Engine service (`correlation/service.py`)

- Env `DOMAIN_SOURCES` = comma list of URLs that return `{"domains": {...}}`. Default empty.
- Every pass fetches each source with a 3 s timeout. A failed fetch keeps the last good answer
  for that source. The service merges the static `PLANT_DOMAINS` with the fetched domains.
  A fetch never removes a static domain.
- `PLANT_ENTITIES` follows the merged domains, so enrolled machine names stay verbatim.
- Prefixes `rail` and `loop` feed the plant families. The prefix `plc` feeds only entity naming
  for now. `run_pass` and the gate do not change.
- `rail:incomer-1` arrives from plant-sim like any other rail domain. It holds one source member,
  `incomer-1`, and three victim-only members. The common-mode rule reads it (SCENARIOS.md 4.5).
- `aggregator/queries.yaml`: the plane-1 namespace regex gains `fleet`. New display-only keys:
  `plc_scan_ms: vplc_scan_time_ms`, `plc_poll_rtt: scada_poll_rtt_ms`,
  `commanded_speed: plant_commanded_speed_pct`. `ENGINE_SIGNALS` does not change.

## 10. API (`api/`)

Env: `FLEET_NS` (default `fleet`), `FLEET_ENROLL_KEY`, `SCADA_WRITE_TOKEN` (both from Secret
`visr-fleet`, optional), `VPLC_IMAGE` (default `skn/vplc:v0.1`), `TASKS_DIR` (default `/tasks`,
the ConfigMap `vplc-tasks`), `DERATE_TARGET_PCT` (default 55), `RELIEF_CHECK_S` (default 60).
Every POST, PUT, and DELETE below goes through the existing operator gate and the audit ledger.

- `GET /api/fleet/profiles` returns `[{id, label, protocol, port}]`.
- `GET /api/fleet/tasks` returns `[{name, title, description, profile_hint, cell, st}]`.
- `GET /api/fleet` returns:

```json
{"source": "k8s", "enroll": "enabled", "plcs": [{
  "name": "plc-stamping", "profile": "siemens-s7-1200", "profile_label": "Siemens S7-1200 (virtual)",
  "protocol": {"kind": "s7comm", "port": 102}, "managed": "static",
  "task": {"name": "stamping-line", "title": "Stamping line", "sha256": "ab12...", "interval_ms": 100},
  "cell": {"name": "stamping", "rail": "psu-a", "machines": ["press-1", "press-2"]},
  "state": "RUN", "fault": null,
  "scan": {"last_ms": 0.21, "avg_ms": 0.2, "max_ms": 1.1, "overruns": 0},
  "scada": {"enrolled": true, "connected": true, "rtt_ms": 3.2, "tags": 22, "good": 22},
  "phases": [
    {"phase": "requested", "ts": 1758000000.1}, {"phase": "scheduled", "ts": 1758000000.9},
    {"phase": "running", "ts": 1758000003.4}, {"phase": "enrolled", "ts": 1758000004.0},
    {"phase": "polling", "ts": 1758000005.0}, {"phase": "in_window", "ts": null}]
}]}
```

  `state` is one of `STARTING`, `RUN`, `STOP`, `FAULT`, `OFFLINE`. The phase order is fixed.
  `null` means not reached yet. Sources: `requested` = the API create time (or the Deployment
  creationTimestamp), `scheduled` = pod creationTimestamp, `running` = container startedAt,
  `enrolled` and `polling` = the tag server `enrolled_at` and `first_good_at`, `in_window` = the
  first time the API sees a key for the PLC pod or one of its machines in the aggregator window.
- `POST /api/fleet/plcs` body `{name, profile, task, rail}` answers 202. Steps: validate, choose
  free machine names `<prefix>-<n>`, `POST /cells` to plant-sim, create the Secret, ConfigMap,
  Deployment, and Service through the Kubernetes REST API with the service-account token.
- `PUT /api/fleet/plcs/<name>/task` body `{task}`: the new task must have the same cell layout.
  Update the ConfigMap, then `PUT /task` on the PLC control port.
- `POST /api/fleet/plcs/<name>/run` and `/stop`.
- `DELETE /api/fleet/plcs/<name>`: not for `managed: static`. Delete the objects, the cell, and
  the tag server registration.
- `GET /api/actions` returns `{"proposals": [...], "active": [...]}`. A proposal exists only when
  all of these are true: a root cause exists, the root has an edge with evidence, an enrolled PLC
  controls the root machine, its DERATE_PCT tag is GOOD, and the value is 100. Shape:
  `{id, verb: "derate", asset, plc, tag, from: 100, to: DERATE_TARGET_PCT, cites: {root, edge,
  evidence, confidence, signal}, expected}`. The `id` is a hash of the verb, asset, root, and
  edge, so it changes when the verdict changes.
- `POST /api/actions/execute` body `{id}`: re-derive the proposals from the current verdict.
  An unknown `id` answers 409 "the verdict changed, review again" (cite or die). Else write the
  tag through the tag server, audit verb `execute` with the citation, and after
  `RELIEF_CHECK_S` audit verb `relief` with `{asset, rail, volts_before, volts_after, amps_before,
  amps_after}` from `/api/plant`.
- `POST /api/actions/restore` body `{asset}`: write DERATE_PCT back to 100, audit `restore`.
- Narrator: the prompt and the template add the controller name, and the last executed action
  when it is newer than the verdict.

### 10.1 Integrity checks and the scenario catalogue (SCENARIOS.md section 5, LOG-068)

- `GET /api/actions` adds `blocked: [{asset, plc, reason}]` and a `signed` flag on each item of
  `active`. An asset whose controller channel has an open integrity finding gets no proposal. A
  derate with `signed: false` holds a value that no signed ledger row wrote.
- `GET /api/integrity` and the `integrity` key of `GET /api/graph` carry the open findings of the
  unsigned-setpoint check and the current-balance check. The checks append ledger rows with actor
  `visr` and verbs `unsigned` (`detected`, `cleared`) and `balance` (`mismatch`, `cleared`).
- Every write through the tag server records a write intent first, so an Execute, a Restore, or a
  PS4A reset never reads as an unsigned change.
- The refusals now write rows too: `fleet-delete` of a static PLC (`refused: base PLC`, 403), a
  duplicate `fleet-create` (`refused: exists`, 409), and a `load-task` with a different layout
  (`refused: layout`, 409). So every POST, PUT, and DELETE above is audited, refusals included.
- The scenario catalogue, its dispatch to the fault owners, and `POST /api/scenarios/reset-all` are
  in `SCENARIOS.md` section 5.1.

## 11. Dashboard (`dashboard/app/`)

- New **Fleet** section after Machines. One card per PLC: name, "virtual" badge, profile label,
  protocol and port, task title and short hash, state, scan time, SCADA RTT, tag quality
  count, cell machines, and the six onboarding phases with real times and elapsed seconds.
  Buttons (operator): Run or Stop, Load task, Remove (hidden for `managed: static`).
- **Add PLC** dialog: name, profile, task (from `/api/fleet/tasks`, with a read-only ST source
  view), rail. It posts, then the card appears in `STARTING` and fills in phase by phase.
- Recommendations: a proposal from `/api/actions` shows an **Execute** button. A confirm dialog
  shows the citation and the write (`tag: 100 -> 55`). A ledger strip lists the `execute`,
  `relief`, and `restore` audit rows, and a **Restore** button per active derate.
- `Floor.jsx`: handle any number of rails (one row per rail). Group cell machines behind their
  controller. Machines section: show the controller for each machine. Draw `incomer-1` as one row
  above the rails, with the board voltage and the plant total current. Mark the row when the supply dips.
- `Boot.jsx`: a sixth real probe, "PLC fleet", on `/api/fleet`.
- `page.jsx` MOCK: realistic dev data for `/api/fleet`, `/api/fleet/tasks`,
  `/api/fleet/profiles`, `/api/actions`. It is never used in the production export.

## 12. Deploy

- `deploy/fleet.yaml`: Namespace `fleet`. A Role in `fleet` for `apps/deployments`, `services`,
  `configmaps`, `secrets` (get, list, create, patch, delete) and `pods` (get, list). A
  RoleBinding to ServiceAccount `api` in `aiops`. The base PLC `plc-stamping`
  (`siemens-s7-1200`, task `stamping-line`, `visr/managed: static`) with its Service. A
  ServiceMonitor for the tag server path `/metrics/fleet`.
- Secret `visr-fleet` in `aiops` and `plant` (keys `enroll-key`, `scada-write-token`), and Secret
  `plc-stamping-token` in `fleet`. `PIVOT_SETUP.md` carries the paste block.
- ConfigMap `vplc-tasks` in `aiops`, from `vplc/tasks/`, created by `skctl up --components engine`.
- `Makefile` and `PIVOT_SETUP.md` step 4 build and import `skn/vplc:v0.1`.

## 13. Contract notes from the build (2026-09-15, LOG-058)

The build settled these points. They are part of the contract.

**plant-sim**
- `POST /cells` answers 201 with the cell JSON, 400 on a bad body, 409 on a name clash. `DELETE`
  answers 403 for a base cell and 404 for an unknown cell. Defaults: rail `psu-c`, `fail_open`
  false, `field_port` 5020, `tau` 45, `heat_k` 0.55. `tau` must be 10 s to 3600 s (LOG-075). A shorter
  time constant makes the explicit thermal step unstable.
- `cells.<cell>.plc` and `devices.<machine>.controller` are the first DNS label of `plc_host`.
- `cells.<cell>.mode` is `closed-loop` (connected), `fail-open` (no PLC, full speed), or `idle`
  (no PLC, idle). `/state` devices also carry `kind` and `speed_pct` (the drive after its ramp).
- `DEVICES` stays an alias of `BASE_DEVICES`. The OpenPLC register blocks never include a cell machine.

**tag server**
- System tags: `SYS.SCAN_MS` (%MW0, ms), `SYS.SCAN_COUNT`, `SYS.STATE`, `SYS.OVERRUNS`, `SYS.TASK_CRC`.
  CELL_ENABLE is `FLEET.<PLC>.<CELL>.CELL_ENABLE`. A 2-machine cell has 22 standard tags.
- `GET /fleet` is a JSON list. The header `X-Fleet-Enroll: enabled|disabled` carries the enrollment state.
- `POST /enroll` answers 403 when `FLEET_ENROLL_KEY` is empty and 401 on a bad token. A new task
  hash, protocol, or tag set restarts the poll and resets the phase timestamps. A same-hash heartbeat
  keeps them.
- `POST /fleet/<name>/write` answers 403 without `SCADA_WRITE_TOKEN`, 401 on a bad token, 404 for
  an unknown PLC, 403 for a tag that is not writable, 400 for a value out of range, 502 when the
  protocol write fails.

**vPLC**
- A `TASK_DIR` without `task.st` (an optional ConfigMap that does not exist) falls back to `TASK_NAME`.
- The enrollment cell names come from the manifest: machine `name` first, then `prefix`.

**API**
- The ConfigMap `<name>-task` holds the **resolved** manifest: every machine has its assigned name,
  `cell.name` is the PLC slug, and `cell.rail` is the chosen rail. Load task keeps those names.
- A background loop (every 10 s) records the `in_window` phase and posts any UI cell that plant-sim
  lost back to `/cells` (cells live in the sim's memory).
- Delete order: the Deployment first, so the PLC cannot re-enroll, then SCADA, then the cell.
- `POST /api/fleet/plcs` answers 400 for a bad name, profile, task, or rail, and for a base-cell
  task. 409 when the PLC exists. 502 when plant-sim or Kubernetes refuses, after a rollback.

**Images**
- Every skn manifest pulls `localhost:5000/skn/<name>:v0.1` with `imagePullPolicy: Always` from the
  box registry (`PIVOT_SETUP.md` step 4). New PLCs use `VPLC_IMAGE` with the same rule.
