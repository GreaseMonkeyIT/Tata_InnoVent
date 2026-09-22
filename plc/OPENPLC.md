# OpenPLC — how it works, and how this project uses it

Companion to `REGISTER_MAP.md` (the address contract — one source of truth), `program.st`
(the logic), `entrypoint.sh` (headless bring-up), `rest-off.sh` (REST API off),
`deploy/openplc.yaml` (the pod), `deploy/openplc-rollout.sh` (image change on the box). Phase 2F,
LOG-033/036/077.

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
  NodePort **:30081** (LAN and Tailscale).
- **Web login (LOG-077)**: user `openplc` with the password from Secret `plant/openplc-auth`. The
  vendor default login does not work. Read the password with
  `kubectl -n plant get secret openplc-auth -o jsonpath='{.data.password}' | base64 -d; echo`.
  `deploy/openplc-auth.sh` makes the Secret, and `deploy/golive.sh` step 0 runs that script.
  The web UI is plain HTTP. A login from the LAN sends the password unencrypted. Tailscale
  encrypts its own traffic. The operator kept the NodePort open for the demo.
- **REST API off (LOG-077)**: upstream OpenPLC also starts a REST API on port 8443. After each pod
  start, its first caller can make a user without a login, and that user can stop the PLC or
  replace the program. `rest-off.sh` removes the line that starts it, at image build time. The web
  UI is then the only way to change the program.
- **Headless bring-up** (`entrypoint.sh`): set the password → login → upload `program.st` →
  compile → start → check. Before the web server starts, the entrypoint writes the Secret value
  into the user table in `webserver/openplc.db`. Each pod start gets a new container file system,
  so it does this at every start. Without a usable Secret, it sets a random password for that pod
  only: the trip loop runs, but nobody can log in. The password goes through stdin only, never into
  a command line or the log.
- **Upload trap (fixed in LOG-036)**: the server stores an upload under a **generated** st_files
  name. The entrypoint reads that name from the upload response before it compiles. It also copies
  the file under both names. Fallback: upload `program.st` by hand in the web UI after each pod
  restart.
- **Is it actually running?** Check in this order:
  1. The pod log shows `[entrypoint] PASS` lines for the password, for `plant_trips` in Running,
     and for a closed port 8443.
  2. `bash deploy/openplc-rollout.sh probe` prints `200 302 plant_trips 000`.
  3. The web **Monitoring** tab shows `t_press1..` moving. All zeros means that the MW offset or
     the writes of the sim are wrong.
  4. The sim `/state` shows `plc: {connected: true, mode: closed-loop}` and `tripped` per device.
  5. Prometheus shows `plant_trip_active` and `plant_plc_connected`.
- **Change the image on the box**: `deploy/openplc-rollout.sh`. Its `test` mode builds on top of
  the running image, so the OpenPLC runtime stays the same, and it runs the image in a throwaway
  container. Its `deploy` mode rolls the image onto the pod and rolls back when the trip loop does
  not close. A PLC restart clears every latched trip, so the script refuses to run while a fault is
  active or a machine has a latched trip.
- **Proof beat**: fire PS5 → temps ramp → forecast card → at 78 °C the coil latches, the floor
  shows the machine dashed-red `OPEN`, amps drop to zero, the rail breathes again. Reset only
  works once it has cooled — by design.
