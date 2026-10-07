# vplc: the virtual PLC runtime

A virtual PLC for the VISR fleet (Phase 2H). The interface contract is `FLEET.md`.

**Honesty label.** This is a virtual PLC with a protocol profile, not vendor firmware. The
protocols are real: S7comm and Modbus TCP frames on the pod network. The scan time is measured
around each real scan. Machine physics stays in plant-sim.

## What it does

- Compiles an IEC 61131-3 Structured Text task (the subset in `FLEET.md` 5.1) into Python
  closures. A compile error reports the line and the column.
- Runs the task in a scan loop at the task interval. After each scan it writes the system words
  `%MW0..%MW4`: scan time, scan count, state, overruns, and the task checksum.
- Serves the memory image on three ports:

| Port | Protocol | User | Map |
|---|---|---|---|
| 5020 | Modbus TCP | plant-sim (the field wiring) | `FLEET.md` 4.1 |
| 102 or 502 | S7comm DB1 (`siemens-s7-1200`) or Modbus TCP | the tag server | `FLEET.md` 4.3 or 4.2 |
| 8080 | HTTP | the API | `GET /healthz`, `GET /state`, `PUT /task`, `POST /run`, `POST /stop` |

- Enrolls with the tag server after the first RUN, after every task load, and every 30 s.

## Tasks

The library is in `tasks/`. Each task is a `.st` source and a `.json` manifest.

- Base cells (static PLCs in `deploy/fleet.yaml`): `stamping-line` (press-1, press-2), and since LOG-100
  `utilities` (compressor-1, chiller-1), `machining` (cnc-1), and `furnace` (furnace-1).
- New cells (Add PLC in the console): `packaging-cell` and `packaging-cell-rush`.

`ews.py` is not a PLC. It is the test attacker of Scenario 4A (`rogue-ews`). It writes one setpoint
over S7comm with no SCADA path and no ledger row (SCENARIOS.md 2.4).

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PLC_NAME` | `plc-local` | the PLC name, `plc-<slug>` |
| `PROFILE` | `generic-iec` | `siemens-s7-1200` or `generic-iec` |
| `TASK_DIR` | empty | a directory with `task.st` and `task.json`. It wins over `TASK_NAME` |
| `TASK_NAME` | empty | a library task from `tasks/` |
| `DEVICE_TOKEN` | empty | the `X-Device-Token`. Empty turns off control writes and enrollment |
| `ENROLL_URL` | `http://tag-server.plant.svc.cluster.local:9300/enroll` | the tag server enrollment URL |
| `PLC_HOST` | `<PLC_NAME>.fleet.svc.cluster.local` | the host that SCADA polls |
| `FIELD_PORT`, `SCADA_PORT`, `CONTROL_PORT` | 5020, 102 or 502, 8080 | the listen ports |
| `BIND_HOST` | `0.0.0.0` | the listen address |

## Run and test on a laptop

```bash
cd vplc
python -m pytest
PLC_NAME=plc-pack PROFILE=generic-iec TASK_NAME=packaging-cell SCADA_PORT=11502 \
  FIELD_PORT=15021 CONTROL_PORT=18081 ENROLL_URL= python -u main.py
curl -s localhost:18081/state
```

The tests bind to free high ports on 127.0.0.1. They never use port 102 or 502.
