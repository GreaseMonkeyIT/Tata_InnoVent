# OpenPLC — how it works, and how this project uses it

Companion to `REGISTER_MAP.md` (the address contract — one source of truth), `program.st`
(the logic), `entrypoint.sh` (headless bring-up), `deploy/openplc.yaml` (the pod). Phase 2F,
LOG-033/036.

## What OpenPLC is

[OpenPLC](https://autonomylogic.com) is an open-source **soft PLC**: a real IEC 61131-3 runtime
that runs on ordinary Linux instead of vendor hardware. Three pieces matter here:

1. **The runtime** — a C program executing the classic PLC **scan cycle**: read inputs → run
   the logic → write outputs, forever, on a fixed interval (ours: `task0 INTERVAL T#100ms` in
   `program.st`). Deterministic, ordered, no threads in the logic — the reason industry trusts
   the model.
2. **The compiler (MatIEC)** — translates IEC 61131-3 source (we write **Structured Text**
   directly; ladder/FBD come out the same) into C, which is compiled and hot-loaded into the
   runtime. "Upload → compile → start" in the web UI is exactly this pipeline.
3. **The web front-end** (Flask, container port 8080) — program management, compile logs,
   runtime start/stop, and a **Monitoring** tab showing live variable values (the fastest way
   to see whether field writes are actually landing).

The runtime exposes its I/O image as a **Modbus/TCP slave** (server) on :502 — the lingua
franca of OT. IEC *located variables* map onto Modbus areas: coils `%QX0.0..` at address 0+,
and the `%MW` memory words inside the **holding-register space at offset 1024** (`PLC_MW_BASE`
on the sim; if writes land nowhere, that offset is the first suspect — see REGISTER_MAP.md).
DNP3 and EtherNet/IP exist too; unused here.

## How we use it (the 2F trip loop)

The PLC is the **trip-interlock authority** of the plant — the L1 layer under everything else,
closing the loop the demo narrates:

```
plant-sim (physics)  --FC16 writes-->  %MW0..3  coolant-side temps ×10   (+ %MW20 reset word)
       ^                                        |
       |                              program.st, 100 ms scan:
       |                              latch trip when temp ≥ 780 (78.0 °C)
       |                              unlatch ONLY on reset AND cooled
       |                                        v
plant-sim (contactors) <--FC01 reads--  coils %QX0.0..0.3 (trip press-1/press-2/cnc-1/furnace-1)
```

- The **sim is the field wiring**: a pymodbus *master* thread writes sensor words each tick
  (1 s) and reads the trip coils back. A set coil = that machine's **contactor opens** in the
  physics: current → 0, throughput → 0, the machine cools, the rail voltage *recovers* —
  consequences emerge from the model, not from a script.
- **Trips are latched** (classic interlock discipline): the coil stays set until an operator
  reset (`POST /reset` pulses `%MW20`) *and* the temperature is back below trip. A hot machine
  cannot be un-tripped.
- **Fail-open, stated honestly**: PLC unreachable ⇒ the sim runs open-loop and reconnects every
  5 s, so the demo survives a PLC outage. A production *safety* PLC fails SAFE (de-energize);
  ours prioritizes demo continuity — say so if asked.
- **Why it's here at all**: PS5's ramp-to-trip gets a *real* consequence (the forecast card is
  the warning; the latched trip is what it predicted), and the Stage-3 act loop gets a real
  actuator vocabulary (the reset word is "verb 0"). The judges' line: the PLC and its Modbus
  plumbing are real industrial artifacts; only the sensor values feeding them are simulated
  physics — the same two-plane honesty as the rest of the stack.

## Deployment + ops crib

- **Image**: built from OpenPLC_v3 source (`plc/Dockerfile`) — slow the first time, cached
  after. Pod in ns `plant`; svc `openplc` :502 (Modbus, the sim's `PLC_HOST`) + `openplc-web`
  NodePort **:30081** (login `openplc`/`openplc`).
- **Headless bring-up** (`entrypoint.sh`): login → upload `program.st` → compile → start. Trap
  fixed in LOG-036: the server stores uploads under a **generated** st_files name; the
  entrypoint scrapes that name from the upload response (and cp's the file under both names)
  before compiling. Fallback: one manual upload via the web UI per pod restart.
- **Is it actually running?** In order: pod log shows `compilation finished` and the runtime
  start; web **Monitoring** tab shows `t_press1..` moving (all zeros = the MW offset or the
  master's writes are wrong); sim `/state` → `plc: {connected: true, mode: closed-loop}` and
  per-device `tripped`; Prometheus `plant_trip_active` / `plant_plc_connected`.
- **Proof beat**: fire PS5 → temps ramp → forecast card → at 78 °C the coil latches, the floor
  shows the machine dashed-red `OPEN`, amps drop to zero, the rail breathes again. Reset only
  works once it has cooled — by design.
