# INNOVENT MASTER PLAN — Stages 2 & 3
**Team SiliconKnights · the step-by-step build plan for after registration**

> **How this document works.** Stage 1 (registration, → 2026-07-05) is frozen and lives in
> `INNOVENT_PLAN.md` — nothing here touches it. This plan covers **Stage 2 (Virtual PoC)** and
> **Stage 3 (Final display)**. Phases are named **2A, 2B…** and **3A, 3B…** — deliberately NOT
> "S2/S3", because S-numbers already mean *fault scenarios* (S1 = disk storm, S5 = memory leak).
> Every phase has a plain-language goal, the steps, a **"done when"** you can verify on the box,
> and an honest list of what can go wrong. Decisions made along the way get logged in
> `INNOVENT_LOG.md`, same as always. Written 2026-07-02 (LOG-018).

---

## 0. Where we stand, in one breath

We have a working brain (the causal engine) that watches a simulated factory of Kubernetes pods
through kernel signals, finds the root cause of trouble, forecasts memory deaths, and explains
itself in plain English — reskinned as VISR and registration-ready. What it does NOT yet do:
speak the factory's own language (its numbers are computer-pressure numbers, not "coolant pump
latency"), lock its doors (no login, no audit — thin for a theme with "Secure" in the title),
watch anything that isn't a pod, or *act* on what it finds. Stage 2 fixes the first two gaps.
Stage 3 fixes the other two. §8 says what it all adds up to. That's the whole plan.

**Assets already in hand (don't rebuild these):**
- The engine + VISR dashboard, live on `mark-two` (`ABB_Accelerator_Codex`).
- **ESP32-S3 firmware** (laptop: `Documents/Google/MCU/firmware/esp32s3_visr/`) — a microcontroller
  that already speaks the engine's data dialect end-to-end ("Seam B": it serves the same `/window`
  JSON the engine eats). Move it into the repo when 3A starts.
- **PLC-SCADA-Custom** (github.com/GreaseMonkeyIT/PLC-SCADA-Custom) — a working Python stack that
  reads/writes real Allen-Bradley PLC tags over EtherNet/IP.
- The `soak/` evidence recorder, the scenario console, and the ABB documentation set.

**The dates (pinned 2026-07-02 from the public InnoVent pages):** Virtual PoC presentation =
**October 2026**; final demo day = **January 2027**. So Stage 2 has roughly three months after
registration, Stage 3 roughly three more. Judged on: **novelty · feasibility · diversity ·
real-world impact · strength of the functional prototype.** Partners this edition: **Emerson and
AWS** — an industrial-automation giant and a cloud vendor; our framing stays *edge-first,
cloud-optional* (that is also exactly how AWS's own edge products describe themselves, so it
reads as fluent, not defensive). Phases below stay dependency-ordered; slot them into the
calendar at the Stage-2 kickoff.

---

## 1. The one-sentence goal of each stage

- **Stage 2 — "Make the factory speak its own language."** The demo should show *industrial*
  symptoms (database latency, inspection throughput, message backlog) degrading and being causally
  explained — not just kernel pressure charts. Plus: the engine must never again give a confident
  wrong answer in anything we record. Plus: lock the doors — login, roles, audit (the theme says
  *Secure*; Phase 2E makes it true rather than a slide claim).
- **Stage 3 — "Cross the substrate and close the loop."** Show the same brain watching *physical
  devices* (microcontroller, PLC) next to the pods — and, with a human's confirmation, *acting*
  on its verdict. That's detect → explain → recommend → **act**, on stage, on real hardware.

**The one rule that outranks everything (carried from ABB, re-affirmed):** nothing is scripted
theater. A number on screen is either *really measured* or *clearly labeled as simulation driven
by real measurements*. The engine's core (`run_pass`) stays a pure function with green fixtures;
anything new plugs in through configuration and service wiring around it, never by editing the
brain mid-demo. This rule is what beat "scripted sensor data" at ABB and it is our identity.

---

## 2. STAGE 2 — Virtual PoC

> **PIVOT (LOG-027/028/029, 2026-07-02).** Stage 2 is rebuilt around the operator's redirect: the
> demo factory becomes a **physical-resource contention plant** — devices contending for current,
> voltage, coolant, air — because that's what a real floor contends for. The engine's soul is
> unchanged (baseline → onset → lag correlation → witness gate → ranking → forecast → narration);
> the *resources* change. **Workspace split:** `ABB_Accelerator_Codex` stays FROZEN (registration
> build + the S-series regression bench); all pivot work happens in the **Tata InnoVent clone**
> (`Tata InnoVent/ABB_Accelerator_Proto`). k3s hosts SCADA + engine layers (+ the plant emulator
> as pods, purely for ops convenience); the old factory namespaces are torn down and their
> 64Gi/5Gi disks reallocated (see `PIVOT_SETUP.md` in the clone — the ground-up runbook).
> Old S1-S5 = causal motifs reborn as the **PS-series** (LOG-028); the emulator's non-negotiable:
> **physics, not scripts** — faults are injected into a dynamical model and causality *emerges*.

### Phase 2A — Truth pass: no confident wrong answers
**Goal:** the engine either roots scenario S2 correctly, or S2 provably stays out of every demo.

**Why first:** everything in Stage 2 builds credibility, and one confidently-wrong verdict in a
recorded PoC destroys more credibility than ten right ones earn. We know from ABB (LOG-099 in the
old BUILD_LOG) that S2 (bulk archive I/O) currently produces a **confident wrong root** — the
engine blames cooling-monitor because its old, remembered coupling ("held backbone") wins when
timescaledb stalls, while the real writer (log-archiver, a batch job with no learned baseline) is
invisible.

**Steps, in order:**
1. **Backbone fix (the real bug):** a remembered edge must not *win root* unless its source pod is
   *currently* deviating. Plainly: memory may say "these two are usually coupled", but memory alone
   must never out-vote live evidence. This is a ranking/promotion rule change inside the engine —
   allowed, but only the ABB way: write the failing test first (fixtures = the engine's frozen test
   cases, and they must stay green), then change the rule, then log the decision.
2. **Let a strong newcomer be a finding:** a pod with no matured baseline (like a batch job that
   just woke up) but a very strong onset should be allowed into the findings list, clearly marked
   as "young baseline". Today it's silently skipped, which is why the true culprit can't even
   compete.
3. **Make the culprit visible physically:** switch the S2 stress from async writes (the writer
   never waits, so it never looks stressed) to synchronous writes (`fio` psync engine) so
   log-archiver visibly self-stalls while it floods the disk.
4. **Re-verify the whole demo set on the box, fresh:** S0 still silent, S1 still roots
   cooling-monitor, S5 forecast still fires, and S2 now roots log-archiver. Run the `soak/`
   recorder for a multi-hour pass as evidence.

**Done when:** a from-scratch box run shows S0/S1/S5 unchanged AND S2 rooting log-archiver — or,
if step 1-3 don't get us there in two honest attempts, we **log the decision** to keep S2 out of
demos permanently and move on. No third attempt; Stage 2 has bigger fish.

**Risks / honesty notes:** touching ranking risks the scenarios that already work — that's why
re-verification of S0/S1/S5 is *inside* the done-condition, not optional. The disk is a spinning
HDD; S2's physics were never the problem, attribution was.

---

### Phase 2B′ — The plant: a physics emulator, not a script player
**Goal:** a running simulated plant — devices drawing current from shared rails, dumping heat
into a shared coolant loop, drawing air from a shared header — whose faults produce *emergent*
causal chains the engine must genuinely infer.

**The non-negotiable:** the emulator is a **lumped dynamical model**, never per-scenario traces.
An electrical bus has source impedance, so aggregate draw genuinely sags the rail for everyone on
it; the coolant loop has a pump curve and thermal time constants, so one machine's heat spike
raises its neighbors' temperatures *with real lags*. Faults perturb the model (bearing friction
rises → that motor draws more current → rail sags → sensitive devices degrade); nothing writes a
symptom directly. Scripted traces would make us the thing we beat at ABB.

**Steps:**
1. `plant/` in the clone: ONE small Python service — physics core (rails + coolant loop, ~1s
   timestep) + ~8 device agents (duty cycles, fault hooks) + a `/metrics` page labeling every
   series `namespace="plant", pod="<asset>"` so the existing L1/L2 ingests it unchanged, +
   `POST /fault/<PS-id>` and `/reset` as the new scenario console. **Scaffold shipped 2026-07-02.**
2. **PS-series scenarios** (the S-motifs reborn, LOG-028): PS0 steady plant · PS1 rail-sag
   cascade (press-1 bearing fault → rail-A sag → press-2/cnc-1 degrade) · PS2 duty-cycle
   aggressor (compressor kicks in, no matured baseline) · PS5 chiller degradation → coolant temp
   ramps toward trip (the forecast beat).
3. Real SCADA on k3s: the historian (TimescaleDB on the 64Gi slowdisk) stores plant signals; the
   PLC-SCADA-Custom tag server joins when a real/emulated PLC (OpenPLC) is added.
4. Old factory namespaces torn down; 64Gi + 5Gi PVs wiped and reallocated (`historian-data`,
   `plant-shared`) with **claimRef baked** so the LOG-008 cross-bind can never recur.

**Done when:** `curl` shows plant metrics flowing; firing PS1 visibly sags rail A on the
dashboard/Grafana; PS0 is boring; all via the runbook (`PIVOT_SETUP.md`) from a clean box.

**Risks / honesty notes:** every plant number is labeled **"physics-simulated plant · real
inference"** — the simulation is the substrate, never the reasoning. The physics stays lumped and
simple (Ohm + first-order thermal); realism budget goes into *coupling structure*, not component
fidelity. The two-plane story stands: the edge box itself remains engine-watched (real kernel
PSI on aiops/observability/plant pods) — the real-faults anchor while the plant is simulated.

---

### Phase 2C′ — Teach the engine the plant's physics (families + domain witnesses)
**Goal:** the engine forms causal edges over plant signals — `press-1 → rail-A sag → cnc-1`,
witness = the declared rail domain — and the PS-series demos attribute correctly.

**Steps:**
1. **Signal families by config:** `ENGINE_SIGNALS` gains `bus_voltage`, `coolant_temp` (victim
   families); a `PLANT_SOURCES` map adds their aggressor signals (`bus_voltage:current_draw`,
   `coolant_temp:heat_load`) — the service-layer generalization of the hardcoded psi map.
2. **Domain witness map (the LOG-021/022 registry, now primary):** `rail:psu-a = press-1,
   press-2, cnc-1` · `loop:cool-1 = chiller-1, cnc-1, furnace-1` (ConfigMap). `_witness_for`
   intersects declared domains per family; plant entities NEVER inherit the pods' same-node
   blanket. Until this patch lands, plant families produce **findings only, no edges** — safe by
   construction, and the runbook says so.
3. Forecast generalization: `coolant_temp` → trip-threshold ETA (the PS5 card), configured like
   the OOM pair (signal + limit).
4. Baselines/maturity rules unchanged — PS0 must stay silent before PS1 is allowed on camera.
5. Fixtures first (2A discipline): plant-family cases — co-deviation without shared domain →
   NO edge (the regression test); declared-rail pair with temporal order → edge.

**Done when:** PS1 on the box yields root = press-1 with rail-domain evidence chips; PS0 soak
silent; psi-family fixtures still green (the S-series bench is the control group).

**Risks / honesty notes:** this is the pivot's engine-adjacent step — config + service-layer
witness code, `run_pass` untouched. Rollback = remove the families from env. The clock-budget
rule applies (§9-Annex): the emulator timestamps at source on one clock, so ε≈0; electrical
"instant" coupling still gets direction from **role asymmetry** (load-carrier leads), never
from sub-second timestamps.

---

### Phase 2C — Domain correlation: the causal graph speaks factory
**Goal:** the engine itself correlates a domain symptom — the verdict card can say "cooling-monitor's
write storm is why *database queries* are 8× slower", with the domain signal as evidence.

**Why after 2B:** you can't correlate numbers you aren't collecting. And 2B alone may already be
enough for the PoC video — 2C is the ambitious step, so it goes second and carries a decision gate.

**Steps:**
1. Feed `db_query_latency` into the engine as a new *victim* signal family via configuration
   (the service layer is already built for multiple signal families — psi_io/cpu/mem — with an
   env list; the engine core doesn't change).
2. Teach the service's witness map that database latency lives in the *disk-coupling* family
   (same shared-disk relation the psi_io family uses — a config/service-file change, not an
   engine change). Witness = the "physically plausible?" checkpoint; without it no edge forms.
3. Baselines: the new signal learns its normal level for ~12 samples before it may gate incidents
   — same maturity rule as everything else, so S0 stays silent. Verify S0 first, then S1.
4. Dashboard: evidence chips can now show the domain signal name; the narrator prompt gets one
   line so gemma4 may mention it ("queries slowed to 400ms").

**Done when:** S1 on the box yields the same correct root as before, **plus** a finding or edge
carrying `db_query_latency` evidence — and a full S0 soak stays silent. Fixtures green.

**Risks / honesty notes:** this is the riskiest Stage-2 item because it widens what the engine
listens to. The rollback is trivial (remove the signal from the env list), which is exactly why
it's config-driven. If it degrades S0/S1 in two attempts, we ship Stage 2 with 2B only and log it
— the PoC story survives without 2C; it just says "correlated at the kernel level, domain view on
top" instead.

---

### Phase 2D — PoC package: what the judges actually see
**Goal:** a tight recorded demo + the supporting artifacts, VISR-complete.

**Steps:**
1. **Boot section** for the dashboard (the one VISR section never built) — a short, honest system
   check screen (engine reachable, Prometheus reachable, N workloads seen) in the restrained HUD
   style. No fake progress bars.
2. Swap the trial Industry font weights for licensed files (open item from Stage 1).
3. Bake the `claimRef` storage fix into `deploy/slowdisk.yaml` (open item, prevents the one known
   restart trap), so a from-scratch bring-up is clean for rehearsals.
4. Write the PoC script and record: **PS0 calm plant → fire PS1 → rail sags, machines degrade →
   verdict + narration + blast radius (witness: rail domain) → PS5 forecast card → reset → calm.**
   The S-series (frozen Codex build) stays the banked fallback if the plant slips. Soak report
   attached as the evidence appendix.

**Done when:** the video exists, a teammate who didn't build it can re-run the demo from the
script alone, and the box can do it twice in a row without hand-holding.

---

### Phase 2E — Secure pass: make the "Secure" in the theme title true
**Goal:** nobody unauthenticated can see the dashboard, call the API, fire a scenario, or (later)
execute an action — and everything state-changing leaves an audit trail.

**Why this exists (the blatant gap):** the theme is Connected, **Secure** & Intelligent. Today we
map "Secure" to anomaly detection — which is honest but thin, and any judge who pokes the demo
finds: no login, plain HTTP, an open POST that fires faults, no device identity, no audit. In
industry this whole area is governed by **IEC 62443** (the industrial-security standard family —
think "zones with guarded doorways"), and the practices are boringly consistent: every human
authenticates and has a role, every device has an identity, every wire is encrypted, every action
is logged. None of that is research — it's plumbing, and it's cheap for us because the transport
layer already exists (**Tailscale = a WireGuard mesh**: identity-based, encrypted, exactly the
"zero-trust overlay" pattern industry uses for remote OT access. We already run on it; 2E makes
it part of the story instead of an accident).

**Steps (execution order — this lands BEFORE the 2D recording so the video shows a secured system;
phase IDs stay stable, see §4):**
1. **Human access:** the dashboard's front door (nginx, already in place) gets TLS + a login
   (simple operator accounts; two roles — *viewer* can look, *operator* can act). No new
   infrastructure — it's config on what exists.
2. **Action authorization:** scenario fire/reset and every future act verb require the operator
   role. The API rejects anonymous state changes. (Read-only endpoints stay open inside the mesh.)
3. **Audit trail:** every state-changing call appends who/what/when + the evidence it cited to a
   log the dashboard can show. This is the same ledger Phase 3D needs — built once, here.
   *Optional +20 lines, worth it:* **hash-chain the entries** (each record carries the hash of the
   previous one) so the ledger is tamper-evident — then the Secure slide honestly says
   "authenticated access AND tamper-evident audit". (Inspired by last year's Unified Logic entry,
   which built a whole top-10 project on telemetry integrity — see §10.)
4. **Device identity (groundwork for 3A):** each Seam-B device gets a per-device token checked by
   the fan-in proxy — a baby version of industry device-provisioning, enough to say honestly
   "devices are enrolled and authenticated, full X.509 certificates are the roadmap step".
5. Say it right on one slide: "aligned to IEC 62443 thinking (zones/conduits/least privilege) —
   not certified, and we say so." Overclaiming a certification would be the worst possible fallacy.

**Done when:** an incognito browser gets a login wall; an unauthenticated `curl` to fire/reset
gets a 401; a fired scenario shows up in the audit list with the operator's name; the demo video
opens with the login. All on the box.

**Risks / honesty notes:** keep it deliberately boring — auth bugs are demo killers, so prefer
nginx-level basic auth + short-lived tokens over a hand-rolled login system. The judges' question
is "did you think about security", not "did you build Keycloak".

---

### Phase 2F — Virtual PLC + SCADA: the industrial data path becomes real (3B pulled forward)
**Goal:** the plant's telemetry flows the way a real plant's does — physics → **OpenPLC** (real
IEC 61131-3 runtime, real Modbus TCP frames on the pod network) → **SCADA tag server** (ISA-88
tag names, quality bits, PLC addresses) → **historian** (the idle TimescaleDB finally ingests) +
`/metrics` → the unchanged aggregator/engine plane. Decided 2026-07-03 (LOG-033).

**Decisions locked (2026-07-03):** (1) **single-path telemetry** — once the tag server proves
stable, the aggregator scrapes it INSTEAD of plant-sim directly (sim `/metrics` stays as an
unscraped debug tap); (2) **Modbus TCP only** — OPC-UA is out of scope unless judge-facing
material demands it; (3) the PLC **earns its place by closing the loop**: latched trip interlocks
(`temp ≥ 78 °C → trip coil → sim reads coil → machine stops`) so PS5 has a consequence and the
Stage-3 act loop has a real actuator (trip reset). MCU emulation (Renode/QEMU) is rejected as
complexity theater.

**Steps:**
1. **OpenPLC pod** (`plc/`, `deploy/openplc.yaml`): pinned-source image, ST trip program
   (%MW sensor words in, %QX trip coils out, latched with condition-gated reset), Modbus :502 +
   web UI on NodePort 30081 (show judges the live ladder). Sim gains a pymodbus client thread:
   writes sensors, reads trips, forces duty=0 on trip; **runs open-loop if the PLC is absent**
   (the demo survives). Register map documented in `plc/REGISTER_MAP.md` — it is the tag source
   of truth.
2. **Tag server** (~250 lines, `pymodbus` + `psycopg2`): polls the PLC at 1 s, owns the tag DB
   (`PLANT.RAIL_A.PSU_A.VOLTS`, units, GOOD/STALE/BAD quality, PLC address per tag), writes a
   TimescaleDB hypertable (historian ingest — closes the PIVOT_SETUP §7 pending line), exposes
   `/metrics` with the same `namespace/pod` labels (aggregator repoint = one queries.yaml URL)
   and `/tags` for the UI.
3. **Tags UI:** `/api/tags` proxy; per-machine tag popover (pod-pop pattern) + a mono tag-browser
   strip; historian badge (rows/s). Caretta's eBPF sees sim⇄PLC⇄SCADA Modbus flows — the
   topology graph fills itself; say so on camera.

**Done when:** PS1 fired with the PLC in the loop shows the same causal verdict as sim-direct;
PS5 ramps → forecast card → **trip coil actually stops the machine** (visible in Machines);
tags render with quality + addresses; historian row count grows; caretta shows the Modbus edges.

**Risks / honesty notes:** OpenPLC image build + headless program upload is the unproven step —
verify on the box early; until then the sim-direct path stays. The physics remains simulated and
labeled; what becomes REAL is the runtime, the protocol, and the historian. Two-sources-of-truth
rule: physics = sim; control state = PLC; tags = the SCADA view of both.

---

### Phase 2G — The 3D plant floor (the causal graph on the floor plan)
**Goal:** a **FLOOR / GRAPH toggle** in the Causal Monitor: an isometric three.js scene (plain
three.js, already a dependency — NOT force-graph-with-fixed-nodes) — clay-model aesthetic, dark
slate floor, neutral machine blocks, meaning-colors only for status and edges. Causal edges run
as glowing conduits vert-to-vert (v1 straight arcs; v2 routed along the rail-duct/pipe polylines
with slow flow animation). Layout ships as data in the sim's `/state` (it owns plant truth).
Click a machine → its tags, trends, live values. The force graph remains for the pods plane.

**Done when:** PS1 on the floor: press-1 goes red, sag conduits reach its rail-mates, the root
card matches; PS0 floor is calm. Gated on 2C′ (no engine edges = nothing to draw).

**Operator refinement (2026-07-04, QUEUED — build only on explicit go; LOG-037):** the shipped
SVG LOD-1 floor (LOG-036) is the adequate interim; the 2G target is TRUE 3D "along the lines of"
the reference render the operator supplied: an assembly-hall isometric — room shell with low
walls/window strips, floor slab with painted lane markings, machines as clean boxy volumes
(white/blue over a teal-blue floor — conveniently near-VISR palette), stack lights, ~30–40°
camera. Carry over from the SVG floor, non-negotiable: causal activity renders ON the fixed
wiring (bus/drops/coolant runs), 90°/45° routing only, wide spacing, verdict box below the
scene, nothing floats, nothing rearranges.

---

## 3. STAGE 3 — Final display

### Phase 3A — Hardware ingest: a physical device in the Pods matrix
**Goal:** an ESP32-S3 microcontroller appears in the dashboard next to the pods, watched by the
same engine — and a real heap leak on it triggers the same OOM-forecast card the pods get.

**Why first in Stage 3:** it's the smallest step with the largest wow ("same brain, different
substrate"), and every later hardware phase depends on this plumbing.

**Steps:**
1. **Witness scoping FIRST (the 3A prerequisite — do not skip):** today the service assumes every
   entity in the window shares one node's CPU/memory domain ("single node" blanket in
   `_witness_for`). True for pods; **false the instant a device joins the merged window** — the
   witness would fabricate a coupling between a microcontroller and a pod, the exact false
   positive the gate exists to kill. Fix before the fan-in goes live: a **coupling-domain
   registry** (ConfigMap: `node:desktop`, `disk:slowdisk`, later `rail:psu-A`, `plc:…`) + a
   signal-family→medium map; witnesses come only from attested shared domains — cross-substrate
   pairs are **default-deny** until a real coupling is attested. Service-layer only; `run_pass`
   and the gate untouched. Three new fixture cases: device alone → finding, no edge; device+pod
   co-deviate, no shared domain → NO edge (the regression test); two devices on a declared rail →
   edge admitted. (This registry is also the seed 3C's wiring plan plugs into.)
   **Important (LOG-022): the registry is written by attesters, not typed by hand.** Devices
   self-describe at enrollment (gateway/AP/"installed at station N"); gateways auto-emit their
   children (the fan-in knows its feeders; the PLC tag server already knows its stations — a free
   `plc:` domain); discovery agents (LLDP walker, broker-watcher) and probational
   signature-inference are the §8 roadmap. Every edge carries witness provenance
   (declared / enrolled / discovered / inferred-probational); probational never renders as certain.
   The ConfigMap is the substrate they write into + the human override slot.
2. **Fan-in proxy** (~30-50 lines, one tiny pod): fetches the aggregator's `/window` AND each
   device's `/window`, merges them (keys don't collide — they're `namespace/device/signal`), and
   serves the union. The engine's `WINDOW_URL` points at the proxy. Engine unchanged.
3. Flash the existing firmware onto the ESP32 (Wi-Fi credentials, NTP check — time sync matters
   because the engine aligns everything on wall-clock timestamps).
4. Add a **leak scenario** to the firmware: a button/HTTP flag that makes it allocate steadily.
   The existing forecaster sees `mem` climbing toward `mem_limit` (heap toward heap size) and
   fires the incipient card — for a physical device.
5. Label honestly on the dashboard: device signals are **"RTOS-equivalent"** pressure numbers
   (the README's synthesis table is the reference) — an analogy we explain, not a claim of Linux
   PSI on a microcontroller.
6. **Naming trap (check before flashing a fleet):** the service shortens pod names by cutting the
   last two dash-parts (so `cooling-monitor-59584cbf7d-6szhd` → `cooling-monitor`). A device named
   `press-station-3` would silently become `press`. Rule: device names with at most one dash
   (`esp32a`, `esp32-a`), or add a one-line "device namespaces keep their full name" exemption in
   the service. Decide in 3A, not on the bench in 3C.

**Done when:** judge-visible sequence on real hardware: ESP32 in the Pods matrix → leak triggered
→ "OOM forecast: esp32s3, ~Ns" card → device resets → card clears.

**Risks / honesty notes:** venue Wi-Fi is the classic demo killer — plan a phone-hotspot or a
travel router as the device network, and rehearse on it. A single device gives findings and
forecasts but **no causal edges** (edges need a coupled pair) — do not promise chains in 3A.

---

### Phase 3B — PLC ingest: real industrial tags in the same plane
**Goal:** real controller data (scan time, fault bits, comm errors) flowing into the same
dashboard, via our own EtherNet/IP stack.

**Conditional phase — first action is a hardware-access check:** do we have a MicroLogix (college
lab / internship contact) for the finals window? If **no**: run the tag stack against an SLC
emulator and say so honestly, or drop 3B and let 3A+3C carry the hardware story. Decide, log it,
move on — no sunk-cost building.

**Steps (if hardware confirmed):**
1. Containerize `tag_server.py` from PLC-SCADA-Custom → one pod in a `factory-hw` namespace.
2. Add a `/metrics` page to it re-exposing chosen tags as gauges (~40 lines): scan time, comm
   error counter, a couple of process values. This makes it a standard Prometheus exporter —
   Seam A — and, run as a pod, it *is* the "SCADA-as-pod" idea from the team chat.
3. Add the query lines to the aggregator's `queries.yaml`; the PLC station appears in the Pods
   matrix (mind the naming trap from 3A).
4. Map one honest fault: e.g. pull the PLC's input wire / overload its scan → scan time and fault
   tag move on the dashboard.

**Done when:** a real tag from a real (or clearly-labeled emulated) controller moves a VISR tile
live.

**Risks / honesty notes:** prefer runtime-measured tags (scan time, error counters) over
application heartbeats — same spirit as the ABB rule about never trusting an app's self-report.

---

### Phase 3C — The wiring witness: first causal chain between physical things
**Goal:** the engine draws a causal edge between two *devices* — "this one is the source, that one
is the victim" — using a declared wiring plan as the physical-plausibility witness.

**Why:** correlation alone never forms an edge in our engine (that's the false-positive gate that
won us class at ABB). Pods prove coupling via shared disks and network taps; devices prove it via
**the wiring plan** — exactly the "new type of input" Kishan named in the team chat.

**Steps:**
1. A small **wiring config** (a Kubernetes ConfigMap — a plain settings file the service reads):
   declarations like `psu-rail-A: esp32-a, esp32-b` — the service layer turns each shared-medium
   group into coupling facts for the witness, the same way the storage-workload list already
   works. Engine core untouched.
   *Dependency note:* the rail-voltage reading is a **new signal family**, and adding one uses the
   exact mechanism Phase 2C builds (signal list + witness map in the service). If 2C was gated
   out, that plumbing gets built here instead — budget for it.
2. Build the coupled pair. Recommended: **two ESP32s on one power rail**, each reading its own
   rail voltage via ADC (a real, measured coupling signal). Fault trigger: a safe dummy load
   (MOSFET + resistor) on device A sags the shared rail; both devices' readings deviate; A leads.
   Pick a different shared medium if the bench says so — the requirement is only: *really shared,
   really measurable on both, safely triggerable.*
3. Rehearse until the physics is repeatable; tune nothing in the engine — if the edge doesn't
   form, the fix is better signals or a better coupling, not a looser gate. (Loosening the gate
   to pass a demo is the exact fallacy this plan bans.)

**Done when:** firing the bench fault produces, on the live dashboard: device-A → device-B edge
with wiring evidence, A ranked root, narration in plain words.

**Risks / honesty notes:** this is the phase most likely to eat bench time — schedule real
rehearsal evenings. If the two-device chain won't stabilize before finals, 3A's forecast demo +
3B's tags still make a strong hardware story; the chain is the stretch goal, and we say so to
ourselves now rather than discover it on stage.

---

### Phase 3D — The act loop: explain → recommend → **act** (with a human)
**Goal:** the Recommendations panel grows an **Execute** button: the operator confirms, the system
performs ONE bounded action, the pressure visibly drops, and the action is recorded in a ledger.

**The safety frame (non-negotiable, from BOOK §6.5 / PLAN §6):** a closed vocabulary of verbs (the
system cannot invent actions) · every action **cites** the causal evidence it acts on ("cite-or-die")
· human-confirm on everything · advisory layer only, never the safety-critical control loop.
This is the deliberate, documented evolution of the ABB read-only boundary — we're allowed to act
*because* we act on a known cause, with a person in the loop.

**Steps:**
1. **Verb 1 — `throttle` (software, fully real):** API endpoint `POST /api/actions/throttle`
   (confirm-gated) that lowers the *source* workload's CPU quota via the standard Kubernetes
   resource-limit mechanism. Honesty check built in: Kubernetes has **no standard disk-I/O
   ceiling**, so for an I/O storm the CPU quota relieves the victim only *indirectly* (a starved
   writer issues fewer writes) — **bench-verify the relief is visible before demo day.** If it
   is too subtle, the honest fallback verb is `pause` (scale the aggressor to zero replicas) —
   cruder, still bounded, still reversible, and unmistakable on the graph. Demo: S1 fire →
   verdict → Execute → victim's stalls visibly recede → reset restores.
2. **Action ledger:** every executed action appends a record — what verdict, what evidence cited,
   what happened to the pressure in the next 60s. Shown as a small "actions taken" list. (This is
   the seed of the rehearsal-ledger idea from the ABB roadmap — start it simple.)
3. **Verb 2 — `derate` (hardware, testbed only):** for the ESP32 rig: a confirm-gated command that
   tells the leaking/loading device to slow its work task (firmware already has task knobs). If 3B
   hardware exists and the bench allows: the PLC write path (our stack can write tags) may demote
   a setpoint instead — testbed only, human-confirm, never on anything that moves metal.
4. Dashboard: Execute buttons live on the existing Recommendations cards (they already show verb +
   citation); add the confirm dialog + ledger strip. Narrator gets one line so it can say what was
   done.

**Done when:** on stage: fault fires → engine explains → operator clicks Execute → confirm → the
graph/pressure visibly improves → ledger shows the entry with its citation. Twice in a row.

**Risks / honesty notes:** the demo verb acts on the *source* (throttle the aggressor), which is
visually obvious and safe. We do NOT autonomize anything — if a judge asks "can it act alone?",
the answer is the designed one: "it may, one day, after the ledger proves it right enough times —
by design it starts supervised."

---

### Phase 3E — Finals package: narration, staging, fallbacks
**Goal:** the physical table and the story, rehearsed.

**Steps:**
1. **Narrator tune (prompt-level only):** gemma4 speaks plant language — station names, "coolant
   pump", domain symptoms, actions taken. No model retraining; it's a prompt/template pass.
2. **The table:** desktop (cluster) + big screen (VISR) + ESP32 rig (+ PLC if 3B lives) + one
   printed one-pager of the architecture. Power and network survive without venue Wi-Fi.
3. **The script:** calm → software fault (S1, domain symptoms) → verdict → Execute → relief →
   hardware leak → forecast card → (stretch: device chain) → close on the roadmap slide (hardware
   fault taxonomy, fleet scale — talked, not faked).
4. **Fallbacks:** every live beat has a recorded twin from rehearsal; the demo laptop carries the
   PoC video, the soak report, and screenshots. If the bench dies, the story degrades gracefully
   instead of collapsing.

**Done when:** full dry run in front of the team, timed, including one deliberate failure drill
(kill the Wi-Fi mid-demo and recover via fallback) — before we travel.

---

## 4. Sequencing at a glance

| Order | Phase | Rough size | Hard dependency | Can drop? |
|---|---|---|---|---|
| 1 | 2A truth pass | 1-2 weekends | — | S2-fix yes (gate), backbone fix no |
| 2 | 2B′ plant emulator + teardown/reallocation | 1-2 weekends | PIVOT_SETUP runbook | core of Stage 2 — no |
| 3 | 2C′ plant families + domain witnesses + PS-series console | 1 weekend + soak | 2B′ | edges gated; findings-only mode is the fallback |
| 4 | 2F virtual PLC + SCADA (3B pulled forward, virtual) | 1-2 weekends | 2B′; OpenPLC image proves on the box | yes → falls back to sim-direct scrape |
| 5 | 2G 3D plant floor (FLOOR/GRAPH toggle) | 2-3 days | 2C′ (edges exist) + layout data | yes (display depth, not the spine) |
| 6 | 2E secure pass | 1 weekend | — | no (theme-title word) |
| 7 | 2D PoC package | 1 weekend | 2A/2B/2E | no |
| 8 | 3A ESP32 ingest | 1 weekend | fan-in proxy, 2E device tokens | no (Stage-3 core) |
| 9 | 3B PLC ingest (hardware variant; virtual form = 2F) | 1 weekend | 3A plumbing, **hardware access** | yes (conditional) |
| 10 | 3C wiring chain | 2+ weekends bench | 3A (+ 2C's signal-family plumbing) | stretch goal |
| 11 | 3D act loop | 1-2 weekends | 2E auth/ledger (verb 1 is software-only); 2F trips = verb-0 | verb 1 no, verb 2 yes |
| 12 | 3E finals package | 1 weekend + rehearsals | all above | no |

Phase IDs are stable even where execution order differs (2E runs before 2D; the letters just
record the order the phases were planned, not the order they run).

2A/2B can start in parallel (different files). 3D-verb-1 is independent of all hardware phases —
if Stage-3 time runs short, **act-loop verb 1 outranks the device chain** (it completes the
detect→explain→recommend→act story, which is the pitch; the chain is depth, not the spine).

## 5. Standing rules (the fallacy guards)

1. **Measured or labeled.** Every screen number is really measured, or visibly marked as
   simulation derived from real measurements. No timed scripts that "make it look bad".
2. **The brain stays pure.** `run_pass` remains a pure function; fixtures stay green; new inputs
   arrive via config and service wiring. Any exception is a logged decision with a test first.
3. **Gates never loosen for demos.** If a demo needs a looser causal gate, the demo is wrong.
4. **Two honest attempts, then decide.** Risky items (S2 fix, 2C, 3C) get two real tries; then we
   log the outcome and re-scope. No sunk-cost spirals — Kishan's one-USP discipline applies to
   engineering time too.
5. **Every phase ends box-verified** — on the real desktop cluster, from the runbook, not on a
   laptop preview — and gets a LOG entry.
6. **Claims match the build.** Roadmap items (fault taxonomy, fleet scale, autonomy) are *spoken*
   as roadmap, never demoed as if built. It worked at ABB; it's cheaper than faking and always
   will be.

## 6. Known unknowns (pin these ASAP)

- ~~Stage 2 / Stage 3 official dates~~ — **PINNED:** Virtual PoC **October 2026**, final demo
  **January 2027** (public InnoVent pages, 2026-07-02).
- **The full official §3.2.2.5 problem-statement text** — the gap analysis in §8 is built from the
  theme title + industry practice; when the team re-reads the rulebook bullets, re-check §8's gap
  table against them line by line and log anything it missed.
- **PLC hardware access** for the finals window (drives the 3B go/no-go).
- **Team hands:** who solders/benches 3C, who records 2D, who owns the ledger UI — assign at the
  Stage-2 kickoff.
- **Venue constraints** (power, network, table space) — ask the organizers before building the
  finals table plan.

## 7. Explicitly not in scope (so nobody re-litigates it)

- **Building** the VISR *platform* (OS-skin, Advisor/Runtime, board-portability layer) — dropped
  at LOG-003, stays dropped as a build. What returns is the **story**: §8 defines "VISR OS" as the
  roadmap architecture we demonstrate *proof points* of, stage by stage — never as software we
  claim exists. The difference between §8 and the LOG-002 over-reach is exactly that discipline.
- Autonomous (no-human) actions — roadmap talk only, by design.
- A full hardware-fault taxonomy / general RTOS log reasoning — one slide, zero code.
- Replacing anyone's SCADA/HMI — we are the advisory layer beside it, in ConfidenceOS's own
  winning framing: read-only in, one supervised verb out.

---

## 8. The north star — "VISR OS" (what all of this adds up to)

**The claim we are building toward, in one sentence:** *any industrial asset — a container, a
microcontroller, a PLC, eventually a vehicle — can be enrolled into one secure plane where an
authorized engineer anywhere in the country can see its live health, get causal verdicts, and run
approved tests remotely, without caring what protocol or tool the asset speaks underneath.*

Two things must be shown for that sentence to be believed, and they map to what the operator set
as the actual goals:
1. **Hardware-agnosticism — shown, not said.** The ladder of §9: the same dashboard and the same
   brain watching pods (today), a microcontroller (3A), a PLC (3B), with each new rung costing
   only a thin adapter. Every rung we demo makes the "and eventually a vehicle" line credible.
2. **The framework story — said precisely, in industry's own vocabulary.** This is where the
   research matters, because industry has already standardized the *access* layer we describe:

**How industry actually builds this today (plain-language survey):**
- **In factories,** devices don't talk to dashboards directly — thin **protocol adapters**
  translate each dialect (EtherNet/IP, Modbus, Profinet…) into a common plane; **OPC UA** and
  **MQTT (Sparkplug)** are the two lingua francas. *Our Seam A/B architecture is this exact
  pattern* — our EtherNet/IP tag server is one adapter; OPC UA/Modbus adapters are additional
  drivers, not redesigns.
- **For security,** the playbook is **IEC 62443** thinking: segment into zones, authenticate every
  human and every device, encrypt every wire, audit every action, sign every update. Remote access
  runs over identity-based encrypted meshes (WireGuard-class). *We already run on Tailscale
  (WireGuard); 2E adds the login/roles/tokens/audit; full X.509 device certs = roadmap.*
- **For fleets,** the pattern (AWS Greengrass / Azure IoT Edge — note our partner list) is a
  **local edge runtime per site** that works autonomously, plus a central pane that federates
  many sites. *Our single-node K3s + engine IS that local runtime; federation = many VISR nodes
  + one hub view over the mesh. Roadmap, honestly labeled.*
- **For vehicles,** the software-defined-vehicle world has settled on: **VSS** (a standard naming
  tree for vehicle signals, from COVESA), **KUKSA** (the open-source broker serving those signals),
  and **SOVD / ISO 17978-3** (the new ASAM diagnostics standard) — which is **HTTP + JSON + OAuth2,
  explicitly designed for local, remote, and cloud diagnostic access**, bridging legacy UDS
  underneath. **The punchline we must not waste:** the industry's newest vehicle-diagnostics
  standard chose *exactly the architecture our stack already has* — REST/JSON APIs with token
  auth over IP. "An engineer connects to a vehicle, reads its metrics, and runs authorized tests
  remotely" is not our fantasy; it is what SOVD standardizes. What SOVD does NOT provide — and
  where VISR is novel — is the **brain on top**: causal attribution, forecasting, dependency
  mapping, and bounded supervised action. Access standards move data; nobody standardized the
  reasoning. That's our seat at the table.

**The gap table (theme title vs. us, honestly):**

| Theme word | Industry practice | What we have today | Blatant gap | Plan hook |
|---|---|---|---|---|
| Connected | OPC UA / MQTT-Sparkplug adapters into one plane; fleet federation | Prometheus + our own `/window` dialect; custom EtherNet/IP stack; single site | no *standard* OT protocol spoken; no multi-site | 3B (adapter pattern proven); OPC UA driver + federation = roadmap slide |
| Secure | IEC 62443 zones; per-device identity; mTLS; RBAC; audit; signed OTA | encrypted mesh (Tailscale) by accident of setup; otherwise open HTTP, no login, no audit | **the big one** | **2E** (login/roles/tokens/audit, said in 62443 vocabulary); X.509 + signed OTA = roadmap |
| Intelligent | anomaly detection, digital twins, predictive maintenance (commodity) | causal root-cause + forecast + narration + planned act loop — beyond commodity | none — this is our strength | 2B/2C deepen it with domain signals |
| Edge AI | local inference at the asset, cloud-optional | everything on-box incl. the LLM; air-gap capable | none | say it with the partner-aware "edge-first, cloud-optional" line |
| (vehicle vertical) | VSS names + KUKSA broker + SOVD remote diagnostics | API already SOVD-shaped (HTTP/JSON); ad-hoc signal names | no VSS naming, no real vehicle feed | §9 D4: optional OBD-II proof point; VSS-mapped naming = roadmap |

**What we show vs. what we say, per stage (the anti-fallacy line):**
- PoC (Oct 2026): SHOW pods + domain symptoms + secured access (2E login on camera). SAY the
  ladder and the framework, one slide, standards named correctly.
- Finals (Jan 2027): SHOW the MCU rung (3A), the act loop (3D), and — because the cluster is
  *already remote* from the operating laptop — the honest remote beat: **an authorized engineer
  in one place operating an asset in another, over an encrypted mesh, with the action landing in
  the audit log.** That is the VISR OS sentence, physically demonstrated at small scale.
  SAY: fleet federation, vehicle enrollment via VSS/SOVD (+ WP.29 / UNECE R155-R156, the
  cybersecurity & software-update regulations, on the vehicle roadmap slide), X.509 provisioning
  — as roadmap.
- Never: demo the word "vehicle" without a real vehicle feed; claim OS/platform software that
  doesn't exist; claim 62443 *compliance* (we claim alignment of thinking, and say the difference).

---

## 9. The device ladder (§8's rung-by-rung proof; only 3A/3B are scheduled work)

- **D0 — Kubernetes pods.** Done since ABB. The reference rung: full signals, full causality.
- **D1 — Microcontroller (ESP32-S3).** Phase 3A. RTOS-equivalent signals, honestly labeled.
- **D2 — Raspberry Pi (PARKED — planned, deliberately not scheduled).** The interesting rung,
  because a Pi runs real Linux: **it has a real kernel with real PSI** — no synthesis needed,
  full-fidelity signals. Three enrollment modes, in ascending ambition:
  - **(a) Seam-B Linux device:** a ~50-line exporter reads `/proc/pressure/*` + cgroup stats and
    serves `/window`. Cheapest, and the signals are *better* than the ESP32's.
  - **(b) Second K3s node:** the Pi joins the cluster as a real node (`k3s agent`); its pods get
    watched automatically. Two known costs, flagged now: arm64 images must be built, and the
    engine's same-node witness logic assumes ONE node today (the service must scope CPU/mem
    coupling by node label — a service-layer change, noted in the code comments already).
  - **(c) Standalone mini-VISR node:** the Pi runs the whole small stack — this is the federation
    unit of §8, one "site" in miniature. Roadmap tier.
- **D3 — PLC (MicroLogix).** Phase 3B, conditional on hardware access.
- **D4 — Vehicle (roadmap; optional proof point).** IF a real car + an OBD-II reader are actually
  available near finals: a Pi/ESP32 gateway reading a handful of real signals (RPM, coolant temp)
  into the Pods matrix would be the single most Tata-shaped demo beat we could stage — but it is
  a stretch-of-a-stretch: attempt only if D1 is rock-solid and 3C/3D are done rehearsing. Absent
  hardware, D4 is the VSS/SOVD roadmap slide in §8, and that is enough.

### §9-Annex — witness classes for physical systems (the 3A/3C reference; full reasoning in LOG-021)

A witness = a shared medium causality can flow through. Pods auto-discover theirs; physical
systems **declare** theirs from documents factories already keep. The classes:

| Medium | Couples via | Witness source | Maps to |
|---|---|---|---|
| Electrical (rail/PSU/phase) | sag, brownout, harmonics | single-line diagram, panel schedule, wiring ConfigMap | `shared_relation` |
| Communication (bus/switch/broker/gateway) | contention, babbling node, gateway saturation | topology docs; **auto:** LLDP, broker client lists | `shared_relation`; master→slave & gateway→children **directed** |
| Control (same PLC, interlocks) | scan-time coupling; A gates B | **PLC tag map (we own it — free witness)**; cause-&-effect matrix (a declared causal graph) | `shared_relation`; interlocks **directed** |
| Mechanical (shaft/belt/material flow) | vibration, jams downstream | drawings, line layout | flow **directed** |
| Process/fluid (compressor/coolant/header) | pressure/temperature starvation | **P&ID** | supply→consumers **directed** |
| Thermal (same cabinet/zone) | heat | cabinet layout | `same_node`-style, **source-attributed** |
| Spatial (same room) | dust, humidity, flooding | asset registry location | co-location |
| Operational (same tech visit / OTA batch) | common intervention | CMMS / OTA logs | hidden common cause |

**Clock budget rule (LOG-024, from the Lamport discussion):** timestamp-ordering across sources is
valid only while clock error ≪ the medium's propagation lag (Lamport's physical-clock condition).
Per source, the real error term is **max(clock-sync error, sampling/poll period)** — for PLC tags
that's the gateway's 2s poll, not NTP. Budgets: pods ε≈0 · ESP32 ε≈tens-of-ms · PLC ε≈poll period
(engineer the cadence, 3B) · **electrical media: unmeetable — direction from role asymmetry (who
carries the load leads), never from time (3C).** Slow media (thermal/fluid, minutes) are easy.

Doctrine: **something other than the correlated signals must attest the coupling** (pods were
never hand-declared either — Caretta/K8s attest them). Attestation tiers (LOG-022): T1 enrollment
self-description + gateways auto-emitting children (tag server → `plc:` domains for free) · T2
discovery agents (LLDP walker, broker-watcher) · T3 one-time document ingestion per plant · T4
probational engine inference (fingerprint channels distinct from fault signals, quiet-baseline
learned, low-prior + confirm-or-decay — the existing memory machinery pointed at domains). Every
edge carries witness provenance; probational renders as *suspected*, never certain. Cross-substrate
pairs stay default-deny until attested. Deck line: "the plant maps itself; the engineer confirms,
not types."

---

## 10. The field — what last year's top 10 teaches (2025-26 cohort, analyzed 2026-07-02)

Nine of ten finalists were automotive/EV (adaptive dashboard · EV fault detection ×2 · V2X fleet ·
blind-spot camera · ADAS retrofit · battery twin ×2 · wireless charging · telemetry integrity ·
range extender). Caveat: last year's brief was smart-mobility-flavored, so the skew partly
reflects the theme — but the judging panel's automotive DNA is structural (it's Tata
Technologies). Our §8 vehicle roadmap is the bridge; the industrial lane is likely thinner.

**The closest analogue — "T-Factor" (top 10):** on-vehicle EV fault detection, claimed "38 ms
detection, 92% root-cause accuracy, 88% self-healing, fully offline", ASIL-B/WP.29 vocabulary,
digital twin + hardware prototype + Zephyr integration on demo day. Lessons taken:
1. **The claims culture is quantified and bold.** Answer with *measured, reproducible* numbers
   (detection latency, forecast lead time, evidence count per verdict) + soak reports as receipts
   + fire-it-yourself live faults. "Verifiable" is our counter to "big".
2. **"Self-heals 88%" is the opaque version of our act loop.** Pitch 3D as the defensible
   version: one bounded verb, evidence-cited, human-confirmed, audit-logged — what an OT
   environment would actually accept.
3. **Compliance vocabulary lands.** We speak IEC 62443 + SOVD (§8); the vehicle slide adds WP.29.

**Cheap steals adopted:** "digital twin" is the cohort's lingua franca (3 of 10 used it) — we may
honestly say **"a causal digital twin — learned from live signals, not drawn in CAD"** (the graph
+ baselines + case memory qualifies); tamper-evident audit via hash-chaining (2E step 3, from
Unified Logic's whole-entry-on-integrity); Pi-as-edge-gateway is a normalized pattern (×2) so §9
D2 will read familiar; physical rigs made finals tables (×3) confirming 3A/3C/3E; problem slides
were socio-economically quantified (₹-crore losses, death counts, "47% of EV breakdowns are
electrical/software" — verify before reusing any borrowed stat).

**The gap nobody filled:** every entry is single-asset intelligence; SwarmSync shares fleet data
but doesn't reason over it. **No one does cross-source causal attribution** — the exact pain our
engineer contacts confirmed and the exact thing the engine does. The USP sentence survives the
field: *everyone detects anomalies; we explain them, across sources, with evidence.*
