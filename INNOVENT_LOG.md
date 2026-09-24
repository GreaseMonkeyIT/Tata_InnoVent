# INNOVENT_LOG — evolving decision log

Append-only. **Never delete an entry**; if a decision is reversed, add a new entry that supersedes it (and reference the old one). One entry per significant decision. Companion to `INNOVENT_PLAN.md` (current state).

---

**LOG-001 · 2026-06 · Kickoff.** Tata Technologies InnoVent 2026, team **SiliconKnights**. Theme: AI at the Edge. The team is an **ABB Accelerator 2026 finalist** with a working causal-AIOps engine for Kubernetes; intent is to leverage it. Domain strength: heavy machinery + industrial automation (Tata Hitachi internship + ABB project).

**LOG-002 · explored (later dropped) — the "VISR platform" idea.** "Kubernetes for the physical edge": treat every controller/sensor/PLC/HMI/ECU as a uniform node; an OS-skin/overlay that self-detects hardware; a 3-layer stack — **Advisor** (cross-target compatibility advice), **Runtime** (uplink/rumqtt + MicroPython/WASM), **VISR engine** (carried causal engine); POVs (car/factory/edge); a no-RTC time/log substrate; external dev-board integration (Pi/ESP32/Milk-V/Arduino). Researched open-source SDV stacks (KUKSA/VSS, AGL, bytebeam uplink/rumqtt) and built a scaffold (advisor/bridge/firmware/visrctl/edge_aggregator) from the ABB `mark-one` core; validated the Advisor CLI and the MQTT→engine ingest.

**LOG-003 · REDIRECT — dropped the VISR platform (supersedes LOG-002).** Grassroots review against the InnoVent themes + the Karpathy `RULES/`. Findings: the platform became **infrastructure/middleware that fits no "Edge AI for X" theme**, centered the *unproven* abstraction layer over the *proven* engine, and over-complicated. **Decision: perfect the existing ABB engine as the entry.** Wiped the VISR scaffold from the project folder; re-cloned plain ABB `mark-one` → `ABB_Accelerator_Proto/`. The VISR **name + restrained HUD skin** are kept (as the dashboard reskin); the platform and board-integration are parked.

**LOG-004 · Target category locked.** **§3.2.2.5 — Edge AI for Connected, Secure & Intelligent Industrial Systems** (Industrial Heavy Machinery vertical). The engine maps cleanly: **Connected** = inter-node dependency mapping · **Secure** = deviation-from-baseline anomaly detection · **Intelligent** = causal root-cause + forecasting + LLM narration. Best fit because it uses the engine's *whole* capability (vs predictive-maintenance, which uses only the forecast slice) and sits in the team's domain.

**LOG-005 · July-5 scope = the current build, as-is.** No hi-fi ambition for registration. Reuse the working engine + dashboard; reframe to industrial + reskin. (Carried over: the engine is a predictive-maintenance / connected-intelligent-secure health brain.)

**LOG-006 · Repair agents = Stage-3 / final display (not July 5).** The "act" step of explain→recommend→**act** (BOOK.md §6.5): closed action vocabulary, a small orchestrator mapping `verdict→one bounded action`, cite-or-die, safe-because-acts-on-known-cause, human-confirm. Machine actions = derate/throttle/cooldown/isolate/work-order. Advisory, not in the safety-critical loop.

**LOG-007 · Data-publishing — planned, alongside scenario fixes (not built).** Other factory pods to emit **simulated, namespace-relevant** signals via tiny Prometheus exporters, added to `aggregator/queries.yaml`. Key design: the sim values **react to scenarios** so the causal graph correlates **domain symptoms**, not just PSI.

**LOG-008 · Engine restored on the Linux desktop (full restart "hard drive and all").** Sequence/fixes: `systemctl start k3s` (was `k3s-killall`'d; observability/aiops/caretta returned from the datastore); the static **slowdisk PVs were missing → recreated**; a **cross-bind race** (`shared-logs-pvc` grabbed the 64Gi `tsdb` PV) → fixed by **`claimRef` pinning** each PV to its PVC; `/mnt/slowdisk/{tsdb,shared-logs}` **did not exist → created** (the FailedMount cause). `alloy` CrashLoop = known, ignorable (deferred log collector, unused by the engine). Run `skctl` from `~/ABB_Accelerator_Codex` — the synced clone lost exec bits (Windows→Syncthing); `chmod +x` to fix. **Action:** bake `claimRef` into `deploy/slowdisk.yaml`.

**LOG-009 · Reskin direction = restrained Halo Infinite ("VISR").** Principle: **clarity is the product, Halo is the accent (~90/10)** — professional software, cues for flavor not cosplay. Studied 3 refs (Academy board, TACMAP, boot screen — shot 3 is a *boot* screen, not the ammo counter). **Kept:** cyan single accent · thin corner brackets (primary element only) · mono uppercase micro-labels · bracketed IDs · meaning-colors (red source/amber victim/teal healthy). **Dropped:** hex wallpaper · glow-everything · amber triangles · double-frame chrome. **Sections:** Boot · Causal Monitor · Nodes · Scenarios · Recommendations. Mockups reviewed and **approved** ("everything looks solid").

**LOG-010 · Typography = the Industrial font.** Use the Industrial typeface for the reskin (confirm exact file/web-font for the dashboard).

**LOG-011 · Reskin implementation approach.** A **theme + light chrome layer over `core/dashboard`** (Next.js): palette, Industrial font, header/bracket chrome, status colors. The 3D causal graph + all data wiring stay as-is — **no engine/logic changes.** Pending the user's explicit "go".

**LOG-012 · 2026-06-30 · Reskin GO — implementation pass 1 (theme layer).** The user gave the go; built the restrained VISR skin as a **theme + light chrome layer over `dashboard/`** (Next.js, `output: export`) — **no engine/graph-logic changes.** Files: `app/globals.css` (palette retuned to the graph's meaning-colors — red source / amber victim / **teal** healthy `#5dcaa5`; **cyan `#36c5e0`** single accent; mono uppercase micro-labels; `.bracketed` corner-bracket utility), `app/layout.jsx` (display font via `next/font`, **self-hosted → air-gap export preserved**; metadata → VISR), `app/page.jsx` (3 edits: topbar → "▣ VISR · Causal AIOps", corner brackets on the **Verdict** panel, `className` passthrough on `Panel`). **Build-verified:** `next build` ✓ compiled, 4/4 static pages. **Open:** the exact **Industrial** font is still a **placeholder** (`Saira Semi Condensed` via next/font) pending the real file/web-font; visual review with live data pending (needs the desktop engine for `/api/*`). NB: memory/plan said `core/dashboard` — the real path is `dashboard/`.

**LOG-013 · 2026-06-30 · Reskin = full section redesigns (supersedes the LOG-012 recolor); work moved to the canonical repo.** The deployed LOG-012 (thin theme layer) read **"way too minimal"** — the approved mockups are **full section redesigns** (Boot · Causal Monitor · Nodes · Scenarios · Recommendations), not a recolor. Rebuilt `dashboard/app/{globals.css,page.jsx}` as a VISR design system + 4 **live-wired** sections: Causal Monitor (3D `<Graph>` + ROOT CAUSE panel from `/api/graph`+`/api/narrative`), Nodes (`/api/pods` ⋈ `/api/pod-resources` ⋈ graph roles → hot/strained/ok), Scenarios (catalogue + client live-state + Fire/Reset), Recommendations (derived causal `throttle` from the root edge + `/api/recommendations` resize/reclaim + Gini fairness). `next build` ✓; **render-verified via a local dev preview** (dev-only mock in `page.jsx`; prod export = live data only). **Canonical work now lives in `ABB_Accelerator_Codex`** (laptop `~/Documents/Linux_Projects/ABB_Accelerator_Codex`, a *full* clone, origin `GreaseMonkeyIT/ABB_Accelerator_Proto`) on **`mark-two`** — it has working `skctl`/deps; the Tata-InnoVent synced clone is degraded and was reset to mark-one. **Open:** exact **Industrial** font (placeholder `Saira Semi Condensed` in place — user asked for the real one); the **Boot** section (not built). Deploy = rebuild `skn/dashboard:v0.1` + `k3s ctr images import` + `kubectl -n aiops rollout restart deploy/dashboard`.

**LOG-014 · 2026-06-30 · Industry font + Pods circular meters + Grafana retained/expanded (render-verified).** Wired the real **Industrial = Industry (Fontfabric)** font, self-hosted via `next/font/local` (`dashboard/app/fonts/IndustryTest-{Medium,Demi,Bold,Black}.otf` → weights 500/600/700/800; trial "Test" set, licensed swap later). Renamed **Nodes → Pods** (they're workloads on a single-node K3s, not K8s nodes). Revamped per-pod metrics into **circular meters** (CPU / MEM / **I/O**, % in centre, teal/amber/red thresholds) with a **hover popover** showing allocations-vs-utility in **MiB / millicpu** (from `/api/pod-resources` request/limit/usage). **Retained the Grafana PSI graph** and added **PSI CPU + memory** panels (`deploy/grafana-psi-dashboard.yaml`, panelIds 2/3), embedded as three `d-solo` iframes in a new **Pressure (PSI)** section. `next build` ✓; render-verified via local preview (gauges, Industry font, hover content). **Deploy now = TWO cluster changes:** (1) rebuild `skn/dashboard:v0.1` + import + rollout; (2) `kubectl apply -f deploy/grafana-psi-dashboard.yaml` (grafana sidecar reloads ~30s — else PSI CPU/mem panels show "not found"). Still open: **Boot** section.

**LOG-015 · 2026-06-30 · Reskin shipped & confirmed live; entering the registration push (T-5 to 2026-07-05).** Full VISR redesign deployed on `mark-two` and confirmed working by the user (Grafana PSI live; only follow-up fix = taller `d-solo` embeds so the per-workload hover tooltip isn't clipped). **Stage-1 product is registration-ready.** Remaining for July 5: deck refresh (VISR shots + §3.2.2.5 reframing) · demo recording (S0 silent → S1 → verdict/blast/recommendations → reset) · Drive folder + college IDs · the form. **Gates:** ABB reuse T&Cs (IP/originality) + a female teammate (§6.0). **Deferred to after registration:** Stage 2 (per-pod simulated domain data that reacts to scenarios + S2/S3/S5 scenario fixes) and Stage 3 (repair agents / act-loop). Single-working-copy gate resolved (canonical = `ABB_Accelerator_Codex` @ `mark-two`, git-flow). Also pending: **Boot** section; swap trial Industry weights → licensed.

**LOG-016 · 2026-07-02 · BOTH registration gates resolved.** (1) **Female teammate added** (§6.0 diversity criterion). (2) **ABB reuse cleared** — no NDAs on the codebase; ABB uses our code as a base, so reusing it for InnoVent poses no IP/originality conflict. Registration is now blocked only on deliverables (deck · demo recording · Drive folder + college IDs · the form). Context also on file: team WhatsApp debate (Kishan/Aaryan) on extending causal attribution to **hardware faults** (RTOS logs, PLC tags, SCADA-as-pod, wiring-plan topology input, sensor trust scores) + ABB Theme-1 (next-gen HMI) framing + the ConfidenceOS (ABB winner) advisory-layer reply — **directional Stage-2/3 material, no decision taken**; Kishan's caution stands: one clear USP for the pitch, don't dual-pitch hardware+software agents at InnoVent.

**LOG-017 · 2026-07-02 · Pods section → matrix of cards (dashboard-only, no engine change).** The row layout wasted the full panel width per workload (name left, meters far right, dead middle — worse with 13 rows). Rebuilt as a **responsive card matrix**: `.pods` = CSS grid `repeat(auto-fill, minmax(300px, 1fr))` (3-up at the 1080px app width), each card = status-bordered column (dot + name + ns header, CPU/MEM/I/O circular meters spread beneath). Cards are **status-ranked** — root (hot) first, then blast-radius (strained), then steady — via a stable client-side sort that preserves the API's hottest-first order within each rank. The hover popover was rebuilt as a **use | req | lim mini-table** (mono, tabular-nums) anchored to card width so it can't overflow the grid. Files: `dashboard/app/globals.css` (+ the earlier uncommitted 220→420px `.gframe iframe` fix rides along), `dashboard/app/page.jsx`, `.claude/launch.json` (+`dashboard-dev` preview config). Verified: `next dev` render (3-col matrix, rank order, popover geometry via forced-visible check) + clean `next build` ✓ 4/4 pages (note: stop the dev server before building — a concurrent dev server corrupts `.next`). **Deploy = rebuild `skn/dashboard:v0.1` + `k3s ctr images import` + `kubectl -n aiops rollout restart deploy/dashboard`** — one rebuild ships both this and the taller-Grafana-embed fix.

**LOG-018 · 2026-07-02 · Stage-2/3 build plan authored — `INNOVENT_MASTER_PLAN.md` (docs only, no code).** ABB-style
master plan for after registration; Stage 1 untouched. **Stage 2 = "make the factory speak its own language":** 2A truth
pass (backbone/root-promotion fix test-first + S2 sync-fio + young-baseline findings; two attempts then keep-S2-out
decision), 2B domain data layer (ONE `domain-probe` pod; **probes = really measured** [live DB query timing, MQTT RTT]
preferred over **derived gauges** [computed from real resource state, labeled "simulated"]; factory images untouched;
D-004 letter-change gets its own LOG entry), 2C domain correlation (db_query_latency as a new engine signal family via
config + witness map — gated, droppable), 2D PoC package (Boot section, licensed font, claimRef bake, recorded video).
**Stage 3 = "cross the substrate and close the loop":** 3A ESP32 ingest (fan-in `/window` proxy + heap-leak scenario →
OOM-forecast card on physical hardware; device-NAMING trap flagged — workload() strips dash-suffixes), 3B PLC ingest
(CONDITIONAL on MicroLogix access; tag_server-as-pod + /metrics), 3C wiring-witness device chain (two ESP32s on one
power rail, ADC rail-voltage signal; reuses 2C's signal-family plumbing; STRETCH goal), 3D act loop (verb 1 `throttle`
= confirm-gated CPU-quota cut on the source — bench-verify relief visibility, fallback verb `pause`/scale-to-zero;
action ledger; verb 2 hardware `derate` testbed-only; cite-or-die + human-confirm throughout), 3E finals package
(narrator plant-language prompt pass, physical table, fallback recordings, failure drill). **Priority rule: 3D verb 1
outranks the 3C chain** (completes detect→explain→recommend→act = the pitch spine). Standing fallacy guards: measured-
or-labeled, run_pass purity (test-first exceptions only), gates never loosen for demos, two-attempts-then-decide,
box-verified phase ends, claims match the build. Known unknowns to pin: Stage-2/3 official dates, PLC access, hands,
venue constraints. Plan self-checked once (reiteration pass fixed: 3C's hidden dependency on 2C plumbing, the device
naming trap, the throttle-verb I/O-ceiling overclaim, the D-004 evolution note).

**LOG-019 · 2026-07-02 · Master plan extended: Phase 2E (Secure pass) + §8 "VISR OS" north star + §9 device ladder
(docs only, no code).** Operator's directive: the goal to show judges = (1) true hardware-agnosticism, (2) a
nationwide framework — an authorized Tata engineer connects to any enrolled asset (eventually a vehicle), reads
metrics, runs approved tests remotely, protocol-agnostic ("VISR OS"). Research findings baked in: **timeline PINNED**
(Virtual PoC = October 2026, final demo = January 2027; judging = novelty/feasibility/diversity/impact/prototype
strength; partners = Emerson + AWS → frame "edge-first, cloud-optional"). Industry survey → gap analysis vs the
§3.2.2.5 title words: **"Secure" is the blatant hole** (we have no login/TLS-policy/device identity/audit; industry =
IEC 62443 thinking, identity-based encrypted mesh, RBAC, audit) → **new Phase 2E** (nginx TLS+login, viewer/operator
roles, 401 on anonymous state changes, audit trail [= the 3D ledger built early], per-device tokens on the fan-in;
executes BEFORE the 2D recording; "62443-aligned, not certified" wording mandated). "Connected" gap = no standard OT
protocol + single-site → adapter-pattern story (3B proves it) + OPC UA driver/federation as roadmap. Vehicle story
grounded in standards: **VSS/KUKSA (signal taxonomy+broker) + SOVD/ISO 17978-3 (HTTP+JSON+OAuth2 remote diagnostics,
bridges UDS)** — punchline: the newest vehicle-diagnostics standard chose the architecture our API already has; what
nobody standardized is the causal brain on top = our seat. §8 fixes the show-vs-say line per stage (finals gets the
honest remote beat: engineer in one place, asset in another, over the existing Tailscale mesh, action → audit log;
never demo "vehicle" without a real feed). **§9 device ladder:** D0 pods ✓ · D1 ESP32 (3A) · **D2 Raspberry Pi —
PARKED per operator ("keep in plans, don't work on it")**: (a) Seam-B Linux device w/ REAL kernel PSI, (b) second K3s
node (arm64 images + same-node witness must scope by node label — service comment already flags it), (c) standalone
mini-VISR federation unit · D3 PLC (3B) · D4 vehicle (OBD-II proof point ONLY if car+dongle available and 3C/3D are
done; else the §8 slide). §7 reconciled with LOG-003: the VISR platform BUILD stays dropped; "VISR OS" returns as
roadmap STORY with staged proof points only. LOG-018's sequencing table updated (2E inserted, executes before 2D).

**LOG-020 · 2026-07-02 · Last year's top-10 cohort analyzed (operator supplied the list) → master plan §10 (docs
only).** 9/10 finalists automotive/EV (caveat: smart-mobility theme year — but the judge DNA is automotive, so §8's
vehicle bridge is validated; industrial lane likely thinner). **Closest analogue = T-Factor** (on-vehicle EV fault
detection: "38ms / 92% root-cause / 88% self-heal / fully offline", ASIL-B+WP.29 vocabulary, twin + hardware
prototype demo) → lessons: quantified-claims culture (answer with MEASURED reproducible numbers + soak receipts +
fire-it-yourself faults — verifiable beats big); their "self-heal 88%" is the opaque version of our 3D act loop
(pitch ours as the defensible, OT-acceptable version); compliance vocabulary lands (vehicle slide adds WP.29
R155/R156 next to SOVD). **Cheap steals adopted:** "causal digital twin — learned, not drawn" deck line (twin
appeared 3×, honest claim via graph+baselines+cases); **hash-chained tamper-evident audit ledger added as optional
2E step** (from Unified Logic's integrity-only top-10 entry); Pi-as-gateway normalized (2×, §9 D2 reads familiar);
physical rigs on finals tables (3×, confirms 3A/3C/3E); socio-economic quantification of problem slides (verify any
borrowed stat first). **The gap nobody filled: cross-source causal attribution — every entry is single-asset
intelligence.** USP survives the field: "everyone detects anomalies; we explain them, across sources, with evidence."

**LOG-021 · 2026-07-02 · Witness design for physical systems + a 3A ordering fix (docs only).** Q&A with the operator
("what witnesses exist for physical systems?") produced the taxonomy: coupling media = electrical (shared rail/PSU —
from SLD/panel schedule) · communication (same bus/switch/broker/gateway — partially AUTO-discoverable via LLDP/broker
client lists) · control (same PLC / interlock chains — the **PLC tag map we already own is a free witness**; the
cause-&-effect matrix is a declared causal graph) · mechanical (shaft/belt/material flow — directed) · process/fluid
(shared compressor/coolant — from P&ID) · thermal (same cabinet) · spatial (same room) · operational (common
technician visit / OTA rollout from CMMS logs). Maps 1:1 onto the existing `Witness` fields (shared_relation /
ebpf_edges-directed / same_node / co-pressure-corroboration); doctrine reaffirmed: **inferred coupling is
corroboration only, never a primary witness** (no correlation manufacturing its own witness). **LOGICAL ERROR found
and plan-fixed:** service `_witness_for` blanket-declares same-node + co-pressure across ALL window entities (single-
node assumption) → the moment 3A's fan-in merges a device, it would FABRICATE device↔pod CPU/mem witnesses. Fix =
**coupling-domain registry** (ConfigMap `node:…/disk:…/rail:…/plc:…`) + signal-family→medium map; witnesses only from
declared shared domains; **cross-substrate default-deny** (no declared coupling → no edge, automatically honest).
~50-80 service-layer lines; run_pass/gate untouched; 3 new fixture cases (device-alone finding · device+pod co-deviate
NO edge [the regression test] · declared-rail pair edge admitted). **Moved from 3C to 3A step 1 as a prerequisite**
(the bug fires on fan-in, before any wiring plan exists); the registry doubles as the seed 3C plugs the wiring plan
into. Master plan 3A updated (steps renumbered).

**LOG-022 · 2026-07-02 · Domain registry must be ATTESTED, not hand-typed (supersedes the "declared ConfigMap" reading
of LOG-021).** Operator's (correct) objection: per-device manual declaration doesn't scale and contradicts the VISR-OS
"seamless enrollment" story. Resolution: the doctrine was never "humans declare" — it's "**something other than the
correlated signals must attest the coupling**" (pods were never hand-declared either: Caretta/K8s-API/scheduler attest
them). Four attestation tiers: **T1 enrollment self-description** (device reports gateway/AP/subnet at token time;
gateways auto-emit children — fan-in knows its feeders, broker knows clients, **the PLC tag server already knows its
stations** → `plc:` domains for free; the ONE legitimate human field = "installed at station N"); **T2 discovery
agents** (the hardware Carettas: LLDP/SNMP switch walker, broker-watcher, AP-association reader → comms/control
domains fully auto); **T3 one-time document ingestion per PLANT, not per device** (P&ID/SLD/C&E → domains; new
devices attach via their T1 station field); **T4 probational engine inference** (fingerprint channels DISTINCT from
fault signals — e.g. rail-ripple micro-transients — learned on long QUIET baselines only, entering as low-prior
probational domains via the existing memory machinery [prior/EWMA-confirm/decay/floor pointed at domains]; must be
re-confirmed or decays). Guardrails: every edge carries **witness provenance** (`declared`/`enrolled`/
`discovered:<agent>`/`inferred:probational`) in its evidence; **tiered gate** — probational domains demand stronger
stat+temporal evidence and render as *suspected* (dashed). The LOG-021 ConfigMap = the substrate attesters WRITE INTO
+ human override slot, not a hand-maintained map. Stage impact: 3A happy path = ZERO hand-written domains; 3C bench
rail = one declared line (fine); T2/T4 agents = §8 roadmap ("the plant maps itself; the engineer confirms, not
types"). Deck line captured.

**LOG-023 · 2026-07-02 · ESP32 firmware hardened (operator's code review → 7 fixes applied to
`Documents/Google/MCU/firmware/esp32s3_visr/esp32s3_visr.ino` + README).** Operator flagged, I fixed: (1) **fidelity
labeling** — new header block MEASURED (mem/mem_limit/io_write/psi_io/psi_cpu) vs SYNTHESIZED (cpu estimate, psi_mem
frag-proxy, SPI-share guess); internal `cpu_est` + serial prints `CPU~`; **wire keys keep canonical names** (Seam B =
L3 contract, renaming would break SIGNAL_SOURCES ingestion) — fidelity lives in comments/README/dashboard label, not
the key. (2) **Stress-file rotation** — cap 64MB (`STRESS_FILE_MAX_BYTES`), approx-size tracking (no per-cycle
size() probes), SD.remove+reset on cap; append-forever would fill the card → write failures → pathological psi_io
reading as a real incident. (3) **psi_mem div-by-zero guard** — free heap hits 0 under REAL OOM (exactly what the S5-
style leak demo drives toward!) → old 0/0=NaN would poison the JSON mid-demo; now free_heap<=0 ⇒ psi_mem=1.0. (4)
**Streamed /window** — chunked transfer (CONTENT_LENGTH_UNKNOWN + sendContent), reused 1.6KB buffer, snprintf into
stack char[]; the old ~60KB String build fragmented the heap on every poll (the exporter was contributing to the
psi_mem it reports). (5) **Bearer-token auth** — `DEVICE_TOKEN` const (empty=lab mode); 401 otherwise; requires
`server.collectHeaders(["Authorization"])` (WebServer only stores requested headers — easy trap). Aligns with 2E
device-token groundwork. (6) **Bounded WiFi + SoftAP fallback** — 3×15s attempts then `VISR-ESP32-Setup` AP (no
brick-on-boot); documented consequence: AP mode has no NTP → epoch timestamps → engine's grid alignment auto-ignores
the device = intended fail-safe (**no trustworthy clock ⇒ no causal claims**). (7) **Named heuristics** —
`WIFI_CPU_BASELINE=0.05`, `SPI_CPU_DIVISOR=4` with "a guess" comments. **NOT compile-verified** (no Arduino toolchain
on this laptop) — flash-test is the first 3A errand. README updated (fidelity note + Hardening section + token step).

**LOG-024 · 2026-07-02 · Why a wall-clock TSDB and not Lamport logical clocks — resolved + clock-budget rule (docs
only).** Operator's challenge: logical ordering (Lamport '78) is the proven distributed-systems tool, yet we correlate
via wall-clock time series — "what gives?" Resolution: **two regimes.** Logical clocks order *instrumentable
communication* (send/receive events; distributed tracing = Lamport in production). Our causality flows through
**shared physical media** (disk queue, power rail, coolant) — no message to stamp; this is Lamport's own "external
channel" case (the phone-call anomaly), for which HIS paper prescribes **physical clocks with error ε < propagation
time μ**. The TSDB is therefore the *prescribed* instrument, not a naive compromise — and correlation additionally
needs history (r-at-lag, shapes, slopes), which order alone cannot carry; lag magnitude is itself evidence (media
have characteristic propagation times). **Clock-budget rule pinned in §9-Annex:** per source ε = max(sync error,
sampling/poll period) — pods ε≈0 · ESP32 SNTP ≈ tens of ms · **PLC = the gateway's 2s POLL period (engineer cadence,
not NTP — 3B)** · electrical μ≈µs = unmeetable ⇒ direction from ROLE ASYMMETRY (load-carrier leads; generalizes
writer-leads-staller), never from time — the 3C position now has its theoretical justification. Where we DO have
stampable events (audit ledger) we already use causal chaining = a Lamport total order. No engine change; vector
clocks on signal ingestion would be over-engineering (RULES: simplicity).

**LOG-025 · 2026-07-02 · Deck update path defined (`SiliconKnights_Final - Copy.pptx`, 16 slides, still fully
ABB-era).** Audit: good bones (architecture L0-L4, deterministic-inference story, honest expected-vs-actual matrix on
s16) but ABB Theme-2 title + "ABB Accelerator" footer everywhere, pre-VISR presentation-layer claims (React Flow/
Recharts/WebSocket — reskin not reflected), broken footer numbers (s5/s11/s12 say "4"/"0N"), NO dedicated S5 slide,
and 3 content bugs: s12 S0 shows "✓ storage contention detected" copy-paste block (steady-state slide!), s13 S1 says
"read flush" + wrong chain (truth: WRITE storm, cooling-monitor → shared PVC → timescaledb + dcim-bridge, verdict
~45-50s, [stat,pvc,write,temporal]), s14 S2 reads as fully working vs s16's honest "◐ partial". **5-pass path:**
P1 global rebrand (footers, §3.2.2.5 title + Connected/Secure/Intelligent mapping line, ADD FEMALE TEAMMATE — name
needed from operator); P2 accuracy fixes (S0 findings:[] silence-is-a-feature, S1 corrected chain + measured numbers,
**REPLACE S2 slide with an S5 OOM-forecast slide** — S2 survives only as the matrix ◐ line w/ LOG-100 wording);
P3 de-stale (s6/s10 → shipped VISR description, re-shoot s2/s5/s11 screenshots — GATED on box rebuild of pods-matrix
+ 420px embeds then fired-S1 capture; soften Loki/Alloy to "deferred"); P4 story layer (+Problem/validation slide
[quantified pain + Theme-1 OEM evidence + Cummins story], +Roadmap slide [Stage 2 Oct: probes+secure pass · Stage 3
Jan: act loop+ESP32/PLC rungs, all labeled PLANNED], +VISR OS closer [device ladder, VSS/KUKSA/SOVD/62443-aligned/
WP.29, ConfidenceOS-dialect boundary, USP line], cohort-gap row on s3, "causal digital twin — learned, not drawn" on
s4, s16 retitle + mark-two line); P5 QA (grep ABB remnants, thumbnail render, visual QA, PDF export → Drive).
Target ~18 slides. Operator dependencies: teammate name (P1) + box deploy & screenshots (P3); P1/P2/P4 executable
immediately on request.

**LOG-026 · 2026-07-02 · L0 = reframe-not-scrap + REAL pod rename decided (map PENDING operator approval).** Floor-
level positioning settled over three exchanges: (a) K8s on a real floor = the **L2/L3 runtime** (SCADA/historian/
gateways/alarm-chain/inference — the software plane above tens of PLCs & hundreds of stations), NEVER the control
loop; a SCADA stall doesn't stop machines, it makes production **unsupervised** — VISR keeps the plant's nervous
system diagnosable; "consolidation creates our disease" = lead argument. (b) §3.2.2.5 "Industrial Systems" = the
cyber-physical production stack (mobile machines/J1939 · fixed plant machinery/PLC-SCADA · site ecosystems · the
edge+trust fabric) — NOT data centers; we occupy the infrastructure dimension of Connected/Secure/Intelligent.
(c) **L0 is NOT scrapped** — 13/15 pods already ARE the L2/L3 population; pods-as-PLCs would be scripted-theater.
(d) **Operator called the names fake ("wtf is cooling-monitor, why does it write to a harddrive") → REAL RENAME
decided.** Rule: name = real plant service whose real behavior matches the pod's resource behavior. Proposed map:
cooling-monitor→**waveform-recorder** (CM waveform capture = genuine heavy fsync writer; alt cctv-recorder) ·
timescaledb→**historian-db** · dcim-bridge→**energy-logger** · dcim-exporter→**oee-exporter** (DCIM = wrong-world
datacenter vocab) · alert-dispatcher→**alarm-manager** (ISA-18.2) · notify-gateway→**andon-gateway** ·
safety-interlock→**interlock-monitor** · critical-control-relay→**command-relay** (fixes Purdue-violating names) ·
anomaly-sink→event-sink · KEEP plc-gateway/mqtt-broker/telemetry-ingest/vision-qc/log-archiver/analytics-batch.
**Churn list:** chart+values (fio env moves) · scenario trigger/reset scripts · STORAGE_WORKLOADS env + service.py
default · queries.yaml latency_p95 regex (critical-control-relay.*→command-relay.*) · image retags · page.jsx mock+
SCN strings · **memory-DB RESET (baselines/edges/cases keyed by workload — plan the 15-20min relearn)** · **StatefulSet
rename + slowdisk claimRef care (LOG-008)** · INNOVENT docs+deck get new names, ABB-era docs stay historical + name-map
table. **Timing options:** A = rename before demo recording (real names in deck/video; spends deadline margin) vs
B = Stage-2 opener; recommended **A-with-insurance** (bank a recording on old names first, then rename+verify+
re-record; git revert = fallback). ALSO pending from this thread: D4 vehicle rung → **J1939/mobile-machine** reframe
(OBD-II car = accessible stand-in) + floor-positioning into §8/deck ("Every intelligent factory now runs on an edge
computer nobody watches; VISR is the brain that watches it").

**LOG-027 · 2026-07-02 · THE PIVOT (operator): the demo factory becomes a PHYSICAL-resource contention plant —
"same soul, the resources contended on change."** Operator's directive: the factory = a sensor/PLC array emitting
physical values; floor devices don't contend for cpu/disk/ram (rare) — they contend for **current, energy, voltage
sag, water, coolant**; L0 gets rebuilt for this; k3s remains the factory simulator ONLY if required (better PLC/
sensor/MCU emulators may run the plant, multiple instances); **SCADA + all engine layers stay on k3s.** My scoping
(pending operator confirm): **the soul survives verbatim** — run_pass/gate/ranking/state/forecast are signal-name-
agnostic (vectors in, graph out); families+witnesses+forecast targets are CONFIG (the 2C mechanism + LOG-021/022
domain registry — §9-Annex taxonomy becomes the PRIMARY witness system); forecast generalizes (coolant-temp→trip,
tank→capacity, load→rating). **Genuinely new build:** (1) the plant emulator — CRITICAL principle: **physics, not
scripts** (a lumped dynamical model: electrical bus w/ source impedance → aggregate draw sags voltage for the whole
rail; coolant loop w/ pump curve + heat loads; compressed-air header — faults injected into the MODEL so causal
chains EMERGE and the engine must genuinely infer them; scripted traces would make us NexOps). Candidates: small
Python co-sim harness (recommended: 1 plant-physics core + N device agents over Modbus/MQTT, 1-2 OpenPLC instances
for authenticity, read via our PLC-SCADA-Custom tag server) vs OpenPLC-heavy vs Factory-I/O (commercial). (2) device-
signal ingestion packs (queries.yaml/Seams — carry over), (3) dashboard vocabulary+units (V/A/°C/bar vs %/MiB),
(4) scenario console fires MODEL faults not kubectl. **Recommended synthesis: TWO-PLANE story** — physical plant
(simulated physics, labeled) + the edge box itself (REAL kernel faults, current engine unchanged) watched by one
brain; keeps the real-faults credibility anchor + the 3A/3B real-device rungs converge into the same graph.
**Rename impact: partially superseded** — the 15 software pods mostly get replaced by the emulated plant + real
SCADA stack in Stage 2; registration keeps current names (deck TEXT reframes only); approved names apply to
surviving software-plane services. waveform-recorder→**timeseries-recorder** per operator; **dcim-bridge actual
behavior established** (main.go: 4MB snapshot every 5s + fdatasync to shared PVC + write-latency histogram = a
periodic durable snapshot writer / S1 canary victim) → name candidates by behavior: production-logger / batch-logger
(ISA-88). Timing: July-5 FROZEN as-is; pivot = the new Stage-2 core (absorbs 2B/2C; master plan Stage-2 redraft
pending operator confirm of two-plane + physics-not-scripts).

**LOG-028 · 2026-07-02 · S1-S5 under the pivot: motifs survive, scenarios retire to the bench.** Operator asked how
S1-S5 stay relevant → they don't, as headline demos. They were **causal MOTIFS** on the only physics we had (the
kernel): S0 silence-discipline · S1 shared-medium cascade · S2 no-baseline batch aggressor (its failure already paid
for the 2A fixes the physical plane NEEDS) · S5 ramp-to-limit forecast · S3/S4 compute/comms starvation (parked).
**Physical rebirth map:** S0→S0 (steady plant) · S1→rail-sag cascade (motor overdraw → bus sag → rail-mates degrade;
witness=rail: domain) · S2→duty-cycle aggressor (compressor/furnace periodic load; young-baseline + backbone-demotion
prerequisites) · S5→thermal/level runaway (coolant-temp→trip, tank→overflow) · S3/S4→reborn as comms/bus contention
(Modbus polling starvation, gateway saturation — finally measurable). **Literal S1-S5 keep 3 roles:** (1) permanent
REGRESSION BENCH for the soul (box-verified fixtures + soak; can't regression-test against an unbuilt plant),
(2) native failure modes of the edge box IF two-plane stands (historian compaction storm = real S1; trend-service
leak = real S5), (3) the frozen registration demo + October fallback. Process: plant scenarios get PS-series IDs
(one per motif/medium); DEMO_RUNBOOK splits demo-scenarios vs regression-scenarios. Pitch line: "proven on these
causal shapes in the kernel where faults were real; the plant re-poses the same shapes in plant physics."

**LOG-029 · 2026-07-02 · PIVOT EXECUTION STARTED — Stage-2 redraft + plant scaffold + ground-up runbook (operator:
"start"; workspace = the Tata InnoVent clone, Codex FROZEN).** Defaults adopted per my LOG-027 recommendations
(operator implicitly confirmed by ordering the start): physics-not-scripts ✓ · two-plane ✓ (plane 1 = the edge box
watches itself, REAL psi on aiops|observability|plant; plane 2 = simulated plant physics, honestly labeled).
**Shipped into the clone (`Tata InnoVent/ABB_Accelerator_Proto`):** (1) `plant/sim/main.py` — stdlib-only physics
emulator: rails A/B with source impedance (V = Vsrc − I·R → aggregate draw sags the rail for everyone), coolant loop
w/ pump curve + per-machine first-order thermal lags (REAL lag structure), 8 assets (press-1/2, cnc-1 [V-sensitive],
qa-scanner-1, conveyor-1, compressor-1 [300s duty cycle = the PS2 no-baseline aggressor], furnace-1, chiller-1),
faults perturb the MODEL only: **PS1** press-1 friction→rail-A sag cascade · **PS2** compressor stuck-on · **PS5**
pump degradation→temps ramp to TRIP_C=78 (forecast target); `/metrics` labels series namespace="plant",pod="<asset>"
so L2 ingests unchanged (honorLabels), `POST /fault/<id>` + `/reset` + `/state`. (2) `plant/Dockerfile` +
`plant/deploy.yaml` (ns plant, PVCs historian-data 64Gi + plant-shared 5Gi, plant-sim deploy/svc/ServiceMonitor
[release=prom], historian-db StatefulSet = real TimescaleDB on the 64Gi). (3) **`deploy/slowdisk.yaml` REWRITTEN
with claimRef BAKED** (historian-pv-slowdisk→plant/historian-data, plant-shared-pv-slowdisk→plant/plant-shared;
paths /mnt/slowdisk/{historian,plant-shared}; LOG-008 cross-bind now impossible). (4) `aggregator/queries.yaml`
pivot pack: plane-1 kernel queries retargeted factory-.*→aiops|observability|plant, CCR latency_p95 RETIRED, plane-2
plant_* passthrough queries (bus_voltage/current_draw/coolant_temp/coolant_flow/heat_load/throughput/temp_limit).
(5) **`PIVOT_SETUP.md`** — the full ground-up runbook: prereqs (k3s/helm/docker/ollama installs), repo prep (Syncthing
exec-bit chmod fix = the "couldn't run skctl" cause), SAFE teardown (helm uninstall factory + delete factory-* ns,
observability/aiops untouched, engine memory wipe [old workload keys stale]), 64Gi/5Gi ERASE + reallocation +
claimRef verification, 5 image builds + k3s ctr imports, skctl up --components telemetry,engine,language,dashboard +
plant apply, end-to-end verify (metrics→Prom target→/window plant keys→fire PS1 watch cascade in /state), honest
works-now-vs-pending table, rollback path (Codex resurrects the old factory). **Master plan updated:** Stage-2
preamble (pivot + workspace split), 2B→**2B′ plant emulator**, 2C→**2C′ plant families + domain witnesses** (until
that patch: plant families = findings-only, NO edges — safe by construction; enable ENGINE_SIGNALS only WITH the
patch), 2D script → PS0→PS1→PS5, sequencing table updated. **Next code session: the 2C′ service.py patch** (domain
witness map + PLANT_SOURCES + coolant_temp→temp_limit forecast pair) + PS-series into the scenario API + VISR
dashboard port from Codex + fixtures (no-shared-domain→no-edge regression case).

**LOG-030 · 2026-07-02 · Clone de-staled — VISR synced from Codex (operator: "the dashboard and stuff is not up to
date, check those too").** Checksum audit Codex↔clone: api/main.py, aggregator/main.go, correlation/service.py +
all 9 engine modules, Graph.jsx, PodResources.jsx, dashboard package/Dockerfile/nginx, all deploy yamls + values +
scenario dirs = **SAME** (clone sits at mark-one HEAD 01d29bb, which already includes LOG-105). Drift = exactly 5
artifacts, all mark-two/uncommitted-era: dashboard/app/{globals.css, page.jsx, layout.jsx} (VISR reskin + Industry
font wiring + TODAY'S pods matrix + 420px embeds — Codex working tree carried the uncommitted LOG-017 edits, so the
clone got them too), dashboard/app/fonts/ (4 IndustryTest OTFs, missing), deploy/grafana-psi-dashboard.yaml (clone
had the pre-LOG-014 single-panel version). All 5 copied Codex→clone, checksums verified. PIVOT_SETUP updated:
grafana-psi apply added as step 5.2; honest interim notes added (Pods matrix shows engine-plane pods — plant ASSETS
need 2C′-era UI; Scenarios section's S-buttons target the torn-down factory → error politely; PS-series fires via
curl until the API is wired). Clone dashboard image build now needs no extra steps (fonts in place, package.json
already matched).

**LOG-031 · 2026-07-02 · Post-bring-up de-factoring — the pivot stack now runs clean end-to-end (operator ran
PIVOT_SETUP on the box; three classes of factory residue found and fixed).** Bring-up results: teardown clean,
claimRef pinning HELD (historian-data→historian-pv-slowdisk 64Gi, plant-shared→plant-shared-pv-slowdisk 5Gi, both
Bound first try — LOG-008 bug confirmed dead), plant-sim + historian-db Running, VISR live on :30080 with plant pods
in the matrix. Residue fixed (all in the clone; Codex untouched): (1) **deploy/skctl** re-created factory-* ns +
helm release unconditionally on every `up`, ignoring --components — and the empty release under `set -e` aborted
the run; now gated behind wants_factory() (core|storage|compute|edge asked-for ⇒ factory installs; bare `skctl up`
still = full rollback path). (2) **deploy/api.yaml** still shipped the S2/S5 scenario Roles/RoleBindings into the
deleted factory-data/factory-edge ns → kubectl NotFound aborted skctl BEFORE dashboard.yaml; RBAC retired (SA kept).
Post-abort state also meant aiops pods were old-era: same-tag images (IfNotPresent) + startup-read ConfigMap ⇒
rollout restart of aggregator/engine/api/dashboard required to load the pivot queries + VISR image. (3) The
**query-plane residue LOG-029 missed:** api/main.py /api/recommendations (6 sizing queries + PSI stall) and
/api/pod-resources default still filtered namespace=~"factory-.*" → empty results mislabeled "Prometheus
unavailable" + CPU/MEM dashes; deploy/grafana-psi-dashboard.yaml all 3 panels ditto → "No data". All retargeted to
aiops|observability|plant (the LOG-029 regex), dashboard json version 2→3 for sidecar reload. (4) Dashboard
vocabulary: Pods header "workloads · factory"→"· edge", steady-state fallback line, layout.jsx meta description;
plus a REAL bug — page.jsx fairness filter kept only namespace.startsWith("factory") ginis, which post-retarget
would have silently hidden the fairness bar; now averages whatever the API returns. Render-verified in `next dev`
(mock): EDGE chips render, fairness bar alive, meters fill; known dev-only Graph forwardRef warning unchanged.
`.claude/launch.json` repointed Codex→clone (8.3 short paths — preview harness chokes on spaced paths). Prometheus
values' l0-fast job still names factory-* (dead scrape, harmless): left for 2C′ — do NOT fold plant into l0-fast,
it stamps channel=truth which would EXCLUDE plant series from edges. Box follow-up: rebuild+import skn/api +
skn/dashboard, restart both, re-apply grafana-psi ConfigMap.

**LOG-032 · 2026-07-03 · MACHINES section shipped — the plant floor becomes the dashboard's primary subject
(operator: "we need graphs for the machines within the factory floor; secondary is the pods, as they only host the
AIOps tool and SCADA").** New VISR section between Causal Monitor and Pods: assets grouped by SHARED MEDIUM (rail
psu-a / psu-b columns + coolant strip — the medium IS the causal story), per-machine DRAW/TEMP/THRU with real units
(A/°C/%) + min-max-domained sparklines (0-based scaling would flatten an 8V sag; PodResources ring-buffer pattern,
WINDOW=60), PS-fault badges, + 3 skn-plant Grafana d-solo trend embeds (bus voltage per rail · current draw per
machine · coolant temps vs trip; NEW deploy/grafana-plant-dashboard.yaml, uid skn-plant, no min:0 so sag/climb stay
visible). Pods demoted to "workloads · aiops + scada hosts". Data path: NEW /api/plant (api/main.py) proxies the
sim's /state (PLANT_URL env, baked in api.yaml); /state EXTENDED to carry static topology (rails as
{volts,v_src}, per-device rail + cooled, trip_c, loop nominals) so the UI needs no second source of truth.
**Sim calibration bug found & fixed while wiring temps:** heat_k=6.0 put steady temps at ~287°C — permanently past
TRIP_C=78, making PS5's "ramp to trip" physically impossible (forecast target unreachable; never caught because
§6.4 verified amps/volts/throughput, not temps). Recalibrated heat_k 0.55 default (press-1 steady 58°C, PS5 →
86°C ✓ crosses trip), furnace 1.0 (steady 65, PS5 → ~102). Engine unaffected (temps not in ENGINE_SIGNALS until
2C′) — this was the safe window. **Rail display bands tuned against a LIVE local sim run** (rail A idles 0.900·Vsrc
BY DESIGN — thin-margin electrical sizing; B dips to 0.887 in compressor windows; PS1 → 0.866): ok ≥0.882 /
strained ≥0.872 / hot below — steady reads calm, PS1 reads red, no boundary flicker; UI judges DISPLAY bands only,
deviation stays the engine's job. Verified: py_compile both, live /state + PS5 fire (pump 0.45, flow 120→54.4),
next-dev render (mock incl. PS1 snapshot: rail A red 346.3V, B teal 372.8V, uncooled tiles skip TEMP). PIVOT_SETUP
updated (5.2 += grafana-plant apply; honest notes; §7). Box follow-up: rebuild+import skn/plant-sim + skn/api +
skn/dashboard, apply api.yaml + grafana-plant-dashboard.yaml, restart plant-sim/api/dashboard.

**LOG-033 · 2026-07-03 · PLC/SCADA/3D-floor plan LOCKED into the master plan; Phases 0+1 EXECUTED (operator:
"yes, write them, and start with phases 0 and 1").** Decisions locked: single-path telemetry via SCADA (aggregator
repoints to the tag server once it proves; sim /metrics stays as unscraped debug) · FLOOR/GRAPH toggle (force graph
survives for the pods plane) · Modbus TCP only (OPC-UA out of scope; MCU emulation rejected as complexity theater).
Master plan: new **Phase 2F** (virtual PLC + SCADA — 3B pulled forward; OpenPLC + tag server + tags UI; the PLC
earns its place by CLOSING THE LOOP: latched thermal trips = PS5 consequence + the act loop's verb-0) and **Phase
2G** (3D plant floor, plain three.js, gated on 2C′); sequencing table renumbered.
**Phase 0 SHIPPED — the 2C′ patch (fixtures first, all green):** (1) gate.py `Witness.relation_kind` (default
"pvc" = every existing fixture byte-identical; plant witnesses carry "rail"/"loop" so evidence chips are honest —
the ONE engine touch, standing-rule-2 exception logged here) + pipeline `_writer_edge` accepts it; (2) service.py:
PLANT_FAMILIES/PLANT_SOURCES/PLANT_DOMAINS/PLANT_INVERT env config — domain-witness branch in `_witness_for` (no
same-node blanket, no co-pressure; no shared domain → NO edge by construction), `bus_voltage` ingested INVERTED
(sag = 400 − V, "higher = worse" preserved engine-wide), `workload()` guard (plant entities keep names verbatim —
the k8s heuristic mangled qa-scanner-1 → "qa"); (3) forecast generalized to FORECAST_PAIRS (mem:mem_limit:leak +
coolant_temp:temp_limit:trip; single-entity limit broadcasts loop-wide) — the PS5 trip-ETA card; (4) engine.yaml
bakes ENGINE_SIGNALS += bus_voltage,coolant_temp WITH the domain config (2C′ rule: together or not at all);
(5) fixtures `correlation/tests/test_plant.py`: co-deviation-without-domain→no-edge (regression), rail source edge
(root=press-1, "write"+"rail" evidence, NEVER "pvc"), trip forecast — full suite green incl. all pre-pivot fixtures.
**PS-series console SHIPPED:** /api/scenarios catalogue = PS0/PS1/PS2/PS5 (+S-series marked bench, untriggerable);
PS*/trigger→plant-sim /fault, PS*/reset→/reset; SCN in page.jsx replaced; narrator words += rail voltage / coolant
temperature (api SIGNAL_RESOURCE + page RES_WORD). Render-verified (eval): 4 PS entries, 3 Fire buttons, S-console
gone, no new console errors.
**Phase 1 SHIPPED AS CODE (box-verify pending):** plant-sim = the FIELD WIRING — pymodbus client thread (the one
new dep; import-optional) writes sensor words (%MW0..15 ×10 scaling via PLC_MW_BASE=1024, VERIFY on box) + reads
trip coils 0..3; tripped machine = contactor open (current→0, throughput→0, cools; rail voltage RECOVERS — physics);
PLC unreachable → fail OPEN (open-loop, reconnect 5s; a real safety PLC fails SAFE — say so if asked); /reset pulses
the reset word; /state += plc block + per-device tripped; /metrics += plant_trip_active + plant_plc_connected.
plc/: program.st (latched trips, condition-gated reset, TRIP=780 ×10), Dockerfile (OpenPLC_v3 source build, SLOW),
entrypoint.sh (headless login→upload→compile→start; UNPROVEN — fallback = one manual web-UI upload per pod restart),
REGISTER_MAP.md (the sim↔ST↔tag-server contract). deploy/openplc.yaml (Modbus :502 svc + web NodePort 30081);
plant/deploy.yaml sim env PLC_HOST=openplc.plant.svc. Verified locally: py_compile all, 50-test suite green, sim
open-loop run (plc:{connected:false,mode:open-loop}, trip metrics 0, reset OK), Machines tile shows red
"tripped · contactor open" when d.tripped. Box follow-up: rebuild correlation-engine/api/dashboard/plant-sim (+
openplc when ready), apply engine.yaml+plant/deploy.yaml+openplc.yaml, restart; then PS1 → expect root=press-1
with rail chips; PS5 → trip card then REAL trip.

**LOG-034 · 2026-07-04 · BOX-VERIFY executed — Tier-1 PASS (PS1 acceptance met live); OpenPLC gap found;
FLOOR/EDGE graph toggle shipped.** Box-verify run on the synced clone (`~/Tata_InnoVent/ABB_Accelerator_Proto`;
laptop↔box checksums verified identical across every pivot-touched file first). 4 images rebuilt + imported
(correlation-engine/api/dashboard/plant-sim; operator runs all sudo steps — working mode from here: Claude hands
paste-ready blocks, operator drives the box), engine.yaml + api.yaml + plant/deploy.yaml + both grafana ConfigMaps
applied, rollouts clean. Engine booted 2C′ (signals += bus_voltage,coolant_temp; rail/loop domains; FORECAST_PAIRS
incl. coolant_temp:temp_limit:trip). Passive proof during settle: the engine flagged OUR OWN deploy churn (plane-1,
real PSI) then let it decay; compressor-1 young-baseline root appeared and cleared; the sim's warm-up transient
produced honest trip ETAs that evaporated at thermal equilibrium (press-1 settled at 58.0°C — the LOG-032
calibration figure exactly). **PS1 fired → PASS: root=press-1 with `stat`+`rail` evidence chips (no `pvc`), rail A
red at 344.4V, rail-A machines degrading, rail B healthy teal** — the 2C′ acceptance gate is cleared on the box;
the registration demo decision resolves to the PIVOT STACK. **OPEN:** (a) OpenPLC — image built (source, 1.52GB),
sim reports `closed-loop`, but trips DON'T latch (press-1 82.0°C / cnc-1 90.6°C > trip 78 with contactors closed);
pod log shows only /login probe lines ⇒ headless entrypoint upload unproven (as LOG-033 suspected) and/or
PLC_MW_BASE=1024 wrong; diagnosis = web UI :30081 manual upload + Monitoring-tab %MW check. (b) api pod mem
early-warning (142/192 MiB forecast) — watch; demo blip risk. (c) PS5 acceptance not yet run. **FLOOR/EDGE toggle**
(operator ask; the LOG-033 2G toggle in interim force-graph form): Causal Monitor now switches between two causal
planes — FLOOR = plant entities (set derived from /api/plant itself, no second source of truth) over a standing
shared-media backbone (device→rail, cooled→loop, thin-grey net styling); EDGE = pod plane with the caretta
backbone; ROOT CAUSE panel stays global (the verdict is the verdict). Files: page.jsx (plane state + bipartition
filter + toggle UI), globals.css (.gtog), Graph.jsx (one tolerant line: net labels honor e.label). Cross-plane
edges can't exist (domain default-deny), so the bipartition is honest. Render-verified both planes (dev mock) +
`next build` ✓ 4/4. Deploy = dashboard rebuild + import + restart (operator block handed over).

**LOG-035 · 2026-07-04 · PS1 run #2 read + qa-name bug root-caused & fixed + demo-soak procedure locked.**
Second PS1 fire (T+35min baselines) did NOT meet the bar: **compressor-1 outranked press-1 (0.34 vs 0.20)** — the
young-baseline problem live: 35 min of history can't normalize the 300s duty cycle, so the compressor's rail-B
square wave fired a parallel deviation storm (whole-B `burst` findings + compressor→B-mates edges) and won the
ranking; press-2 surfacing as root candidate #3 = the known root-promotion wart. Run #1's clean pass = cycle-phase
luck. NOT a 2C′ bug — every edge carried honest witnesses (`rail`/`loop`, zero `pvc`, zero cross-plane; PS1's
rail-A chain fully present; bonus: press-1 friction heat produced real `coolant_temp` loop edges). 2A engine fixes
stay DEFERRED (gates never loosen for demos; no engine surgery on deadline eve). **qa bug:** `/api/graph` showed
`qa` for `qa-scanner-1` (and the narrator inherited it) — engine innocent; **api/main.py has its own unguarded
`workload()`** (the LOG-033 guard only went into service.py; qa-scanner-1 is the only 3-segment plant name so only
it mangles). Fix shipped: `_plant_entities()` TTL-cache (30s) sourced from the sim's `/state` via the existing
PLANT_URL (devices ∪ rails ∪ loop; fetch failure keeps last set — fail-safe = stale beats mangled), workload()
returns known plant entities verbatim; py_compile ✓. Side benefit: window-key parsing paths (sparklines etc.) stop
mangling plant names too. Pre-existing display warts noted, NOT touched (surgical): StatefulSet names still strip
(`historian-db-0`→`historian`). **Demo procedure for tonight:** reset PS1 → rebuild api (qa fix) → WIPE engine
memory (today's deploy churn + two PS1 fires polluted baselines) → **long PS0 soak ≥2h (≥24 compressor cycles)**
→ quiet-gate → fire PS1 **during a compressor-OFF window** (check /state amps first — choosing the demo moment is
honest; the physics still emerges) → **read the verdict at 3–4 min, not 2** (run-#1's clean read came later in the
fault window; sustained sag accumulates evidence) → record → reset → PS5. api-pod mem creep (`flap` finding)
still on watch.

**LOG-036 · 2026-07-04 · Operator directive: "the graph is static — reconstruct." Resolution: the ALGORITHM already
is static (domain registry = the pre-existing wiring; the pass only configures weights); the RENDERER was the lie.
LOD-1 Floor shipped + Causal Monitor restructured + PLC entrypoint root-caused & fixed.** Operator's point — a
factory is statically wired, edges never rearrange, "the graph exists from the get-go, only the edge weights are
not configured" — is exactly the LOG-021/022 architecture (PLANT_DOMAINS declares all possible couplings;
witnesses default-deny; runtime = weight/direction/activation). NO engine change made or needed (deadline-eve rule
upheld); the force-directed FLOOR view was retired because floating orbs imply *discovered* structure — wrong
metaphor for a declared plant. **Floor.jsx (new, ~200 lines):** LOD-1 isometric cuboids (size classes by asset
kind, cosmetic) standing on ONE ground plane; overhead bus bar per rail with 90° drops (PSU cuboid + riser at each
group head, live V readout); coolant trench run + risers to cooled machines (pump block + L/min); machine labels
with live A/°C; roles paint the fixed geometry (root red / blast amber / steady teal; `tripped` ⇒ dashed red +
OPEN); **the live causal overlay renders ON the fixed wires** — src→dst path along drop→bus→drop (or pipe for
loop edges), evidence-weighted stroke on the Graph.jsx contention ramp, marching dashes, fixed-size arrowheads
(markerUnits=userSpaceOnUse — strokeWidth-relative markers rendered huge), hover title = evidence. Edges with
unknown anchors are skipped ⇒ the floor filters itself; EDGE view (pods = genuinely discovered topology) keeps the
3D force graph + caretta backbone via `edgeGraph` (= everything not fully inside the plant set; the LOG-034
planeGraph/planeTopo bipartition simplified away). **Causal Monitor layout:** graph now full-width (`.cm2` column;
3D at 460px; floor auto-height ≈540px at app width), **ROOT CAUSE + verdict moved BELOW the graph** as its own
box (`.rootcard.verdict` grid: narrative left · evidence chips + horizontal blast list right). Dev mock upgraded
to the PS1 rail-A story (press-1 root, 3 rail edges + 1 loop edge) so the overlay is design-reviewable. Verified:
preview both planes (11 cuboids, 4 flows, toggle, verdict box) + `next build` ✓ 4/4. Preview gotcha for the
record: the infinite dash animation starves the screenshot tool's stable-frame heuristic — inject
`animation-play-state: paused` before capturing. **PLC entrypoint root cause (from operator's pod log):** upload
succeeded but OpenPLC stores programs under a GENERATED st_files name; the entrypoint compiled the SUBMITTED name
→ FileNotFoundError → runtime never got the program (Modbus up, logic inert — the "closed-loop but no trips"
state). Fix: scrape the generated name from the upload response's hidden `prog_file` field + belt-and-suspenders
cp under both names; `bash -n` ✓; openplc image rebuild is fast (source layers cached). Deploy block handed over:
rebuild dashboard + api (the LOG-035 qa fix rides along — still pending on the box) + openplc, restart, then
memory wipe + soak T0.

**LOG-037 · 2026-07-04 · 2G floor = TRUE 3D (operator reference render) — QUEUED, not built; OpenPLC doc added.**
Operator clarified the floor's end-state with a reference image (assembly-hall isometric: room shell, lane-marked
slab, boxy white/blue machines, stack lights, ~30–40° camera) — "along the lines of this, but do NOT act on it
now; current version is adequate." Recorded as the 2G refinement in the master plan (build only on explicit go;
carry-overs pinned: causal-on-fixed-wires, 90°/45° routing, wide gaps, verdict below, nothing floats). Task also
placed on the session TO-DO list. Meanwhile `plc/OPENPLC.md` added: what OpenPLC is (scan-cycle runtime + MatIEC
+ web UI), the Modbus mapping (%QX coils 0+, %MW at holding offset 1024), our 2F trip loop (sim = field wiring,
latched trips, condition-gated reset, fail-open honesty), deployment + the is-it-actually-running ops crib.

**LOG-038 · 2026-07-04 · TRUE-3D floor SHIPPED (operator go) + physics VERIFIED with a 10-test invariant suite.**
(1) **Physics check (operator: "check the physics"):** full review of `plant/sim/main.py` — model sound; the live
box numbers independently confirm calibration on four points (rail-A idle 361 V computed vs 360.3 observed; rail-B
duty dip; press-1/press-2/cnc-1 steady temps within 0.4 °C of closed form). New `plant/tests/test_physics.py`
(10 tests, deterministic stepping, no HTTP): Ohm's-law sag + bounds · rail isolation · PS1 cascade EMERGES
(friction→amps→sag→V-sensitive victims degrade, rail-B bystander untouched) · thermal steady = LOG-032 calibration
· first-order lag can't overshoot · PS5 flow drop + temps CROSS trip bounded (86.8/101.7 °C closed forms) · trip
consequences (amps≈0, work stops, cooling, RAIL RECOVERS) · PS2 duty-cycle vs stuck-on · reset returns to steady ·
hard bounds over a mixed run. **10/10 green; correlation suite still 50/50.** One documented FINDING (not a bug):
rail A idles at 0.90·Vsrc BY DESIGN, inside the 0.92 brownout band ⇒ V-sensitive machines (cnc-1, qa-scanner-1)
idle at ~89–90 % throughput, never 100; PS1's mark is the DELTA below that — now pinned in the tests with a NOTE.
(2) **3D floor (2G, task #1 done):** `Floor.jsx` rewritten as a plain-three.js orthographic assembly hall per the
reference — TWO production lines (one per rail; row A back, row B front), room shell (slab, lane lines, back/left
walls, emissive window strips), LOD-1 boxy machines with STACK LIGHTS (teal/amber/red = live status; tripped =
darkened body + ⌀ OPEN), PSU cabinets + risers, overhead bus bars (BUS_Y=122) with 90° drops, coolant TRENCH
between the lines with 90° stubs + pump, SpriteText labels (names + live A/°C, rail V, loop L/min). Static scene
rebuilds only on topology-signature change; live state (status/emissive/label text) applied per poll; **causal
overlay = axis-aligned conduit segments ON the fixed wiring + pulse spheres marching src→dst** (evidence-weighted
contention ramp, same as Graph.jsx); gentle drag-yaw orbit + wheel zoom (manual, no OrbitControls dep). SVG interim
kept UNIMPORTED as `Floor2D.jsx` (one-line-swap fallback documented in its header). Same `<Floor plant graph />`
interface — page.jsx untouched except nothing; `.cm-graph.isfloor` fixed at 540 px. Verified: preview (hall renders,
PS1 mock story on the conduits, EDGE toggle + clean unmount, only the known dev-only forwardRef warning) +
`next build` ✓ 4/4. Deploy = dashboard image rebuild + import + restart.

**LOG-039 · 2026-07-04 · Umbrella repo → GitHub (`GreaseMonkeyIT/Tata_InnoVent`); .gitignore authored; embedded
.git removed.** Operator pushes the whole `Tata InnoVent/` folder (proto + INNOVENT docs + RULES) as one repo.
Root `.gitignore` excludes: machine-local config (`.claude/`, `.obsidian/`), Syncthing markers (`.stfolder/`,
`.stversions/`, conflict files), node/next build artifacts (`node_modules/`, `.next/`, `out/`), python caches,
OS noise, and the scratch `ABB_Accelerator_Proto/output.txt`. **Fonts decision:** the IndustryTest trial OTFs are
COMMITTED so a fresh clone can build the dashboard image (next/font/local hard-requires them) — on the assumption
the repo stays team-private; the ignore line to flip is in the file, licensed swap first if it ever goes public.
**`ABB_Accelerator_Proto/.git` (the degraded mark-one clone's) must be removed before `git add`** — an embedded
repo would push as a broken gitlink, not files. Removal converts the synced clone to plain files and Syncthing
mirrors the deletion to the box copy; acceptable: canonical history = `ABB_Accelerator_Codex` + the
`GreaseMonkeyIT/ABB_Accelerator_Proto` remote (LOG-013), and no box runbook uses git in that dir. No file >5 MB
outside ignored dirs (deck included); `RULES/` carries no `.git`.

**LOG-040 · 2026-07-04 · Root README added + dashboard performance pass (no features cut; RULES-surgical).**
README.md: what VISR is (two-plane honesty line), repo layout table, run-it pointers (PIVOT_SETUP, physics tests,
engine fixtures, PS-scenarios), timeline. **Perf pass — the lag diagnosis:** (a) the 3D floor rendered 60 fps
forever, even static and even scrolled off-screen, and `applyLive` ran per FRAME instead of per data poll;
(b) the EDGE force graph keeps its own rAF alive off-screen; (c) all six Grafana d-solo iframes instantiated
eagerly at page load, contending with scroll. **Fixes (4 files, ~50 lines):** Floor.jsx — render-on-demand
(`R.dirty` set by data polls / camera / pulses; steady floor = zero GPU) + IntersectionObserver visibility gate
(off-screen = no work at all, repaint-once on return) + applyLive moved to plant/graph identity change; Graph.jsx —
IntersectionObserver calls the force-graph's own `pauseAnimation()`/`resumeAnimation()` when the panel leaves/
enters the viewport; page.jsx + Machines.jsx — `loading="lazy"` on all 6 Grafana iframes (off-screen embeds don't
even instantiate until approached — first paint fast, scroll uncontended). Verified: preview (canvas survives
scroll round-trip, WebGL context intact, 6/6 iframes lazy, zero console errors) + `next build` ✓ 4/4. Known
capture quirk stands: continuous pulse animation starves the screenshot tool's stable-frame heuristic — cosmetic,
tool-side only. Deploy = dashboard rebuild + import + restart.

**LOG-041 · 2026-07-05 · Scenario console: "Reset plant" button + button state wired to the sim's real
faults; plant `/reset` now restores a record-ready baseline.** The console error path left no recovery: firing
a scenario set the row `live` optimistically (client-only), so after a trigger errored and the page reloaded, an
active fault showed "idle / Fire" with no Reset button while the sim still held it. Fix (dashboard/app/page.jsx):
drop optimistic `live`; derive each row's live/idle + Reset/Fire from `/api/plant`'s `active_faults` (survives
reload) plus a `pending` map for in-flight busy state; add an always-available **Reset plant** header button
(`resetAll()` → the sim's global `/reset`, clears every fault + pulses the PLC trip reset) for stuck/unknown
states; re-pull `/api/plant` immediately after any action so buttons flip without the 5s tick; honest latency
copy (floor ~5s, verdict ~10–15s — the stale "~50s" removed). globals.css: `.resetall` + `.btn:disabled`.
**Plant latch fix (plant/sim/main.py):** `/reset` now also sets every device `throughput=100.0, tripped=False`.
Why: press-1/press-2 are not `v_sensitive`, so their throughput is set only at init and climbs only in the
healthy-voltage branch (`v ≥ 0.92·400 = 368 V`); steady rail-A sits ~361 V (r_src 0.35), so once a trip knocks
throughput down it is latched forever — clearing friction restored draw but not throughput. Verified in dev
preview. Deploy = dashboard rebuild + import + restart; plant-sim rebuild (`docker build -t skn/plant-sim:v0.1
plant`) + `k3s ctr images import` + `kubectl -n plant rollout restart deploy/plant-sim`.

**LOG-042 · 2026-07-05 · Narrator (gemma4) made plane-aware; coolant-trip forecast no longer renders as a
memory OOM.** api/main.py: (1) `_incipient_text` is now class/signal-aware — a `trip`/`coolant_temp` finding
reads "coolant temperature climbing toward the 78 °C trip (… °C now) — projected trip in ~Ns" instead of the
hardcoded "…B of 78 B — OOM" (the memory/bytes template was mislabeling the PS5 thermal forecast, since both
families share `incipient_findings`); the `leak`/`mem` path is unchanged. (2) The gemma4 prompt is plane-aware:
plant signals (`bus_voltage`/`coolant_temp`) frame the domain as an industrial plant floor with machines as
SOURCE/VICTIMS and forbid naming memory/CPU/I/O; `psi_*` keep the Kubernetes-node/pods framing. (3)
`_template_narrative` now emits the resource word (`bus_voltage` → "rail voltage") instead of leaking the raw
engine signal name. Deterministic paths (template + forecast line) are correct regardless of the model. Verified:
syntax + trip-vs-leak phrasing test. Deploy = api rebuild + import + restart (also clears `_NARR_CACHE`). Note:
the Jan-demo recording predates this — its on-screen forecast still reads "OOM"; the burned subtitles narrate it
correctly as a coolant trip.

**LOG-043 · 2026-07-05 · Repo flattened + root commit rewritten; two-working-copy workflow adopted.** The
umbrella repo (LOG-039) shipped one commit with `ABB_Accelerator_Proto/` nested, plus `RULES/`, the SiliconKnights
deck/pdf, and several ABB docs. Restructured to a flat layout: `ABB_Accelerator_Proto/*` hoisted to the repo root;
dropped `RULES/`, `SiliconKnights_Final.*`, and the redundant ABB docs (BOOK/MASTER_PLAN/BUILD_LOG/etc.); fixed the
now-stale ignore path (`ABB_Accelerator_Proto/output.txt` → `output.txt`) so the scratch file stays untracked;
de-nested the README run-it paths. Root commit `53d5776` → `6670af2` (`git commit --amend`), force-pushed to
`origin/main` (solo repo). **Workflow going forward (operator instruction):** edit in `…/Tata InnoVent` (the live
copy — `next dev` + `make images` build from here, nested `ABB_Accelerator_Proto/` layout), then mirror touched
files into `…/Tata_InnoVent_Commit` (the canonical flat git repo; strip the `ABB_Accelerator_Proto/` prefix) and
commit from there.

**LOG-044 · 2026-07-05 · Registration assets: demo video subtitled + reframed 16:9; deck re-themed red→blue.**
**Video:** `SiliconKnights_Tata_Final.mp4` (1892×944 ≈ 2:1, 3:43, post-factory-fixes / pre-gemma-fix) → parked at
the top of a 1920×1080 canvas so the 2:1 slack becomes a bottom subtitle bar; 25 narration cues (steady tour →
fire PS1 → cascade → root press-1 → coolant-trip forecast → trip) burned into the bar via a PlayResY-1080 `.ass`,
audio dropped → `SiliconKnights_Tata_Subtitled_16x9.mp4`. Spot-checked at fire/root/trip beats. **Deck:**
`SiliconKnights_PPT.pptx` (16 slides) re-themed Red+Black+White → Blue+Black+White: `theme` accent1/hlink
`CC0000`→`1D4ED8`, accent2/folHlink `8B0000`→`1E3A8A` (cascades to every `schemeClr`), and ~30 hardcoded red
shades collapsed to a consistent blue scale (`1D4ED8`/`2563EB`/`3B82F6`/`60A5FA`/`93C5FD`, light tints
`EFF6FF`/`DBEAFE`/`BFDBFE`, darks `1E3A8A`/`172554`) across all slides + presProps; green (healthy) / amber
(warning) status semantics and black/white structure preserved. Verified: zero red-family `srgbClr` remain; pack
validations passed → `SiliconKnights_PPT_blue.pptx` (original preserved). No LibreOffice locally → visual QA on
operator's PowerPoint.

**LOG-045 · 2026-07-05 · Deck: fuller palette (green→sky/cyan, warm→canary yellow) + content adaptation begun.**
After the red→blue pass (LOG-044) the surviving green (healthy) and amber (warning) semantics clashed with the new
blue; per operator, remapped the whole deck to one cohesive system: **royal-blue = root/danger** (was red),
**sky+cyan = healthy** (greens `1A6B3C`/`16A34A`/`009900`/`00FF00`… → `0369A1`/`0EA5E9`/`38BDF8`/`22D3EE`… by
luminance), **canary yellow = warning** (ambers/browns `7B3F00`/`CC4400`/`D29922`/`F0A800`/`FF6B35`… →
`854D0E`/`CA8A04`/`EAB308`/`FACC15`/`FDE047`); grays/black/white kept. 199 replacements; verified zero green/warm
`srgbClr` remain. **Content pass started** (adapt, not rewrite — reuse every box/font/size): formal problem
statement drafted in ABB-theme style ("Beyond Monitoring: On-Edge Causal AI for Industrial Systems", §3.2.2.5).
Slide 1 rewritten — "Theme 2:" → "Tata InnoVent 2026 · 3.2.2.5", theme line → "Beyond monitoring — on-edge causal
AI for connected, secure & intelligent industrial systems" (Poppins/sizes untouched); team block kept. Workflow:
preview each slide's copy → operator approves → write. **Resume point (morning):** slide 3 = "(Our Solution)" →
"VISR (ours)" (comparison table otherwise intact), then slides 4–16 (architecture L0–L3, PS0/1/2 scenarios, risk,
close); slide 2 is a text-free hero (skip). Engine-capture commands handed over (fire PS via api `:30088` →
`/api/graph` + `/api/narrative` + `/api/plant` JSON) to generate the scenario-slide visuals — needs api
redeployed first. Deck = `SiliconKnights_PPT_blue.pptx`; visual QA pending on operator's PowerPoint.

**LOG-046 · 2026-07-05 · Deck REBUILT — VISR dark theme + 13-slide restructure (`SiliconKnights_Tata_VISR.pptx`;
supersedes the LOG-044/045 blue deck as the working copy; originals preserved).** Morning box session first:
3 stale images deployed (api/dashboard/plant-sim — LOG-035/036/038/040/041/042 debt cleared), PS1 re-verified on
the engine side via `kubectl get --raw` service-proxy reads (run #1 root=compressor-1 0.36 = young-baseline + ON-window
+ warm-up transient, honest artifacts; run #2 after settle+OFF-window = **PASS: root=press-1 0.33, confidence 1.00,
[write,rail,temporal], zero pvc, qa-scanner-1 unmangled**) — operator captured screenshots. Then the deck: operator
approved the slide-05 VISR-dark prototype and ordered full propagation with a NEW structure (problem statement
invented from §3.2.2.5; ONE scenario only; simplified text, technical detail preserved). **Method answer recorded:**
the deck is a Google-Slides export (zero theme inheritance, per-shape hardcoded colors) — regex surgery replaced by
**python-pptx + a single token module (`visr_kit.py`, mirrors dashboard globals.css) + generated slides**; context-aware
XML transform (`retheme2.py`, TEXT/LINE/FILL 3-way maps) only for the 5 keepers. **Final 13:** title · 02 research
(4 tool families, where each stops) · 03 core insight ("everyone detects, nobody explains" + USP band) · 04 contentions
TABLE (rail/coolant/air/fieldbus/edge-box = the §3.2.2.5 problem statement) · 05 architecture (kept, tech stack
retexted pivot-truthful: plant physics L0, kernel PSI L1, why-cards → simulate-the-plant / watch-the-watcher /
normalization / deterministic-inference / declared-coupling) · 06 L0 physics (Ohm's-law sag, thermal lags, model-only
faults, 10-test suite + machines-by-task colored tiles per operator) · 07 L0 edge runtime (K3s reduced-scope + two-plane
honesty band) · 08 L1+L2 (kept; Loki column → Kernel truth PSI; "one window, two planes") · 09 L3 pipeline (kept incl.
screenshots; steps retexted to witness-gated flow; NLP quote → the LIVE 2026-07-05 press-1 verdict) · 10 L3 agents
(kept, light touch) · 11 PS1 scenario (6-beat timeline → trip; measured verdict panel w/ evidence chips; capture
placeholder) · 12 risk analysis (6 risks, AI-safety framed: spokesperson-only LLM, gates-never-loosen, two-plane,
human-confirm, clock budget, 62443-aligned) · 13 Stage-2/3 outcomes (per master plan, PLANNED-labeled, VISR-OS north
star). Footers → cyan "Tata InnoVent" chip, pages renumbered; browns/whites eliminated; contrast audit = only chip
labels dark-on-cyan by design. **QA = programmatic only (no local LibreOffice)** — operator's PowerPoint is visual QA.
Open: slide-11 placeholder wants today's PS1 capture; slide-9 embedded screenshots are old-factory-era (re-shoot in
2D-era); title-slide hero image unreviewed on dark; Bahnschrift headers (Windows-shipped) + Poppins body + Consolas
micro-labels.

**LOG-047 · 2026-07-05 · Deck restructure per teammate feedback — working file now `SiliconKnights_Tata_VISR.pptx`
(operator renamed off the `_ft. diddy` joke name; bak = `_bak.pptx`).** Two teammate notes: (1) L0 too k8s-heavy, the
physics values + how the engine reads them aren't explained; (2) early slides beat around the bush — a cold reader
can't tell what we solve; add lag-correlation / causal-inference explanation. Executed 3 changes via python-pptx +
`visr_kit.py` (the token module) + `build_v2.py`, all on the operator's hand-edited canonical (read CURRENT text first
— operator had retitled s2→"What do the floors run today?", added images to s3/s6/s11): (a) **slide 7 text-only
revamp** (no geometry touched, per instruction) → "L0 · The plant substrate — and the honesty rule"; K3s demoted to
"THE BACKDROP · K3S RUNTIME"; PLANE 1 now carries real physics VALUES (V=400−I·R, R=0.35Ω; loop 120 L/min, 78°C trip;
8 machines emit bus_voltage/current/coolant_temp/throughput; faults=params, PS1 friction ×1.9); PLANE 2 = HOW the
engine reads it (5s vectors, inverted bus_voltage, EWMA+CUSUM changepoints, declared-medium lag correlation, peak-lag
+role→direction, coolant forecast); honesty band reworded to the domain rule. Headers shortened back to safe 1-line
lengths after a wrap-risk catch. (b) **slide 10 REBUILT** — the dense 4-agent "what it does" boxes replaced by a
VISR-native "L3 · How a verdict is computed": 6 steps (signal vectors → changepoints → lagged correlation r(τ) →
**the witness gate** [orange highlight: no declared domain → no edge] → direction+rank → forecast+narrate) + a band
DEFINING causal inference ("not 'A correlates with B'…"). (c) **NEW problem-statement slide inserted at position 2**
("The problem we're solving", §3.2.2.5, lead band + 3 cards CONNECTED/INTELLIGENT/SECURE mapped to the coupling
problem / explanation gap / edge constraint + gap-statement band). Deck 13→**14 slides**; reordered so 10=L3 pipeline,
11=L3 how; **whole deck renumbered** (chips 02–14 + footer pages) since the insert shifted everything. QA programmatic
(no local LibreOffice): order/chip/page all correct, contrast audit clean (only chip labels dark-on-cyan by design),
slide-8 header colors survived the run-rewrite. **Fit-risk to eyeball in PowerPoint** (my blind spot): slide-8 PLANE 2
body (~7 lines in a 186px box), slide-2 card bodies, slide-11 step cards + definition band.

**LOG-048 · 2026-07-05 · Two final deck fixes (`build_v3.py`; bak2 saved).** (1) **Slide 2 SECURE card** rewritten to
name the security posture: air-gapped + **IEC 62443-aligned**, **per-device cryptographic keys gating every read/write**
(no unauthorized access or rogue command to the floor), motivated by a live citation — **Tata Electronics' own June
2026 breach** (World Leaks dumped 630 GB / 200k+ files incl. iPhone-18-Pro designs + Apple/Tesla data; citing Tata's own
subsidiary in a Tata pitch is deliberate). (2) **Slide 8 REBUILT physics-majority** (operator: the k3s panel still
dominated half the slide even as "backdrop"): now "L0 · The physics of a fault" — two big panels ELECTRICAL·THE RAIL
SAGS (friction ×1.9, ~43→85 A, V=400−I·R sags 361→344 V) + THERMAL·THE LOOP TRIPS (120 L/min, τ=45–90 s, 78 °C latch,
rail recovers) = the visual majority; a HOW IT REACHES THE ENGINE panel; k3s demoted to one thin note line; honesty
band kept. Slides 7 (model + machines) and 8 (fault propagation + engine intake) now split L0 cleanly. Deck still 14;
QA clean. Also shipped `slide11_lagcorr.png` (coolant-loop lag-correlation explainer, text baked under the graph).

**LOG-049 · 2026-07-05 · Deck data-viz assets + registration form answers + GitHub sync (commit `000e65d`) +
box pause/resume procedure for the Oct gap.** Working deck = `SiliconKnights_Tata_VISR.pptx` (14-slide VISR-dark;
backups `_bak`, `_bak2`; operator places images manually — no local LibreOffice to render).
**Deck images shipped (live-folder root, matplotlib in deck palette):** (1) `slide03_cascade.png` — the PS1 shared-
media cascade stepped headless from `plant/sim/main.py` (press-1 draw +98% ~43→85 A, rail-A sag, cnc-1/qa-scanner
throughput slide); real physics, not mock. (2) `slide03_carousel.png` — 4 REAL CC-licensed photos (Tesla line /
ABB switchgear / Drax cooling towers / CCTV wall = THE FLOOR / SHARED POWER / SHARED COOLANT / ALARM FLOOD), VISR-dark
filmstrip; **attribution required** (CC BY / BY-SA), credits in `slide03_photo_CREDITS.txt`; source jpgs `slide03_photo_*`.
(3) `slide11_lagcorr.png` — lag-correlation teaching chart (illustrative Gaussians grounded in real thermal-τ ordering;
explainer text under the graph). **Registration form answers drafted** (portal wants plain ASCII, no em-dashes;
validated): problem statement (~2930 ch), solution/how-it-works (the **witness gate** = the innovation), technologies
(K3s · Prometheus · eBPF/Caretta · Linux PSI · TimescaleDB · Go aggregator · Python+NumPy engine w/ EWMA+CUSUM+Pearson
lagged cross-correlation · Ollama+Gemma narration · FastAPI · Next.js/three.js · OpenPLC+Modbus/pymodbus · Docker),
impact/quantified (1 root cause vs ~a dozen alarms · 5 s cadence · ~6 GB model · 100% on-edge/offline · 50 corr
fixtures + 10 physics tests), originality (own engine, an evolution of the team's **ABB Accelerator 2026 finalist**
build — disclosed; witness gate unique; benchmarked vs SCADA/historians/PdM/AIOps), prior-submission (ABB Accelerator
disclosed). **Determinism framing corrected** (operator Q): verdict = f(input, baseline STATE, config) — deterministic
but STATEFUL; the engine is a statistical-inference-plus-rules PIPELINE, not one trained model; pitch "transparent +
reproducible + few interpretable params + cited evidence", not just "deterministic" (a neural net is deterministic at
inference too). SECURE (slide 2 + form) cites IEC 62443 + per-device crypto keys + the **June 2026 Tata Electronics
breach** (World Leaks, 630 GB Apple/Tesla data — honest nuance: it was exfiltration/extortion, argues the access-
control+audit half more than "rogue command"). **GitHub:** commit `000e65d` "VISR: api/dashboard/plant-sim fixes +
INNOVENT_LOG through LOG-048" pushed to `origin/main` (GreaseMonkeyIT/Tata_InnoVent); repo in sync except `.gitignore`
/`README.md` (by-design flat-vs-nested — leave them); deck + slide images are NOT tracked in the code repo (dropped
LOG-043), live-folder only. **NB: this LOG-049 entry postdates `000e65d`, so the commit repo is one entry behind —
next sync = `cp` live `INNOVENT_LOG.md` into `Tata_InnoVent_Commit/`, then `git add INNOVENT_LOG.md && git commit && git
push` from that repo.** **Box pause/resume for the Oct-2026 gap** (operator won't keep the remote PC on a workload for
months): STOP = `sudo /usr/local/bin/k3s-killall.sh` (+ `sudo systemctl disable k3s` to stop boot auto-start) — halts
every pod, frees CPU/RAM/disk, nothing deleted, claimRef PVs persist; lighter alt = scale `plant`+`aiops` deployments to
0. RESUME = `sudo systemctl enable --now k3s`, wait ~90 s, pods recover from the datastore. Caveats: (a) the engine
re-learns baselines after ANY restart — bring the stack up well before the demo and run the LOG-035 procedure (wipe
memory → long PS0 soak → fire PS1 in a compressor-OFF window); never fire cold; (b) stopping also protects the 64Gi
disk from historian/Prometheus growth over months. Full PC power-off needs Wake-on-LAN (a LAN device on the Tailscale
mesh) or a smart plug + BIOS restore-on-AC for remote power-on.

**LOG-050 · 2026-07-17 · Registration CONFIRMED submitted (Jul 5, on time) — the gate is CLOSED; post-registration
state pinned; commit repo resynced.** Operator confirms the Tata InnoVent registration went in on 2026-07-05: 14-slide
VISR deck (+PDF), subtitled 16:9 demo video, drafted form answers. Next milestone = **Virtual PoC, Oct 2026.** Deck
leftovers (slide-11 PS1 capture, slide-9 old-factory-era screenshots, hero-on-dark review) die with submission — they
matter again only if the deck is revised for the PoC; same for the licensed Industry font swap. Sidebar for the
record: the SASH fellowship application (separate project, `Linux_Projects\SASH\`) was NOT submitted — college
refused the residential semester leave; STATUS.md there marked closed; no InnoVent impact. **Open items are now ALL
box-side and deferred to Oct PoC prep:** (1) pause the stack for the gap (LOG-049 procedure — k3s-killall + disable;
protects the 64Gi disk from historian/Prometheus growth); (2) PS5 acceptance run; (3) api-pod mem-creep watch;
(4) re-confirm OpenPLC trip latching post-entrypoint-fix (LOG-036); (5) LOG-035 re-soak before any demo — never fire
PS1 cold. Box untouched this session. Commit repo resync: live `INNOVENT_LOG.md` (through LOG-050) copied to
`Tata_InnoVent_Commit/` and pushed.

**LOG-051 · 2026-07-17 · Boot section SHIPPED (2D-1) — the last unbuilt VISR section; October prep begun (local,
box untouched).** New `dashboard/app/Boot.jsx`: full-screen self-check overlay on every load — **5 REAL probes**
(`/api/health` services · `/api/pod-resources` prometheus+series · `/api/pods` workloads seen · `/api/graph`
pods+accepted edges · `/api/plant` assets+rails, labeled "physics-simulated") each showing its measured round-trip;
**no fake progress bars** (master-plan rule — nothing renders that didn't happen). Behavior: click anywhere = skip
(checks abort); all-pass → "all systems nominal" → auto-enter after 1.2 s; any failure → holds with an ENTER button
+ "dashboard will show the degraded state" (the dashboard already surfaces down states honestly). **`?boot=hold`**
keeps the screen up after a clean pass — design review + pacing the PoC-recording opener (2D-4 wants the video to
open on boot/login). Style = restrained HUD: TL/BR corner brackets, Industry-face VISR wordmark (cyan, 8px tracking),
mono check rows, teal OK / red FAIL, tokens straight from globals.css. `getJSON` is passed in from page.jsx so the
dev mock keeps it reviewable in `next dev`; prod = live probes only. Verified: dev preview — 5/5 OK with real
latencies, correct details, tokens/centering confirmed via computed styles, click-dismiss + auto-enter both
exercised (NB: the Browser pane's screenshot tool timed out ALL session, even pre-Boot — verification was
text-based: read_page + computed-style probes) — and `next build` ✓ 4/4. Deploy = dashboard image rebuild + import
+ restart; box is paused, so this rides with the next box session (add to the resume checklist).

**LOG-052 · 2026-07-17 · 2A truth pass, LOCAL HALF SHIPPED — fixtures-first; memory can no longer out-vote live
evidence; young-baseline storms surface marked; S2 stress goes synchronous.** October-prep framework order pinned:
2A → 2E → 2D remainder (2C is superseded by the shipped 2C′ plant families — decision to be logged with 2D; 2F tag
server is post-2E). **The three 2A rules, tests written FIRST and confirmed failing for the right reason:**
(1) **Backbone fix** — the S2 confident-wrong-root reproduced in BOTH ranking sites: `state._render` and
`merge.merge_graphs` let a held (memory) edge vote for root whenever findings existed, so a remembered coupling
(cm→tsdb) beat the invisible true writer when only the victim deviated. Rule now: a memory-sourced edge RENDERS
(backbone stays visible) but only VOTES when its src is a current finding; live edges always vote. Counter-cases
pinned so memory stays valuable: source deviating ⇒ held edge re-attributes immediately. (2) **Young-baseline
door** (`pipeline.run_pass`): immature baseline (thr None) was a blanket skip — the true culprit couldn't compete.
Now an UNAMBIGUOUS storm (|zpeak| ≥ `YOUNG_Z` = 8.0, vs the 3.0 finding floor) enters findings marked
`young_baseline: True`; anything weaker stays silent (PS0/S0 warm-up quiet is preserved — the door is narrow by
design). The old pin (`baselines=None ⇒ findings == []`, test_engine ~L227) encoded exactly the behavior 2A
changes — deliberately rewritten. (3) **S2 goes synchronous** (`workloads/log-archiver/archive.sh`):
`--ioengine=libaio → psync` — async queueing let the archiver flood the disk without waiting, so its OWN psi_io
barely moved; psync blocks every write(2) ⇒ it visibly self-stalls ⇒ detectable finding + write-evidenced source
edge. `bash -n` ✓. **Suite: 55/55 green** (5 new fixtures; one flake caught + fixed in MY test: the module-shared
`rng` is consumed sequentially, so a new test drawing from it shifts noise under later tests — new tests use a
private generator; also learned: EWMA decays the residual before the CUSUM alarm, so zpeak-at-alarm ≪ raw step —
calibration self-asserts baked into the fixture). **Box-verify checklist for the next box session:** rebuild
correlation-engine + log-archiver images, then fresh-run S0 silent · S1 roots cooling-monitor · S5 forecast · S2
now roots log-archiver (twice, per the plan's two-attempt honesty rule) + PS0/PS1 regression (plant families ride
the same ranking path). Files: engine/{pipeline,state,merge}.py, tests/{test_engine,test_state,test_merge}.py,
workloads/log-archiver/archive.sh. Mirrored to `Tata_InnoVent_Commit` working tree; commit/push withheld per
operator instruction (explicit command only).

**LOG-053 · 2026-07-17 · 2E secure pass, LOCAL HALF SHIPPED — operator-gated actions, hash-chained audit ledger
(+ dashboard Audit section), TLS+login front door prepared.** Deliberately boring per the plan (auth bugs are demo
killers): ONE shared operator token + nginx basic auth, no hand-rolled login. **(1) `api/security.py` (NEW,
stdlib-only ⇒ unit-testable without FastAPI):** `token_ok` (constant-time; empty/unset expected token = auth
DISABLED — safe rollout) + `AuditLedger` — append-only JSONL, every entry `{ts,actor,verb,target,status,evidence,prev,
hash}`, hash = sha256(prev + canonical(body)) ⇒ edit/delete/splice breaks `verify()` AT that entry; tail hash
recovered on open so the chain survives restarts. **8/8 tests green** (`api/tests/test_security.py`, incl. edited-
entry and deleted-entry tamper cases). This ledger is the same one the Stage-3 act loop writes into. **(2)
api/main.py wired:** POST trigger/reset now `_require_operator` (X-Auth-Token or Bearer; 401 + a "denied" audit
entry on failure — the ledger records who KNOCKED); every fire/reset audited with a best-effort verdict snapshot
(root/score/evidence chips = what the operator saw when they acted); `GET /api/audit` (entries + chain_ok re-derived
on read); `/api/health` gains `auth: enforced|disabled` (an open deployment can't pass as secured). `py_compile` ✓.
**(3) Front door:** `dashboard/nginx.conf` → **`nginx.conf.template`** (official envsubst mechanism — token never
baked into the image): 80 = 301→https:30443 only; 443 TLS + basic auth (`viewer` looks, `operator` acts); `map
$remote_user` injects X-Auth-Token ONLY for operator (viewer's clicks get the API's honest 401) + X-Remote-User for
attribution. Dockerfile → templates dir, EXPOSE 80 443. **(4) Deploy:** dashboard.yaml mounts Secrets `visr-auth`
(htpasswd + operator-token) + `visr-tls`, adds nodePort **30443**, HTTPS readiness probe — **fail-closed** (create
Secrets FIRST); api.yaml takes VISR_OPERATOR_TOKEN with `optional:true` (Secret absent = disabled, honest) +
AUDIT_PATH on an emptyDir (PVC promotion = noted roadmap, single-node box). **(5) Dashboard "Audit" section** (last
panel): 30s poll, newest-first rows time·actor·VERB·target·status·evidence, denied rows red, chain-intact/BROKEN +
auth state in the meta; dev mock incl. a denied viewer attempt. Verified: dev preview (rows + meta correct, zero
console errors) + `next build` ✓ 4/4. **Deferred within 2E:** step 4 device tokens ride with 3A (no fan-in proxy
exists yet); grafana :30030 anonymous + api GET NodePort stay open inside the mesh = viewer-equivalent, said
honestly. **Box block (create Secrets, then rebuild dashboard+api images + apply + restart):**
```
sudo apt-get install -y apache2-utils   # htpasswd (once)
htpasswd -nbB viewer   '<viewer-pass>'   > /tmp/htpasswd
htpasswd -nbB operator '<operator-pass>' >> /tmp/htpasswd
TOKEN=$(openssl rand -hex 24)
openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=visr.local" \
  -keyout /tmp/tls.key -out /tmp/tls.crt
kubectl -n aiops create secret generic visr-auth \
  --from-file=htpasswd=/tmp/htpasswd --from-literal=operator-token="$TOKEN"
kubectl -n aiops create secret tls visr-tls --cert=/tmp/tls.crt --key=/tmp/tls.key
rm /tmp/htpasswd /tmp/tls.key /tmp/tls.crt
# then: rebuild skn/dashboard + skn/api images, k3s ctr images import, kubectl apply -f
# deploy/dashboard.yaml -f deploy/api.yaml, rollout restart both.
# done-when (2E): incognito browser -> login wall; `curl -k -X POST https://<node>:30443/api/scenarios/PS1/trigger`
# (no auth) -> 401; viewer login firing -> 401 + a `denied` audit row; operator fire -> row with evidence;
# `curl -s -X POST http://<node>:30088/api/scenarios/PS1/trigger` (direct NodePort, no token) -> 401.
```
Files: api/{security.py,main.py,pytest.ini,tests/test_security.py}, dashboard/{nginx.conf.template,Dockerfile,
app/page.jsx,app/globals.css} (nginx.conf REMOVED — superseded), deploy/{dashboard.yaml,api.yaml}. Mirrored to the
commit working tree; commit/push withheld.

**LOG-054 · 2026-07-17 · 2D remainder CLOSED (local) + DECISION: old 2C superseded by 2C′ — the up-to-2E framework
is laid.** (1) **2D-3 claimRef bake: already done** — `deploy/slowdisk.yaml` is the pivot edition (LOG-029) with
claimRef pre-bound on both PVs; the master-plan open item predates that rewrite. Verified, no change. (2) **2D-4
script: `POC_SCRIPT.md` (NEW, proto root)** — the full recording script to the done-when standard (teammate can
re-run it alone; box does it twice): prep day (resume→deploy pending images→LOG-035 soak→rehearse twice), then
cold-open on the 2E login + Boot self-check, PS0 calm two-plane tour, PS1 hero beat (fire in a compressor-OFF
window; floor ~5 s; verdict ~10–15 s; evidence-chip beat = the USP, never trimmed), audit row on camera, PS5
forecast→PLC trip→reset, close; honesty rails scripted verbatim (simulated-plant labeling, "spokesperson-only"
LLM, 62443-aligned-not-certified); pre-decided fallbacks (narrator down, LOG-046 young-baseline artifact, S-series
bench rollback); ~5–6 min budget. (3) **DECISION — old Phase 2C (db_query_latency domain correlation) is
SUPERSEDED, not skipped:** 2C targeted the retired factory workloads; the pivot's 2C′ (LOG-033, shipped + box-
verified LOG-046) delivers the same thesis — domain signals as first-class engine families behind declared-domain
witnesses — on plant physics (`bus_voltage`/`coolant_temp` + PLANT_SOURCES + rail/loop witness map). Nothing
db-latency-shaped returns unless a SCADA-historian-latency angle earns it in 2F; logged so nobody "finishes" 2C
later. **Framework state up to 2E: 2A ✓(local, LOG-052) · 2B′/2C′ ✓(shipped pre-registration) · 2D Boot ✓(LOG-051)
+ script ✓ + claimRef ✓ (font swap + the recording itself = operator/box) · 2E ✓(local, LOG-053). Remaining Stage-2:
2F tag server + tags UI (next local build), then ONE box session: deploy LOG-051..053 images + Secrets, 2A/2E
box-verify, PS5 acceptance, OpenPLC latch re-confirm, soak, record per POC_SCRIPT.md.** Mirrored; commit/push
withheld (operator's explicit command only).

**LOG-055 · 2026-07-17 · 2F remainder, LOCAL HALF SHIPPED — SCADA tag server (`scada/`, NEW) + tags UI; Stage 2 is
now fully code-complete locally.** Path realized: physics → OpenPLC %MW/%QX → Modbus → **tag server** → tag DB +
TimescaleDB historian + /metrics + /tags. **(1) `scada/tags.py`** (pure, **7/7 tests**): the tag DB as code — 41
tags (28 measured + 13 derived), ISA-style names (`PLANT.PRESS_1.AMPS`), units, PLC addresses, ×10/×100 scaling
per REGISTER_MAP; derived tags = SCADA calculated-tag practice with the formula shown in the address field
(machine VOLTS = its rail's; HEAT = k·I from the sim's calibration constants; TRIP_LIMIT const) — honest, since
those aren't PLC-readable; **quality GOOD→STALE(>10 s)→BAD(>30 s)** by age of last good read (`requality`); BAD
tags LEAVE /metrics (a gap, never a lie); `prom_text` emits series/labels IDENTICAL to the sim's exposition, and
`engine_parity_metrics()` pins the aggregator's queries.yaml set inside a test so a rename breaks loudly. **(2)
`scada/tagserver.py`:** READ-ONLY Modbus client (FC03 32-word block + FC01 coils, 1 s poll — never writes; the sim
stays the field wiring, the trip program the authority); lazy self-healing psycopg2 → `plant_tags` hypertable
(create_hypertable with plain-postgres fallback; **closes the PIVOT_SETUP §7 historian-ingest line**); rows/s +
rows_total; HTTP /metrics (+`scada_plc_connected`/`scada_historian_*` self-health), /tags, /healthz; PLC down ⇒
last map served, aging honestly. **(3) Register contract extended:** the sim now also writes **MW24–31 throughput
×10 as a SEPARATE FC16 write** — a single 32-word sweep would zero MW20 (reset_cmd) every tick and race the reset
pulse (caught in review); REGISTER_MAP.md updated (16–19/21–23 never written; tag server = read-only second
client). **(4) Deploy split = the cutover gate:** `scada/deploy.yaml` (Deployment+Service, ns plant, **NO
ServiceMonitor** — scraping sim AND tag server would duplicate every plant_* series and corrupt the engine's
vectors) + `scada/cutover-servicemonitor.yaml` (apply it, then delete the plant-sim ServiceMonitor, ONLY after box
stability: tag values match the sim's debug tap through a PS1+PS5 run; rollback = the reverse pair; Grafana
caveat: `plant_fault_active`/`plant_plc_connected` panels go empty post-cutover — sim-plane meta, check skn-plant
panels). **(5) UI:** api `GET /api/tags` proxy (SCADA_URL; honest "unavailable") + Machines gains the **SCADA
tag-browser strip** (scrollable grid, quality dots, derived dimmed, PLC+historian badge with rows/s; absent server
⇒ "tag server unreachable — telemetry is sim-direct") and **per-machine hover popovers** (pod-pop pattern: signal ·
value · address/formula). Verified: scada 7/7 · py_compile ✓ (tagserver/tags/sim/api) · dev preview (41 chips, 8
popovers, press-1 = TEMP/AMPS/THROUGHPUT/TRIP/VOLTS/HEAT with formulas, badge live, zero console errors) ·
`next build` ✓ 4/4. **Box-session additions:** build `skn/tag-server:v0.1`; rebuild plant-sim (MW24–31) + api +
dashboard; apply scada/deploy.yaml; stability-watch vs the sim tap; then the cutover pair; caretta should now show
sim⇄PLC⇄tag-server Modbus flows — say so on camera. **Everything left in Stage 2 is the ONE box session
(LOG-051..055 deploy + verify + soak + record per POC_SCRIPT.md).** Mirrored; commit/push withheld.

**LOG-056 · 2026-08-10 · CLAD/LabVIEW workshop Day 1 + the sponsor read; study pack written
(`LabVIEW_CLAD/`, NEW), no stack change.** Tata is running a **CLAD** (Certified LabVIEW Associate
Developer) workshop for InnoVent. **Why:** this edition launched 2026-06-04 with **Emerson's Test &
Measurement business (= the former NI: LabVIEW, TestStand, PXI, cRIO)** and AWS as partners, theme
"AI at the Edge"; Tata's own stated intent for the Emerson tie is taking student code "from virtual
simulations into rugged, real-world hardware environments", and the support package includes NI
tool access + jobs to top participants. Read: **sponsor enablement + a hiring credential + the
virtual-PoC→physical-finals arc**, i.e. an available advantage, NOT a stated requirement (confirm
with organizers before building for that reason alone). Day 1 covered data types, operations,
conditionals, loops, front-panel elements, arrays, clusters/bundles, **shift registers**, and
**loop dependency on CPU speed** — which is our own stack in another notation: `scada/tags.py`
tag_table = an array-of-clusters, quality GOOD/STALE/BAD = an enum, `plc/program.st` latch = a shift
register, `plant/sim/main.py` `loop()` = a While loop + shift-register Euler integrator + a
compensated wait (= Wait Until Next ms Multiple). **Written:** `LabVIEW_CLAD/{README.md (objective +
exam context + day log), DAY1_FUNDAMENTALS.md (syllabus + 20 CLAD traps + 5 practice VIs),
VISR_APPLICATION.md (concept map + PoC options)}`. **Finding logged (no fix applied):** the sim's
`dt, last = max(now - last, 1e-3), now` clamps only the LOW side; with `tau=45 s` a stalled tick
above ~90 s would make the Euler step diverge and manufacture a temperature excursion no fault
caused — cheap hardening later = clamp dt to a few ticks + count the clamps (the Python form of
Timed Loop's `Finished Late?`). **RECOMMENDATION, decision deferred to operator:** if we use
LabVIEW at all, **Option A only** — an additive read-only LabVIEW panel over the tag server's
`/tags` JSON (HTTP GET → Unflatten From JSON → array of Tag clusters), built against a RECORDED
JSON file first, zero stack change, cuttable from the recording; plus optionally a one-afternoon
single-machine thermal VI for the sponsor-alignment slide. **NO rewrite, NO second Modbus writer,
NO LabVIEW on the recording's critical path**, and nothing decided before the remaining box session.
Not mirrored to the commit tree (notes, not code) pending operator call.

**LOG-057 · 2026-09-14 · One folder: the live copy and the repo merged, pre-InnoVent legacy removed, GitHub up to date.**
Operator decisions: ONE `Tata InnoVent` folder for edits and commits. No pre-InnoVent legacy files. A normal new
commit, with no history rewrite. A full backup before the merge.
**Backup:** `Documents\Claude\Backups\Tata_InnoVent_pre-merge_2026-09-14\` holds a hash-verified copy of the live
folder (12,054 files). It also holds the whole old `Tata_InnoVent_Commit` folder (moved, not copied) and a
full-history git bundle.
**Merge, in order:**
(1) Delete the stale nested `.git`, `node_modules`, `.next`, `out`, and the Python caches while Syncthing still syncs them, so the box drops them too.
(2) Add a `.stignore` (`.git`, `node_modules`, `.next`, `out`, caches, archives).
(3) Move `ABB_Accelerator_Proto/*` to the root.
(4) Copy the canonical `.git` in from the commit folder.
`git status` then matched the 31 known changes, and all 172 repo files matched byte for byte.
**Commit `724d262`:** the LOG-052 to LOG-056 work (tests 55/10/8/7 green).
**This commit** removes the factory bench: `workloads/` (15 services), `deploy/charts/factory/`, and `scenarios/`
(S0 to S5 and the ledger). It also removes `QUICKSTART.md`, `agents/`, `archive/`, `appendix/`, `tools/`, and
`deploy/values/beyla.yaml`. The old accelerator docs, decks, and `output.txt` left the working folder too. Git
never tracked them.
**Fixes to everything that pointed at them:** `api/main.py` drops the S1/S2/S5 routes, its Kubernetes client, and
`COOLING_URL`. The Caretta topology now uses `TOPOLOGY_NAMESPACES` (default `plant,aiops`) instead of the `factory`
prefix. The plant Modbus flows can now appear, as the 2F done-when expects. `skctl` drops the factory groups and
pause/resume/down. Prometheus drops the `l0-fast` factory job. The `Makefile` builds and imports the seven VISR
images. `soak/` now cycles PS1/PS2/PS5 through the API and sends the operator token. Docs and code comments no
longer point at deleted documents. `INNOVENT_PLAN.md` now shows the current state. `PIVOT_SETUP.md` gains the 2E
Secrets step and the tag server.
**Consequences:** the 2A box-verify moves to the PS-series: PS0 silent, PS1 roots press-1, PS2 roots compressor-1,
PS5 forecast card. `POC_SCRIPT.md` drops the old bench fallback. A failing plant plane now means no recording.
**Verified locally:** tests 55/10/8/7 pass. A 17-check API smoke test passes in a scratch FastAPI venv. A soak run
against a mock API passes the token path, the refusal path, and the report render. `npm ci` and `next build` pass
(4/4), and the dev preview renders from the new path. Syncthing shows the flat tree on the home PC at 100%. No
`.git` and no build output entered the sync.
**Box block (operator, next box session):** the working path is now `~/Tata_InnoVent`. Run
`chmod +x deploy/skctl soak/*.sh plc/*.sh` after the sync. Confirm that `ls ~/Tata_InnoVent` shows no
`ABB_Accelerator_Proto/`. Earlier log entries stay unchanged (append-only).

**LOG-058 · 2026-09-15 · Phase 2H: the virtual PLC fleet and act loop verb 1, built and tested locally.**
**Operator decisions (2026-09-14 and 2026-09-15):** build the entirety of the remaining items plus a
capability to simulate virtual PLCs, load them with tasks, and see them come online. The judges said
the most complete product has the best chance (108 teams to about 10). The college lab PLCs (S7-200,
S7-1200, Micro820, in-charge approval received) wait until after the Virtual PoC. **The Stage 2 PPT
and the demo video are due 2026-09-26.** The box gets a local image registry so deploys need no sudo.
Leftover old-bench releases (beyla, factory) may be removed.
**Design:** `FLEET.md` is the interface contract. A virtual PLC is a soft-PLC runtime with a vendor
protocol profile, not vendor firmware. The protocols are real frames. plant-sim keeps all physics.
The base stamping cell (press-1, press-2) gets a process PLC, `plc-stamping` (S7-1200 profile).
OpenPLC stays the separate trip interlock. The stamping cell fails open, so the plant behaves as
before when its PLC is absent.
**Built:** `vplc/` (Structured Text subset compiler, measured scan loop, S7comm DB1 and Modbus TCP
servers, field port, control HTTP, signed enrollment, 4 tasks) · `plant/` cells, rail `psu-c`, field
wiring, `/cells`, `/domains` · `scada/` fleet registry, drivers, enrollment, quality, setpoint writes,
`/metrics/fleet` · `correlation/service.py` run-time domains (`DOMAIN_SOURCES`) · `api/` fleet routes
over a Role in namespace `fleet`, act loop (cite-or-die proposal, 409 on a changed verdict, relief
row after 60 s), narrator controller line · `dashboard/` Fleet section, Execute and ledger, drive rows,
N-rail floor with PLC cabinets, sixth Boot probe · `deploy/fleet.yaml`, registry manifests,
`make push`, PIVOT_SETUP steps 4, 5.0b, 5.3d.
**Resume blockers fixed on the way:** `api/Dockerfile` did not copy `security.py` (the new api image
would crash-loop). The dashboard readiness probe hit basic auth and got 401 (new `/healthz` path).
pymodbus was unpinned in the tag server. Dependencies are now pinned to the versions proven on the box.
**Verified locally:** tests correlation 60, plant 20, api 24, scada 59, vplc 41, all green. A
process-level smoke test ran a vPLC on the S7 profile and one on the Micro820 profile with plant-sim
and the tag server: 18 of 18 checks passed. Both PLCs enrolled and polled GOOD (RTT 0.27 ms over
S7comm, 1.95 ms over Modbus). A DERATE write of 55 cut press-1 from 42.9 A to 23.1 A. Task hot-load
and the compile-error refusal both worked. `next build` passes, and the dev preview renders the
Fleet cards, the six phases, the Execute dialog, and three rails.
**Box state:** registry on 127.0.0.1:5000 with all 8 images pushed. k3s still paused. Pending
operator sudo: `registries.yaml`, reboot, `systemctl enable --now k3s`, then the 2E Secrets.
**Next:** deploy, 2A/2E/2H box-verify, the overnight soak with `plc-stamping` running, rehearse,
record per `POC_SCRIPT.md`, then the deck on the official template.

**LOG-059 · 2026-09-15 · Session summary written (`HANDOFF.md`), go-live scripted, frontend change set for the next session.**
**Operator decisions:** the frontend changes in the next context window. Log and note down everything
in a summary now, then take everything live on forge over Tailscale.
**Summary:** `HANDOFF.md` records the dates (Stage 2 PPT and video due 2026-09-26), the operator
decisions, the Phase 2H build and its proof, the resume fixes, every box fact, the college lab facts,
the open items, the risks, and the process notes. The next session starts from it.
**Go-live, scripted:** `deploy/resume.sh` is the one operator step (sudo and the dashboard
passwords): the registry mirror file, k3s start, node wait, the 2E and 2H Secrets. `deploy/golive.sh`
needs no sudo: old-bench cleanup, every manifest in order, restarts onto the registry images, and
checks of the front door, the plant, OpenPLC, plc-stamping over S7comm, SCADA, the engine, and the
api. `PIVOT_SETUP.md` gains the "Resume after a pause" section.
**Box facts found:** a non-interactive SSH shell does not set `KUBECONFIG`, so kubectl reads the
root-only k3s config. User linger is off, so `systemd-run --user` jobs stop with the SSH session
(use `screen`). The registry holds all 8 images. k3s stays paused until the operator runs `resume.sh`.

**LOG-060 · 2026-09-15 · The stack is LIVE on forge: go-live 0 failures, fleet and OpenPLC latch verified on the box.**
**Sequence:** the operator ran `deploy/resume.sh` (registry mirror, k3s start, node Ready, the 2E and 2H
Secrets). It started `deploy/golive.sh`, which finished with **0 failed checks** in about 45 s: beyla
release and the chaos namespace removed, every manifest applied, all 8 Deployments rolled onto the
registry images, login wall 401, `/healthz` 200, http 301, api auth enforced, anonymous fire 401,
stamping cell closed-loop, OpenPLC closed-loop, tag server GOOD, plc-stamping enrolled over S7comm
with 23 of 23 tags GOOD, engine graph up, `/api/fleet` RUN, `/api/tags` from SCADA, Caretta topology.
**Checked after settling:** every pod Running (alloy CrashLoop is the known ignorable one). The
aggregator and openplc pods report `docker.io/skn/...` names, but their specs pull
`localhost:5000/skn/...` and the digests match: containerd shows the July alias of the same image.
Over Tailscale from the laptop: `https://100.93.123.48:30443/healthz` 200, `/` 401, `:30080` 301.
**2H live test (actor `claude-verify`, operator token from the Secret):** `POST /api/fleet/plcs`
created `plc-pack-1` (Micro820 profile, packaging-cell, rail psu-c). Phases: requested +0.0 s, pod
+0.2 s, runtime +1.2 s, enrolled +2.8 s, SCADA GOOD +5.9 s (Modbus RTT 7.3 ms, 32 of 32 tags),
engine window +15.6 s. The cell ran closed-loop, its three machines drew current, psu-c sat at 386.5 V.
Load task `packaging-cell-rush` answered 200. The tag server published `plc:plc-pack-1`. The audit
chain stayed intact. `plc-pack-1` stays running for the operator to see. **Remove it before the soak.**
**OpenPLC latch re-confirm (LOG-050 item 4, open since July): PASS.** PS5 fired. press-1 and furnace-1
reached 78 C in about 50 s. The forecast card led the trip (press-1 ETA 2.4 s, furnace-1 7.2 s,
press-2 27.5 s). The PLC set both trip coils (SCADA read 1.0), the contactors opened, and the trips
stayed latched for 20 s while the fault stayed active and the machines cooled. One reset cleared both.
**Not verified yet:** PS1 roots press-1 and the Execute beat (both need matured baselines), PS2,
PS0 silence. The engine is noisy after the restart on July baselines (root compressor-1, 20
findings). That is expected: wipe the engine memory and soak before any verdict counts.

**LOG-061 · 2026-09-15 · The college lab track is dropped.**
**Operator update:** the Mechatronics lab setup is too limiting (one Micro820 with one experiment
would read as weak). A plan to feed outside data into the plant plane followed. LOG-076 closed that
plan: the plant plane runs on the physics sim only.
**Guard added:** `.gitignore` gains `industry_data/` and `*.ttrecx`. The repo is public, and no data
file from outside the project enters git.
(Edited 2026-09-22, LOG-076: the data details are removed.)

**LOG-062 · 2026-09-16 · The dashboard becomes a single-screen operator console on the Stage 2 palette. Live on forge.**
**Operator brief (2026-09-15):** keep the infographics. (1) Adopt the Stage 2 deck palette (#0000B3,
#12C6B3, #FF9C00, #000000) sparsely, with color theory, and keep the calm VISR look. (2) Replace the
scrolling page: a SCADA-like system needs buttons, panels, and a map on one static screen, with EVE
Online and FTL as references, a few scrolling sub-panels, nothing obscured, and no sensory overload.
**Operator decisions:** build two layout prototypes first. The operator picked **A** (map in the
center, panels on both flanks) over B (wide map, bottom deck). Teal marks the normal state. The fonts
stay (Industry for display, the system sans for text). The Grafana graphs return through `/grafana/`.
**Found while planning:** since the HTTPS front door (LOG-060), the six Grafana iframes pointed at
`http://<box>:30030`. A browser blocks a plain-HTTP frame inside an HTTPS page as mixed content, so
those graphs were blank on the live console.
**Color roles:** grounds are near-black shades of the Tata blue hue (60/30/10). Teal is the normal
state and the accent. Blue fills operator commands, the brand plate, and the active tab only: on black
it has about 1.5:1 contrast, so it never carries text. Amber is warning. Red (#F2495C, kept) is alarm,
the 180° complement of the teal. Black marks live data wells. Every status also has a shape (● ▲ ■ ⌀).
**Console (layout A, 1920×1080 at 100 %):** command bar lamps · left: Assets (rows per machine by rail
and loop, ISA-101 band bars) and Fault injection · center: the map (FLOOR or EDGE, ISO or PLAN camera,
click to select, teal corner brackets, forecast machines amber) over the Selected, Fleet, Tags, Trends,
and Edge tabs · right: Verdict (STEADY, FORECAST with trip ETAs, ROOT CAUSE with a chain line), Actions,
Event log. No page scroll. Compact sizes below 1600×860, a stacked fallback below 1280×700.
**Nothing covers the console:** Execute confirms inside its Actions card, Add PLC is a form inside the
Fleet tab, and Remove needs a second click within 5 s. The floor camera frames the machines for any
panel shape. `page.jsx` split into `lib/` hooks and one component per panel. `Machines.jsx` removed.
**Fixed on the way (older bugs):** `Graph.jsx` passed a ref through a `next/dynamic` wrapper that drops
refs, so the EDGE graph never ran zoomToFit or its off-screen pause. `Floor.jsx` used the deprecated
`THREE.Clock`.
**Grafana route:** nginx proxies `/grafana/` to `prom-grafana.observability.svc` behind the same login.
Grafana got `GF_SERVER_ROOT_URL` (`/grafana/` sub-path) and `GF_SERVER_SERVE_FROM_SUB_PATH=true` by
`kubectl set env`, not helm. `deploy/golive.sh` re-applies both and checks them.
`deploy/values/prometheus.yaml` carries them for a fresh install.
**Verified locally:** `next build` passes. In the dev preview, 1920×970 and 1366×700 have no page
scroll. Row clicks and map clicks select assets, a cabinet click opens its PLC card, ISO and PLAN switch,
the inline confirmations render, and the Remove arm expires. No JS errors or React warnings.
**Verified on forge:** all 35 changed files matched by sha256 over Syncthing. `make push
ONLY="dashboard"` passed, and the rollout reached Ready. `/` 401, `/healthz` 200, http 301, and
`/grafana/` 401 from the box and over Tailscale. Grafana answers `/grafana/api/health` (database ok),
both `d-solo` panels 200, `appSubUrl` /grafana. The new golive checks pass. A POST query with the
proxy's Host and Origin headers reaches Grafana's query layer, so no origin check blocks the panels.
**Not verified yet:** the authenticated view in a browser (Claude does not type the console passwords).
The operator logs in and confirms the console and the Trends graphs.
**Docs updated:** `dashboard/README.md`, `README.md`, `INNOVENT_PLAN.md`, `POC_SCRIPT.md` (every beat
location, six Boot probes, inline confirmation), `PIVOT_SETUP.md` (step 5.2b, section 6.5), `HANDOFF.md`.
Nothing is committed.

**LOG-063 · 2026-09-16 · The floor map gets a normal orbit camera. Live on forge.**
**Operator report (after the first live login):** the drag direction was inverted, and a drag past a
certain angle also zoomed in.
**Cause:** the hand-rolled camera added the drag distance to the azimuth, so the scene moved against the
cursor. It also re-framed the hall's bounding box after every rotation step (LOG-062), so the zoom changed
while the operator rotated.
**Fix:** `Floor.jsx` uses three.js OrbitControls (from the `three` package, no new dependency). Drag
rotates with the cursor, right-drag pans, and the wheel zooms (0.5 to 5). A rotation never changes the
zoom. The polar angle stays between almost straight down and about 12° above the slab. The frustum keeps
its height in world units, so a panel resize never zooms. ISO and PLAN are presets that aim and frame the
camera once. A second click on the active preset resets the view. A new plant layout (Add PLC)
re-frames the camera only when the operator has not moved it. Picking now reacts to a left click only.
**Verified locally:** `next build` passes. In the dev preview, a drag rotates with the cursor, and machine
sizes stay the same through the rotation. The wheel zooms, a drag never selects, a click still selects,
ISO resets, PLAN frames the schematic, and a plane toggle keeps the preset. No JS errors or warnings.
**Docs updated:** `dashboard/README.md`, `POC_SCRIPT.md` beat 2.1.

**LOG-064 · 2026-09-16 · The college lab reference PLC models leave the code. The factory bring-up on forge waits for an operator go-ahead.**
**Operator directive:** remove the reference PLC models (Micro820, S7-200, and similar) and make sure the
code works without them. Then bring the factory up on forge.
**Removed:** the `ab-micro820` profile (`vplc/profiles.py`, `api/fleet.py`, the dashboard mock) and the
`bottle-filling` task, which copied the S7-200 lab experiment. The packaging tasks now hint
`generic-iec`. Two profiles stay: `siemens-s7-1200` (S7comm, used by `plc-stamping`) and `generic-iec`
(Modbus TCP). The lab PLC facts left `HANDOFF.md` section 6.1 and `INNOVENT_MASTER_PLAN.md` (2H, 3B, D3).
(Edited 2026-09-22, LOG-076: a data plan is removed.)
**Tests:** the tests that used the removed items now use `generic-iec`, `packaging-cell`, and
`stamping-line`. The 409 check for a different cell layout now loads `stamping-line` onto a packaging
PLC. A new assertion checks that the runtime refuses an unknown profile at boot. Results: correlation 60,
plant 20, api 24, scada 59, vplc 39 (was 41, the two bottle-filling tests are gone). `next build` passes.
**Forge:** Syncthing delivered all 21 changed files (sha256 match), and the two task files are gone there
too. `make images` built vplc, api, dashboard, and tag-server into the local Docker cache. In those
images, the vplc has 3 tasks and 2 profiles, the api lists 2 profiles, and the dashboard bundle has no
Micro820 string. Nothing was pushed, and no pod changed.
**Found before the bring-up:** `plc-pack-1` (the LOG-060 rehearsal PLC) still runs on `ab-micro820`. The
new vplc image refuses that profile at boot, so a restart on it would crash-loop. Remove the PLC before
the vplc push. With no fault fired, the engine named compressor-1 as root (0.41) with 8 findings. A clean
bring-up needs the LOG-035 procedure: engine memory backup and wipe, then a quiet PS0 soak.
**Blocked:** the auto-mode permission classifier stopped the API removal of `plc-pack-1` (the operator
token read from the `visr-auth` Secret, then DELETE). The rest of the bring-up changes live workloads
too, so it waits for the operator's go-ahead (`HANDOFF.md` section 0, item 3).
**Docs updated:** `FLEET.md`, `vplc/README.md`, `POC_SCRIPT.md` (beat 2b.2), `INNOVENT_MASTER_PLAN.md`,
`HANDOFF.md`. Nothing is committed.

**LOG-065 · 2026-09-16 · `deploy/factory-up.sh`: the factory bring-up becomes one operator command. Shutdown facts for forge.**
**Operator:** "go" for the four bring-up steps: remove `plc-pack-1`, push and run golive, back up and
wipe the engine memory, soak PS0. The operator also asked whether forge may shut down after the task.
**Blocked again:** after the go, the permission classifier refused the API removal of `plc-pack-1` a
second time. Claude does not work around it. The operator runs the bring-up.
**Built:** `deploy/factory-up.sh` (no sudo). Step 1 removes every UI-created PLC through the api. Step 2
pushes the images (`ONLY`, and `PUSH=0` skips it). Step 3 runs `golive.sh` and stops on a failed check.
Step 4 stops the engine, copies the memory from a maintenance pod to `~/visr-backups`, compares the file
count and the byte count, wipes, and starts the engine. An exit trap starts the engine on every failure
path, and `WIPE=0` skips the step. Step 5 starts the PS0 watcher in the screen session `visr-ps0`. It
writes one read-only verdict line per minute (QUIET or NOISY) to `/var/tmp/visr-ps0-soak.log`.
**Found:** the old wipe in `PIVOT_SETUP.md` step 2 (rm, then a rolling restart) can keep old state. The
engine holds its SQLite files open and creates some of them on first use. The old and the new pod overlap
during a rolling restart, so the old pod can write into the new files. Step 2 now stops the engine first.
**Checked:** `bash -n` passes on the laptop and on forge, the file has LF line endings, and the forge copy
matches by sha256. The script has not run yet.
**Forge shutdown facts:** k3s and docker are enabled at boot, the registry container restarts always,
Wake-on-LAN is on for `eno1`, and no reboot is pending. All state sits on disk volumes, so a shutdown is
safe for the data. A restart makes the engine re-learn, so a PS0 soak runs again after power-on.
**Docs updated:** `PIVOT_SETUP.md` (Resume step 4 and step 2), `HANDOFF.md` (section 0 items 3 and 6,
section 5), `POC_SCRIPT.md` (step 0.3), `soak/README.md`. Nothing is committed.

**LOG-066 · 2026-09-17 · First factory-up run (WIPE=0): plc-pack-1 removed, new images live, one golive check flaked.**
**Run:** the operator started `factory-up.sh` with `WIPE=0` at 00:03. Forge shuts down tonight, so the wipe
and the soak run after the next power-on. Step 1 removed `plc-pack-1` through the api, and the fleet now
holds only `plc-stamping`. Step 2 pushed vplc, api, dashboard, and tag-server. Step 3 ran `golive.sh`: 27
of 28 checks passed, including plc-stamping over S7comm with GOOD tags on the new vplc image.
**Flake:** the login wall check ran once, right after the dashboard rollout, and did not get 401. The
NodePort can route to the old pod for a few seconds. Two minutes later the wall answered 401 from the
box, the LAN, and Tailscale, and `/healthz` answered 200. The script stopped at that check, so the PS0
watcher did not start. A shutdown follows, so the watcher is not needed tonight.
**Fix:** `golive.sh` now retries the three dashboard front-door checks for up to 60 s, like the plant checks.
**Next on forge:** after power-on, run `factory-up.sh` with `PUSH=0` (golive, wipe, soak).

**LOG-067 · 2026-09-17 · Stage 2 deck, first full draft on the official template (local, gitignored).**
**Operator directive:** start the Stage 2 PPT tonight while forge is off, per the Stage 2 rules. Screenshots
from the earlier demo work may stand in for now.
**Files (all in the gitignored `Design_PPT/`):** `SiliconKnights_Tata_VISR_Stage2.pptx` (15 slides),
`stage2_build.py` (builds the deck from the template), `stage2_charts.py` (charts from the plant model),
`stage2_assets/` (images), `stage2_render.ps1` and `stage2_sheet.py` (PowerPoint renders for QA), and
`SiliconKnights_Tata_VISR_Stage2_overview.png`.
**Rules kept:** the builder starts from `InnoVent-27_Stage_2_Presentation_Template.pptx` and keeps the logos
and footers. It removes the guidelines slide and the content checklist slide. Every visible run is Arial.
Titles are 28 pt, body text 16 to 18 pt, captions 10 to 12 pt, in the four brand colors on white.
**Slides:** cover, team, problem (with the downtime cost and the market size), objective and approach (with a
comparison against alternatives and the phases), solution overview, solution architecture (a new slide, not in
the template), technical implementation, novelty: prior art, novelty: competitor benchmark (a new slide),
challenges, results, demonstration, project plan, closing, additional information (honesty notes and sources).
Every slide has speaker notes: about 1,280 spoken words, 9.8 min at 130 words a minute.
**Evidence used:** measured results only, with the place and month on each card (LOG-046, LOG-058, LOG-060).
The PS1 chart comes from `plant/sim/main.py` stepped on the laptop: press-1 42.9 to 85.3 A, rail A 360.3 to
344.3 V, which matches the box figures. The console images are the LOG-062 previews on mock data. Their
MOCK badge shows, and the captions say so.
**Research (web, 2026-09-17):** Siemens True Cost of Downtime 2024 ($1.4 trillion a year, $2.3 million an hour
in automotive). MarketsandMarkets, March 2026 (USD 13.89 billion in 2026 to 23.79 billion in 2031, 11.4 %
CAGR). ISA-18.2 and EEMUA 191 alarm rates. Prior art: Bauer et al. 2007, Schleburg et al. 2013, MicroRCA 2020,
Dynatrace Davis AI. Competitors: Siemens Senseye with Maintenance Copilot, GE Vernova SmartSignal. The
benchmark rates a cell only when public documentation supports it. Otherwise it shows a dash.
**Checked:** the pptx skill validator passes against the template. PowerPoint rendered all 15 slides, and a
visual pass fixed text overflow, the half-circle symbol angles, number-unit line breaks, and a duplicate-part
save bug (add slides before deleting any).
**Still open (amber chips in the deck):** the college name, the team leader, each member's branch, role and
photo, mentor or user feedback, the demo video link. After the soak: live console captures to replace the mock
images, PS0 silence, PS1 time to verdict, and the Execute relief row. Operator decision: mention the earlier
accelerator finalist status or not.

**LOG-068 · 2026-09-17 to 19 · The PS set grows to seven failure families, and the engine learns to stay quiet.**
**Why:** the operator said the deck built the whole pitch around PS1 and felt thin, and that the Secure part of the
track had no real story. Field research (web, 2026-09-17) mapped real incidents to the ways a plant fails between
machines. The research notes sit in the gitignored `FIELD_RESEARCH.md`, because they hold presentation notes.
**The set (`SCENARIOS.md`, the new contract):** PS1 rail-sag cascade (Milford Haven 1994), PS2 power sag trips the
chiller (Azure Australia East 2023: a chiller-1 overload relay trips on sustained undervoltage and cuts the coolant
flow), PS3 control network storm (Browns Ferry 2006: an M/M/1/K segment model on the real Modbus field link),
PS4A setpoint write with no record (Stuxnet, FrostyGoop: the `rogue-ews` pod writes press-1 DERATE over S7comm),
PS4B current report contradicts the feeder (Buncefield: the vPLC AMPS word replays while the real current rises),
PS5 coolant pump degradation (LG Polymers 2020), PS6 the monitor runs out of memory (Toyota 2023, 2003 blackout:
a real leak in the tag server, then an OOM kill and a blind SCADA view).
**Secure:** the api runs two integrity checks every 5 s (`api/integrity.py`): a writable setpoint that changes with
no signed ledger row or write intent, and a rail whose feeder meter does not balance the PLC-reported currents.
Findings go to `/api/integrity`, the `integrity` key of `/api/graph`, and ledger rows with actor `visr` (`unsigned`,
`balance`). The act loop blocks an asset whose controller channel is under suspicion. Refusals now write rows for
the 403 static delete and the two 409s. `deploy/refusals.sh` shows 401, 409 and 403 on camera.
**The soak found a real engine bug (17-18 Sep, box):** 1 quiet line in 1,438. Every plant baseline was stored as
median 0.0 and MAD 0.0: the engine restarted while the aggregator ring was nearly empty, learned the zero padding,
and the storm rule then locked it. A 90th-percentile gate also flagged every normal compressor ON window. Fixes in
the engine: learn only from windows 90 % full (`BASELINE_MIN_COVERAGE`), gate quantile 35 (`GATE_Q`), plant names
kept whole in memory keys (qa-scanner-1 was stored as "qa"), multi-source families (`heat_load|cooling_shortfall`),
a witness over source-only members, a forecast floor at the learned band, bare edges only from deviating plant
members, and a 0.3 °C MAD floor for coolant. The box also scraped cAdvisor twice: the `mem` query now pins
`job="cadvisor-fast"` (the tag server showed 63 MiB against a real 31 MiB).
**Verified offline (2026-09-19):** the real plant physics through the real service loop, engine started before the
ring filled. PS0 silent 180 of 180 passes for the last 30 min. Roots: PS1 press-1 (about 80 s), PS2 compressor-1
through rail psu-b and loop cool-1 (40 of 42), PS3 hmi-gw, PS5 chiller-1 (52 of 60), PS4B press-1. After a reset
the true root clears in 2 to 5 min, with no wrong roots. Tests: correlation 69, plant 35, api 56, scada 63, vplc 43
(266). `next build` passes. The console reads the catalogue, shows the integrity and blind bands, walks the two-hop
chain, and has mock variants chain, network, integrity and blind.
**Deck v2 (gitignored `Design_PPT/`):** 17 slides. New: "Seven ways a plant fails between machines" and "Secure by
design" (ISA/IEC 62443-3-3 FR1 to FR7, built today and next). The problem slide leads with Milford Haven,
LG Polymers, Toyota and JLR. Results use the 17 Sep go-live numbers.
**Not yet on the box:** every new scenario, the engine fixes, and the refusal rows. Next: `factory-up.sh` (push the
six changed images, wipe, soak), then `deploy/proof-run.sh` and `deploy/refusals.sh` for the evidence.
**Box check before the deploy (2026-09-19 17:05):** forge ran the old images, and no screen session was open. The
`alloy` pod had restarted 5,049 times in 98 days. Its config used semicolons, which Alloy syntax does not allow,
so the config never loaded. LOG-008 called this ignorable, but the engine now watches `observability`, so a pod in
a restart loop there adds noise to a soak. Fixed in `deploy/values/alloy.yaml` (one attribute per line).
`factory-up.sh` step 2b upgrades the release at its installed chart version (1.10.0), and step 5 moves the old soak
log aside so that the new log holds one soak only.

**LOG-069 · 2026-09-19 · The new build is live on the box, and the PS0 soak passes.**
**Run:** the operator started `factory-up.sh` (run 3, defaults) at 17:35. It pushed the six changed images and
upgraded alloy. It then ran golive (36 of 36 PASS, the new checks included), backed up the engine memory
(`~/visr-backups/engine-memory-20260919-173823.tar`, 70 MB), wiped it, and started the watcher at 17:39. The running
pods use the pushed digests. Alloy now runs, and Loki gets pod logs. The alloy status check gave a false WARN: the
upgrade changed only the ConfigMap, so `rollout status` read the old stuck state. Step 2b now restarts alloy first.
**Soak:** the first 15 lines were NOISY (up to 47 findings, roots Prometheus, OpenPLC and the historian) while the
restart wave left the window. Every line from 17:54:23 is QUIET: no finding, root, forecast card or integrity
finding, and no chiller trip. At 21:35 the count was 221 QUIET of 236 lines, 3 h 40 min unbroken. The 24 h soak of
17-18 Sep had 1 QUIET line in 1,438, so the zero-baseline fix holds on the box. The engine stored 7 cases from the
restart wave (4 to 11). They do not break the silence. The saved verdict graphs of the proof run
show if a scenario matches one of them (`meta.case_id`).
**Next:** `deploy/proof-run.sh` then `deploy/refusals.sh` in one screen session. `proof-run.sh` now copies the
watcher log to its evidence folder, adds the soak numbers to the summary, and marks its start and end in the
watcher log.

**LOG-070 · 2026-09-19 · First box proof run: seven checks pass, three scenarios lose a race with the thermal trips.**
**Run:** `proof-run.sh` then `refusals.sh`, 21:52 to 22:27 (34 min), evidence in `/var/tmp/visr-proof-20260919-215235`.
**Pass on the box:** the PS0 soak before the run (238 QUIET of 253 lines, QUIET from 17:54:23, 3 h 58 min), no token
401, stale id 409, base PLC delete 403, audit chain intact (29 rows). PS3 root hmi-gw after 81.2 s. PS4A unsigned
write after 39.1 s, and Caretta named the client `rogue-ews`. PS4B current balance after 15.0 s (gap 18.22 A). PS6
leak card after 105.2 s, blind SCADA view after 203.5 s, SCADA back 3.0 s later.
**Fail or not valid:** PS1 named press-1 after 90.5 s, but press-1 had tripped at 78 C about 77 s after the fault
(friction 1.9: 42.9 to 85.3 A, rail 360 to 344 V, heat to 78 C). Execute went to a stopped press, so the relief
row (0.2 to 0.23 A) proves nothing, and the verdict had cleared before the reset (0.0 s). PS2 named compressor-1
after about 80 s with the chiller relay tripped, but the loop hop through chiller-1 never showed at the same time:
OpenPLC tripped the loop machines first, and the root moved to cnc-1, then chiller-1. PS5 fired only 60 s after
the PS1 reset: the first card (press-2, 18 s) was not for the first machine to trip (furnace-1, 40 s). The
narrator timed out: no model was loaded, and the 6.1 GB model does not fit the 4 GB GPU.
**Cause:** the soak fix `GATE_Q=35` holds a verdict until a signal stays out of band for most of 2 min, so a root
needs about 80 s. Since July the thermal time constants were 30 to 90 s, so OpenPLC trips a hot machine in 40 to
95 s. The offline replay did not show this, because it runs no OpenPLC trip coil.
**Diagnosis tools (scratchpad):** a per-family probe on the real sim and the real service loop, with the OpenPLC
78 C latch emulated and the engine clock on simulated time. It reproduces the box: press-1 trips at +71 s, root at
+80 s.
**Fix (laptop, not yet on the box):** (1) `plant/sim/main.py`: thermal time constants x3 (press-1 120 s, press-2
165 s, cnc-1 90 s, furnace-1 270 s). The temperature noise now scales with sqrt(40 s / tau), so the stationary spread
stays about 0.22 C. Steady temperatures do not change. (2) `correlation/engine/merge.py`: a held edge votes for root
only when its source is a finding of the same signal. A bus-voltage finding on cnc-1 had let a held coolant edge
cnc-1 -> press-1 vote, and the merged root flipped to cnc-1 while both signals ranked press-1. New test
`test_merge_memory_edge_does_not_vote_for_another_signals_finding` (fails on the old merge). (3) Catalogue
`expect_s`: PS1 90, PS2 150, PS3 90, PS6 120. (4) `deploy/proof-run.sh`: calm before PS5, lead time per machine
(its own card against its own trip), cards on machines that did not trip, a timeline file per fault, a graph on a
failed check, a flag when Execute reaches a tripped press, and a 40 s narrator timeout. A dry run on a fake api
passes. Tests: correlation 70, plant 35, api 56.
**Offline with the fix (candidate copy, probe):** PS0 840 of 840 passes quiet over 2 h 20 min. PS1: root press-1 at
+80 s, trip card at +20 s, trip at +219 s when nobody acts. Execute at +100 s: 85.2 to 45.0 A, rail 344.5 to 359.7 V,
no trip. PS2: root compressor-1 at +20 s, the loop hop at +130 s, the first trip at +160 s. PS5: root chiller-1 at
+80 s. Lead per machine: furnace-1 86 s, press-1 117 s, press-2 306 s. One false card: cnc-1 settles at about 66 C,
below the trip. After machines trip, the root still moves among the tripped machines (known limit).

**LOG-071 · 2026-09-20 · Second box proof run: six of seven scenarios and every refusal pass.**
**Deploy:** factory-up run 4 (2026-09-19 23:20, defaults) put the LOG-070 fix on the box: golive 36 of 36, alloy
restarted cleanly, memory wiped. The PS0 soak was NOISY for its first 15 lines, then QUIET on every line from
23:40:11 (105 lines). The slower thermal constants did not make PS0 noisy. The operator armed a screen that
waited for 120 watcher lines with the last 30 QUIET, then ran `proof-run.sh` and `refusals.sh` (01:24 to 02:12).
**Evidence** (`/var/tmp/visr-proof-20260920-012448`): PS1 root press-1 after 74.4 s on bus voltage (write, rail,
temporal), trip card after 34.2 s, and no trip. Execute reached a running press: press-1 85.3 to 45.01 A, rail
psu-a 344.2 to 359.44 V after 60 s. PS1 verdict clear 285.5 s after the reset. PS5 (after a calm verdict):
furnace-1 card 32.5 s, trip 112.7 s, lead 80.2 s. press-1 card 22.1 s, trip 133.0 s, lead 110.9 s. Cards on
press-2 and cnc-1 had no trip in the watch window. PS3 root hmi-gw after 81.3 s (write, net, temporal). PS4A
unsigned write after 39.1 s (DERATE 100 to 30, client `rogue-ews`). PS4B current balance after 15.1 s (feeder
133.08 A, reported 114.5 A, gap 18.58 A). PS6 leak card after 93.2 s, blind SCADA view after 203.7 s, back 3.0 s
later. Refusals 401, 409, 403. Audit chain intact (29 rows). The narrator answered from the model this time.
**PS2 still fails:** root compressor-1 after 81 s, chiller relay trip at about 90 s, loop flow 120 to 53 L/min.
furnace-1 tripped at 196 s and press-1 at 220 s. The trips unloaded rail B, so the sag and the compressor root
ended at about 250 s, before the loop hop showed (301 s, root chiller-1). The two hops never showed together.

**LOG-072 · 2026-09-20 · The plant gets a supply above its rails, and the engine learns to look above the plant.**
**Why:** a plant meters its distribution board far more often than it exposes a PLC there, so VISR must be able
to root a cause above the plant. (Edited 2026-09-22, LOG-076: the data details are removed.)
**The gap:** `Rail.step` used a constant source voltage, so every sag in the model started inside the plant and
the engine could never root an external cause. PS2 also carried the wrong anchor. Azure Australia East 2023 was
an external supply disturbance, and PS2 is a stuck-on compressor.
**Built:** a `Supply` object `incomer-1` above every rail. `Rail.v_nom` is the fixed rating that every threshold
and band uses, `v_in` is the live board voltage, and `v_src` stays a read-only alias so the console, `/state`, and
the tests need no change. `Supply.step` draws no random numbers from the global stream, so at nominal the rail
noise is unchanged. New fault PS7, three metrics, `/state.supply`, and `rail:incomer-1` in `/domains`. New engine
rule in `correlation/engine/common_mode.py`: when every member of a declared medium deviates together and no
member leads, the root is the medium. `COMMON_MODE=0` turns it off. PS2 keeps its mechanism and states honestly
that it reproduces the Azure cascade and not the trigger. PS7 reproduces the trigger.
**Measured (laptop):** plant 39 tests pass, correlation 79 pass. A dip to 0.85 drops every rail together, `psu-c`
by the board's own 60.0 V, and the chiller relay trips inside 100 s from every cycle phase tested. `cnc-1`
throughput falls to 72.7 %, which matches the fixed-rating formula (73.8) and rules out a live-board threshold
(86.8), so the brownout branch stays alive under a dip.
**PS7 does not pass yet.** New `correlation/tests/replay_offline.py` drives the real plant model on the engine's
5 s grid through the real `run_pass`: PS1 PASS, PS2 PASS, PS7 FAIL with root `press-1`. The common-mode rule
fires and puts `incomer-1` above `psu-a` and `psu-c`, and `incomer-1 -> psu-b` forms on its own with a real 5 s
lag. A constant-power machine still answers the dip with more current, so the source path builds twelve false
aggressor edges and `press-1` outranks the board. The fix is an explained-load test in `_writer_edge`: a source
edge must not form when the source's current rise is fully explained by the voltage drop it is supposed to be
causing. That path carries PS1, PS2, and PS4B, so it gets its own change and its own regression run.
**Scope kept out:** the sub-second event channel. The aggregator polls every 5 s and the engine resamples onto a
5 s grid, so a sub-second dip leaves no sample. PS7 uses a held dip, which the grid does see. The event channel
and the meter as a separate instrument on its own protocol are later work.
**Data handling:** the repo is PUBLIC. This change adds `*.xlsx` and `mechatronics_lab_manual.pdf` to
`.gitignore`, so a bare spreadsheet or the lab manual at the repo root cannot enter git.
**Files:** `SCENARIOS.md` (2.2 anchor note, new 2.8, 3.1 to 3.3, 4.1, 4.2, new 4.5, 8, 10, 11), `FLEET.md` (7, 9,
11), `plant/sim/main.py`, `plant/tests/test_physics.py`, `plant/tests/test_cells.py`, `correlation/service.py`,
`correlation/engine/pipeline.py`, `correlation/engine/common_mode.py`, `correlation/tests/test_common_mode.py`,
`correlation/tests/replay_offline.py`, `deploy/engine.yaml`, `aggregator/queries.yaml`, `.gitignore`.

**LOG-073 · 2026-09-20 · PS2: the thermal latch was eating the rail hop. Residual flow goes to 0.60.**
**Why the box failed PS2 (LOG-071):** the two hops must be up in the same poll. They were not. At
`CHILLER_RESIDUAL_FLOW=0.45` the loop fell to 54 L/min after the chiller relay tripped, three cooled machines
reached the 78.0 C latch in `plc/program.st`, and their contactors opened. That unloaded rail B, the sag went
away, and the rail hop died at about 250 s, before the loop hop settled at 301 s.
**New tool:** `correlation/tests/ps2_lab.py`. `replay_offline.py` runs one pass of one family and answers who
the root is. PS2 never failed on the root. It failed on timing. The lab runs both plant families through the
real GraphMemory, the real learned baselines, the real merge, and the real forecaster, one pass every 20 s,
and it emulates the OpenPLC 78 C latch. It scores `deploy/proof-run.sh`'s own PS2 predicate, so the number it
reports is the number the box checks. Each setting needs its own process, because `service._memory` is a live
SQLite store and a second trial inherits the first trial's baselines.
**Measured, one process per setting:**

| residual | first pass | window | machines that latched | max temp |
|---|---|---|---|---|
| 0.45 (old) | t+60 | 120 s | press-1, press-2, furnace-1 | 78.0 |
| 0.50 | t+60 | 140 s | press-1, furnace-1 | 77.5 |
| 0.55 | t+60 | 160 s | furnace-1 | 78.0 |
| **0.60 (new)** | **t+60** | **180 s** | **furnace-1** | **77.7** |
| 0.65 | t+60 | 180 s | furnace-1 | 77.8 |
| 0.70 | t+60 | 180 s | none | 76.5 |
| 0.75 | t+80 | 160 s | none | 74.0 |

**Decision:** `CHILLER_RESIDUAL_FLOW` 0.45 to 0.60. It sits at the start of the plateau, it cuts the latches
from three machines to one, and it keeps that one real trip, so the trip forecast still predicts an event that
arrives. Above 0.70 nothing latches and the cards promise a trip that never comes. Every plant test passes
with the new value. Two assertions moved with it: the PS2 flow band (54 to 72) and the PS7 flow bound.
**What this does NOT prove.** The lab runs two signals. The box runs six, with `psi_io` primary, and the merge
across six can move the root. PS2 PASSES in the lab even at 0.45, so the lab reproduces the mechanism and not
the box verdict. The table measures the improvement between settings. Only a box proof run settles PS2, and
forge is offline.
**The second candidate, the curve-fit forecaster, is not a PS2 fix.** It changes the trip ETA, not whether the
two hops coincide. Cards already fire in the lab, four at the first passing poll. It stays on the list as a
separate improvement.

**LOG-074 · 2026-09-21 · `/data/` is now gitignored.**
**Why:** the operator made a `data/` folder at the repo root for local files. The repo is PUBLIC, and
`.gitignore` did not cover `data/`, so one `git add -A` would have published the folder. The Sep 26
recording stays on the physics model. (Edited 2026-09-22, LOG-076: the data details are removed.)
**Change:** `.gitignore` carries `/data/`. The leading slash keeps the rule at the repo root, so a code
folder named `data/` stays tracked.
**Files:** `.gitignore`.

**LOG-075 · 2026-09-21 · The sim review before the code freeze: six fixes, and no verdict moves.**
**Why:** the operator asked for a full pass over the plant model before the sim code is locked for the Sep 26
recording. Each suspect was first reproduced on the real model with a scratchpad script, then fixed with a test.
**Fixed:**
1. A repeat fault did damage. The API sends `/fault/<id>` on every console click. A second PS4B apply recorded
   the replay from the faulted current (truth 61.3 A, vPLC word 61.3 A), so the evidence was gone. Now
   `inject_fault` changes nothing for an active fault and answers 200 "already active".
2. Throughput froze under the brownout line. A machine that the voltage does not slow recovered only while its
   rail was above 368 V. Rail A idles at 360 V, so press-1 ran at 42.8 A with 2 % throughput after a trip
   whenever its PLC link was down. It now climbs back 2 points per tick at any voltage.
3. An idle 4 A labeler read below 0 A on 19 of 3,000 ticks. The current now has a floor of 0 A.
4. `loop()` took its step from the wall clock with no cap. A 120 s stall would integrate as one step. The
   thermal model then overshoots, and in a compressor window the chiller relay gains more than its trip heat.
   `tick_dt` now caps the step at 5 s, and a cell machine needs a `tau` of 10 s or more.
5. A plant-sim restart put every cooled machine back at 35 C, so the engine saw a warm-up ramp of up to 20 min
   on four machines. A cooled base machine now starts at its healthy steady temperature.
6. The HTTP server ran one request at a time with no read timeout. One silent connection could freeze
   `/metrics` and the liveness probe. It now runs one thread per request with a 10 s timeout, like the tag server.
**Doc fix:** SCENARIOS.md 2.8 said that the PS7 dip ramps over 3 s. The supply slews at 133 V/s, so the 60 V dip
lands within one tick. The text now says so.
**Kept on purpose:** rail A under the brownout line (cnc-1 and qa-scanner-1 at about 89 % with no fault),
`plant_heat_load_watts` as a heat index, and a plant with no planned stops. SCENARIOS.md 2.9 lists them.
**Measured (laptop):** plant 45 pass (6 new) plus the 2 known pymodbus errors. Correlation 79 pass. The offline
replay gives PS1 PASS, PS2 PASS, PS7 FAIL, the same as before. The PS2 lab at 0.60 gives the LOG-073 row
exactly: first pass t+60, a 180 s window, furnace-1 latched, 77.7 C.
**Freeze:** the sim code is frozen from here. The box still runs the old image. The next factory-up must carry
LOG-073 and LOG-075, then a soak and a proof run.
**Files:** `plant/sim/main.py`, `plant/tests/test_physics.py`, `plant/tests/test_scenarios.py`,
`plant/tests/test_cells.py`, `SCENARIOS.md` (2.8, new 2.9, 10), `FLEET.md` (13).

**LOG-076 · 2026-09-22 · No outside data in the repo, and the historian password leaves the manifests.**
**What was wrong (a Claude error):** the outside data files never entered git, but a written summary of them
did. The 2026-09-20 commit carried that summary in LOG-061, LOG-072, `SCENARIOS.md` (the PS7 row, 2.8, 11), and
a `.gitignore` comment. The uncommitted LOG-074, `SCENARIOS.md` 2.9, and one sim comment did the same.
`.gitignore` kept the files out, but nothing checked what the docs said about them.
**Decision (operator):** the project stays on the sim. The plant plane runs on the physics model only, and it
needs no field logs. The outside-data plan from LOG-061 is closed.
**Removed:** every data detail from `INNOVENT_LOG.md` (LOG-061, 064, 072, 074), `SCENARIOS.md`,
`INNOVENT_MASTER_PLAN.md`, `.gitignore`, `plant/sim/main.py`, and `correlation/engine/common_mode.py`. Each
edited log entry says so. The sim and engine edits change comments only. An AST compare shows the same code,
so the LOG-075 freeze holds.
**GitHub:** the 2026-09-20 commit leaves `main`. `main` goes back to its parent, and one new commit carries
LOG-058 to LOG-076.
**The historian password:** GitGuardian flagged the 2026-09-20 push for a "Generic Database Assignment". The
match was the historian password in `scada/deploy.yaml`. The same value sat in `scada/tagserver.py` and
`plant/deploy.yaml`, and it is in the public history back to the first commit. The database has no NodePort,
so the risk is low. The value is public, so it must change.
**Change:** a new Secret `plant/historian-auth` holds the password. The new script `deploy/historian-auth.sh`
makes it, and `deploy/golive.sh` runs the script in step 0, before any apply. When the Secret is missing, the
script makes a random password, sets it in the running historian through the local socket, and then writes
the Secret. The historian reads `POSTGRES_PASSWORD` from the Secret. The tag server DSN carries no password,
and libpq reads `PGPASSWORD` from the same Secret. No manifest or source file holds a database password now.
**Box effect at the next factory-up:** golive step 0 changes the password once. The apply restarts the
historian pod, and its data stays on the volume. Until the tag server restarts in step 3, the historian takes
no new rows. Step 5 then checks that the tag server writes the historian again. That line is INFO only,
because no PS verdict reads the historian. After that run, the old value in the history opens nothing.
**Rule from now on:** before a commit, search the staged diff for data details and credentials. No tracked
file describes an outside data file.
**Measured (laptop):** plant 45 pass, correlation 79, api 38, scada 53, vplc 32. The errors are the known gaps
in this interpreter (`pymodbus`, `snap7`, `fastapi`). `bash -n` passes on both scripts. The script has not
run on the box yet.
**Files:** `INNOVENT_LOG.md`, `SCENARIOS.md`, `INNOVENT_MASTER_PLAN.md`, `PIVOT_SETUP.md` (5.0c), `.gitignore`,
`plant/sim/main.py`, `correlation/engine/common_mode.py`, `plant/deploy.yaml`, `scada/deploy.yaml`,
`scada/tagserver.py`, `deploy/golive.sh`, new `deploy/historian-auth.sh`. Local only: `HANDOFF.md`.

**LOG-077 · 2026-09-22 · The OpenPLC web login leaves the vendor default, and the REST API goes off.**
**Why:** `plc/entrypoint.sh` logged in to the OpenPLC web UI with the vendor default login to upload
`plc/program.st`. NodePort 30081 shows that web UI on the LAN and on Tailscale. Anyone who reached it could
log in with the public default, then stop the PLC or replace the trip program. The OpenPLC latch is the
trip interlock for PS1, PS2, and PS5. GitGuardian already flagged one credential in this repo (LOG-076).
**Found on the box (read-only):** the box built the running image on 2026-07-04 from upstream commit `b5d4135`.
It keeps the web users in table `Users` of `webserver/openplc.db`, with the password as plain text. The
only user is `openplc`, with the vendor default. The same build also starts a REST API on HTTPS port 8443.
Its user database is empty after each pod start. While it is empty, `/api/create-user` needs no login, and
a REST user can stop the PLC or replace the program. No Service lists port 8443, so only pods and the node
can reach it. The runtime control socket (port 43628) listens on 127.0.0.1 only.
**Decisions (operator):** NodePort 30081 stays open for the demo, behind the new password. The REST API
goes off, so the web UI login is the only way to change the program.
**Change:**
1. The new script `deploy/openplc-auth.sh` makes Secret `plant/openplc-auth` with a random 48-character
   password when the Secret does not exist. `deploy/golive.sh` runs it in step 0. A second run changes nothing.
2. `deploy/openplc.yaml` mounts the Secret at `/etc/openplc-auth`. The mount is optional, with mode 0400.
3. `plc/entrypoint.sh` writes the Secret value into the user table before the web server starts, so the
   vendor default never opens a session. Then it logs in with that value, uploads, compiles, and starts
   the program. At the end it checks that the dashboard shows `plant_trips` in Running.
4. Without a usable Secret, the entrypoint sets a random password for that pod only. The trip loop starts,
   and nobody can log in to the web UI. When the table change fails, the entrypoint writes a FAIL line.
   It then logs in with the vendor default, so the trip loop still starts. The password goes in through
   stdin only, so no command line and no log line holds it.
5. The new script `plc/rest-off.sh` removes the line that starts the REST API from `webserver.py` at build
   time. It stops the build when the file does not look as expected or does not parse.
6. The new script `deploy/openplc-rollout.sh` has four modes. `test` builds the new image on top of the
   running image, with no source build, and checks it in a throwaway container. `deploy` stops during a
   soak, a proof run, an active fault, or a trip. Then it keeps the old image as `skn/openplc:pre-log077`,
   pushes, applies, restarts, and verifies. It rolls back when the trip loop does not close with
   `plant_trips`. `rollback` puts the old image back. `probe` prints four words for `golive.sh`.
7. `deploy/golive.sh` step 5 has five new checks: NodePort 30081 answers, the web UI refuses the vendor
   default, the Secret password logs in, the dashboard shows `plant_trips`, and nothing answers on port 8443.
   A failed check gives an INFO line and does not stop the run. No PS verdict reads the web UI.
**Box effect at the next factory-up:** factory-up does not build the openplc image. Golive step 0 writes the
Secret, and the apply adds the mount. The old image does not read either, so the trip loop does not change.
The five new checks give INFO lines until the rollout.
**Measured (box, no change to k3s, the registry, or the images):** the running image ran in throwaway
containers with no network. Each container got the new entrypoint and `rest-off.sh` through a mount.
- With the Secret: the probe gave `200 302 plant_trips 000`. The table held one user with a 48-character
  password, and no user had the vendor default. No process command line, process environment, or log line
  held the password. The bring-up took 12 s.
- Without the Secret, and with an unusable Secret value: WARN lines, a random password, `plant_trips` in
  Running, and nothing on port 8443.
- The open ports were 102, 502, 8080, 44818, and the local control socket. Nothing answered on port 8443.
- `rest-off.sh` gave the same result on a second run. `bash -n` passes on the five changed scripts.
**Not done:** the rollout. The box still runs the image from 2026-07-04 with the vendor default login on
NodePort 30081. The operator runs `deploy/openplc-rollout.sh` after the Stage 2 recording, or before it
after a go. Never run it during a soak, a proof run, or a recording. A PLC restart clears every latched trip
and opens the trip loop for about one minute.
**Still open:** Modbus :502, S7comm :102, and EtherNet/IP :44818 have no login in their protocols. So any pod
in the cluster can write PLC data words. A NetworkPolicy can limit that later. The web UI is plain HTTP, so
a login from the LAN sends the password unencrypted.
**Files:** `plc/entrypoint.sh`, `plc/Dockerfile`, new `plc/rest-off.sh`, `plc/OPENPLC.md`,
`deploy/openplc.yaml`, `deploy/golive.sh`, new `deploy/openplc-auth.sh`, new `deploy/openplc-rollout.sh`,
`PIVOT_SETUP.md` (4.1, 5.0d, 5.3b, 7), `INNOVENT_LOG.md`.

**LOG-078 · 2026-09-22 · The console and the deck say "Scenario 1", and the locked code goes live on forge.**
**Naming (operator decision):** a judge can read "PS1" as "problem statement 1". The deck and the console
now say "Scenario 1". The fault rows show the number only. The map banner, the event log, and the message
after a fire say "Scenario 1". The API, the scripts, the ledger, the tests, and `SCENARIOS.md` keep the PS
IDs. Only the display changes (`scenarioNo` and `scenarioName` in `dashboard/app/lib/format.js`).
`next build` passes. On the mock data in the browser, the rows read 0 to 6 and the event log reads
"Scenario 1".
**Deck (local, `Design_PPT/`):** the families table header is "Scenario". Every slide, every note, and the
chart use "Scenario" with a no-break space before the number. The plan follows the sim-only decision
(LOG-076): next come Scenario 2 on the box and a supply-dip scenario, and October to November adds the
hardware rung. The deck has no ABB mention and no feedback chip. The team chips stay until the team sends
its details. The audit reports 0 problems, and the template validator passes.
**Recording script:** `POC_SCRIPT.md` uses the new names. Its prep now follows factory-up, the soak, and
the proof run.
**Workflow (operator):** no branches in the repo. Every change goes into the main folder. The LOG-077 work
came from a separate worktree session. Its files went into the main folder byte for byte, and then the
worktree and its local branch were removed. Neither reached GitHub.
**Deploy (forge, 2026-09-22, operator go):** forge had all 171 source files of the laptop (hash check).
One screen session, `visr-ship`, ran `deploy/openplc-rollout.sh deploy` and then `deploy/factory-up.sh`
with the defaults.
- OpenPLC rollout, 14:03 to 14:04: the throwaway test and the live pod both gave the probe
  `200 302 plant_trips 000`. The web UI refuses the vendor default, takes the Secret password, runs
  `plant_trips`, and nothing answers on port 8443. The old image stays as `skn/openplc:pre-log077`.
- Factory-up, 14:04 to 14:09: the build made new correlation-engine, dashboard, plant-sim, and tag-server
  images from the new source. The api and vplc code did not change, so their images stayed. Golive gave
  42 PASS, 0 FAIL, and 0 INFO lines. Step 0 changed the historian password in the running database and
  wrote Secret `plant/historian-auth`. The apply restarted the historian pod once, and the tag server
  writes the historian again. The five OpenPLC checks of LOG-077 pass on the live pod.
- The engine memory backup is `~/visr-backups/engine-memory-20260922-140759.tar`. After the wipe, the PS0
  watcher runs in screen `visr-ps0` from 14:08:56, for 24 h. Its first line was NOISY with the restart
  churn, as on 2026-09-19.
**Next:** the soak passes when the last 30 watcher lines are QUIET. Then `deploy/proof-run.sh` and
`deploy/refusals.sh` run, and the deck gets the new numbers. The recording is on 2026-09-24.
**Files:** `dashboard/app/lib/format.js`, `dashboard/app/FaultInjection.jsx`, `dashboard/app/EventLog.jsx`,
`dashboard/app/MapPanel.jsx`, `dashboard/app/Console.jsx`, `dashboard/app/lib/useConsoleData.js`,
`dashboard/README.md`, `SCENARIOS.md` (section 1 IDs, section 7), `POC_SCRIPT.md`, `INNOVENT_LOG.md`.
Local only: `Design_PPT/stage2_build.py`, `Design_PPT/stage2_charts.py`, `HANDOFF.md`.

**LOG-079 · 2026-09-22 · Proof run 3: all seven scenarios and every refusal pass on the box. PS2 passes for the first time.**
**Run:** screen `visr-proof` on forge, 16:26 to 17:10 (2,653 s), on the images of LOG-078. The soak before it had
123 QUIET of 138 lines, QUIET since 14:23:59. A wrapper warmed the narrator first (HTTP 200 in 40.9 s). The
evidence is in `/var/tmp/visr-proof-20260922-162619`.
**Measured, against proof run 2 (LOG-071):**

| Check | 2026-09-20 | 2026-09-22 |
|---|---|---|
| PS1 root press-1 | 74.4 s | 80.3 s, write, rail, temporal on bus_voltage |
| PS1 first trip card | 34.2 s | 30.2 s, and press-1 did not trip |
| Execute relief, press-1 | 85.3 to 45.0 A | 85.3 to 45.0 A |
| Execute relief, rail psu-a | 344.2 to 359.4 V | 344.4 to 359.6 V |
| PS1 clear after the reset | 285.5 s | 305.7 s |
| PS5 first trip and its lead | furnace-1 at 112.7 s, 80.2 s | furnace-1 at 116.5 s, 76.3 s (press-1 102.5 s) |
| PS2 root compressor-1 with the loop hop | none, fail | 304.2 s |
| PS3 root hmi-gw | 81.3 s | 90.2 s |
| PS4A unsigned write | 39.1 s, rogue-ews | 39.1 s, rogue-ews |
| PS4B current balance | 15.1 s, 18.6 A gap | 15.0 s, 18.1 A gap |
| PS6 leak card, then blind | 93.2 s, 203.7 s | 90.4 s, 205.1 s, SCADA back 3.0 s later |
| Refusals | 401, 409, 403 | 401, 409, 403 |
| Audit chain | intact | intact, 29 rows |

**PS2:** LOG-073 set `CHILLER_RESIDUAL_FLOW` to 0.60, so fewer cooled machines latch and rail B stays loaded
while the loop hop forms. The root and the loop hop now hold in the same poll. The chain needs 304 s, and the
catalogue gives `expect_s` 150 for PS2, so the console message after a fire gives too short a wait. PS2 is not in
the recording script, and the code stays frozen, so the value does not change now. PS2 clears 480 s after the reset.
**Deck (local):** the families slide marks all seven as measured on the edge box. The Results, Secure, and plan
slides and the honesty notes carry the numbers of 2026-09-22. The audit reports 0 problems, and the template
validator passes.
**Files:** `INNOVENT_LOG.md`, `SCENARIOS.md` (status, 2.2). Local only: `Design_PPT/stage2_build.py`, `HANDOFF.md`.

**LOG-080 · 2026-09-23 · The console follows ISA-101: gray when normal, color only when abnormal, values without explanation text.**
**Why:** the operator asked to remove the explanation text from the panel and to use one name for it,
"Fault injection".
**Change (display only, the API and the scripts do not change):**
- The panel header drops the "scenarios · sim" label. The title is "Fault injection".
- Each scenario row drops the mechanism line, the "injected" and "owner not answering" prefixes, and the
  incident anchor. The row tooltip still holds the mechanism and the anchor. The glyph still shows the
  state.
- The Steady plant row drops "no fault · baselines mature · the engine stays silent".
- After Fire or Reset, the message says "Scenario N fired (time)" or "Scenario N reset (time)". It no longer
  gives an expected wait, so the short `expect_s` of PS2 (LOG-079) no longer shows. Error messages do not change.
- `globals.css` drops the unused `.fi-ds` and `.fi-an` rules.
- Selected tab header (second request of the day): the kind moves to its own line under the name, as
  chips ("MACHINE", "RAIL PSU-A", "COOLED" or "UNCOOLED", and a red "TRIPPED" chip). Rails, the loop,
  and segments use the same chips. The grey "auto · root cause, forecast, or first rail" and
  "operator pick" texts are gone. In auto mode, one teal label names the reason: "Auto · root cause",
  "Auto · forecast", or "Auto mode". After a pick, a blue command button "Go to root cause" (or
  "Auto mode" when there is no root cause) replaces the label and the small "follow root" button.
  `Console.jsx` computes the reason and passes it through `Tabs.jsx` to `Selected.jsx`.
- Whole console (third request): the operator asked for an "industrial grade" console with only the
  information that matters, and asked for a check of HMI guides first. Guides read: ISA-101 and the
  High Performance HMI method (normal in gray, color only for abnormal states, redundant shape coding,
  dim labels and bright values, no decoration text).
  - Color: the new token `--normal` (#8A93A6) replaces teal for every normal state: status glyphs,
    sparklines, band ranges, running PLC cards, onboarding bars, map machine lamps, closed-loop PLC
    links, and normal pods in the edge graph. Teal stays only as the brand accent (corner brackets,
    boot splash). The map selection brackets use the command blue edge. Evidence chips are neutral.
  - Command bar: no brand subtitle, no "signal" readout, no refresh period. The lamps show a value
    only when it matters (fleet run count, SCADA blind, auth not enforced, CHAIN BROKEN, integrity).
  - Panel headers: no meta text while normal. Verdict shows "engine offline" and Event log shows
    "CHAIN BROKEN" only when true. Actions shows the count.
  - Assets: no "machines · simulated". Loop line shows the pump only. Feeder and segment lines are short.
  - Map: no hint line, no "physics-simulated" caption, banner "Scenario N injected", legend root,
    affected, tripped.
  - Selected: short sub-lines ("trip 78 °C", "plc-stamping · set 100 %", "low 335 V"). The tag table
    shows measured tags only, without the derived formulas.
  - Verdict: no narrator source line. Short integrity and blind bands.
  - Actions: the proposal shows the expected effect only. The confirm step shows the write and the PLC.
    Advisory cards drop the "cites" line. Event log rows drop the citation detail.
  - Fleet: the onboarding row shows only on PLCs added with Add PLC. The base PLC showed times from a
    week-old Deployment (+590330 s). The row now stays inside the card (`minmax(0, 1fr)` columns,
    ellipsis), with short labels (req, pod, run, enroll, scada, engine) and times in s, m, or h.
  - Tags, Edge, Trends: one-word bars.
**Checked:** the local dev server with mock data renders the panel with no explanation text. The
Selected header shows the chips, "Go to root cause" after a pick of qa-scanner-1, and "Auto · root cause"
on press-1 after the click. The browser console shows no errors except the 404s of the missing dev API.
The whole console at 1600 x 900 (incident mock) shows color only on the root, the affected machines,
and the fault. Both Fleet cards stay inside their bounds. `npm run build` passes.
**Not deployed:** the box still runs the dashboard image of LOG-078. More console removals come from the
operator first, then one dashboard image push.
**Files:** `dashboard/app/FaultInjection.jsx`, `dashboard/app/Console.jsx`, `dashboard/app/lib/useConsoleData.js`,
`dashboard/app/Selected.jsx`, `dashboard/app/Tabs.jsx`, `dashboard/app/globals.css`, `dashboard/app/Glyph.jsx`,
`dashboard/app/lib/palette.js`, `dashboard/app/lib/format.js`, `dashboard/app/Floor.jsx`, `dashboard/app/Graph.jsx`,
`dashboard/app/CommandBar.jsx`, `dashboard/app/Assets.jsx`, `dashboard/app/MapPanel.jsx`, `dashboard/app/Verdict.jsx`,
`dashboard/app/Actions.jsx`, `dashboard/app/ActLoop.jsx`, `dashboard/app/Fleet.jsx`, `dashboard/app/Tags.jsx`,
`dashboard/app/Edge.jsx`, `dashboard/app/Trends.jsx`, `dashboard/README.md`, `INNOVENT_LOG.md`.

**LOG-081 · 2026-09-23 · Faults leave the console for a shell on the box. Resizable panels. A Grafana trend per asset.**
**Why (operator):** fault controls on the operator console are an afterthought and make the demo less
authentic. A real operator sees only the effects of a fault. The operator also asked for panels that
the operator can resize, a larger verdict, and one Grafana graph of the selected machine in place of
the small sparklines. Status markers take their colors again, as before LOG-080.
**Change:**
- Colors: the status markers (glyphs, map machine lamps, closed-loop PLC links, edge graph nodes, the
  PLC RUN state) are teal again when normal, amber for a warning, and red for an alarm. The other
  LOG-080 changes stay: sparklines, band ranges, card edges, and onboarding bars are gray, and the
  map selection brackets are blue.
- New `deploy/faults.sh`, the fault shell. `ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'`
  opens it in the screen session `visr-faults`, where it stays on standby after Ctrl-A D. Commands:
  `s` (status), `f <n>` (fire), `r <n>` (reset), `r all` (reset every fault owner). The same commands
  run once as arguments. It reads the operator token from the Secret `aiops/visr-auth`, keeps it in
  the process only, and sends the actor `fault-shell`. While the PS0 soak screen `visr-ps0` runs, a
  fire asks for a confirmation, or stops without a terminal unless `FORCE=1`.
- Console: the Fault injection panel, its Reset plant button, and the "Scenario N injected" map banner
  are gone. `FaultInjection.jsx` and the `scenario`, `resetAll`, and `/api/scenarios` polling code of
  `useConsoleData.js` are removed. The api endpoints do not change. `proof-run.sh` and `refusals.sh`
  still use them.
- Layout: the Event log moves under Assets. The Verdict takes the free height of the right column,
  and Actions keeps 320 px. The new `Split.jsx` makes each gap a drag handle: the two column edges,
  Assets and Event log, Map and tabs, Verdict and Actions. A double-click resets that size. The sizes
  stay in the browser (`localStorage` key `visr.layout`).
- Selected tab: the sparklines are gone. It shows the current values, one Grafana frame, and the
  measured SCADA tags. The frame is the new panel 4 "Selected asset" of the `skn-plant` dashboard, with
  the hidden variable `asset` (`var-asset=<name>` in the URL). Its queries match only the series that
  the asset has: draw and temperature with the trip line for a machine, voltage and the feeder meter
  for a rail, flow for the loop, load for a segment.
**Checked:**
- `faults.sh` passes `bash -n`.
- On the box, `status` lists scenarios 0 to 6 with 0 NOW. `fire 9` and an unknown command are refused.
  Nothing was fired, because the soak ran.
- Six of the seven panel-4 queries were run on the box Prometheus (not the segment load) and answer as expected: press-1 draw and trip 78,
  no trip line for conveyor-1, rail psu-a volts, no volts row for press-1, and the cool-1 flow.
- In the dev preview at 1600 x 900: the Event log sits under Assets and the Verdict is taller. A drag
  widens the left column to 472 px and the tabs to 450 px. Both sizes survive a reload, and a
  double-click resets them. The map follows each resize.
- `npm run build` passes.
**Deploy (not done):** push the dashboard image, then `kubectl apply -f deploy/grafana-plant-dashboard.yaml`
(the Grafana sidecar loads panel 4 in about 30 s), then restart the dashboard. Syncthing brings
`faults.sh` to `~/Tata_InnoVent/deploy`.
**Files:** `deploy/faults.sh` (new), `deploy/grafana-plant-dashboard.yaml`, `dashboard/app/Split.jsx` (new),
`dashboard/app/FaultInjection.jsx` (removed), `dashboard/app/Console.jsx`, `dashboard/app/Selected.jsx`,
`dashboard/app/Tabs.jsx`, `dashboard/app/MapPanel.jsx`, `dashboard/app/lib/useConsoleData.js`,
`dashboard/app/Glyph.jsx`, `dashboard/app/Floor.jsx`, `dashboard/app/Graph.jsx`, `dashboard/app/Fleet.jsx`,
`dashboard/app/lib/palette.js`, `dashboard/app/globals.css`, `dashboard/README.md`, `README.md`, `SCENARIOS.md`
(section 7), `POC_SCRIPT.md`, `PIVOT_SETUP.md`, `INNOVENT_PLAN.md`, `BOOK.md`, `INNOVENT_LOG.md`.

**LOG-082 · 2026-09-23 · The Tags tab is a searchable table, one row per tag.**
**Why (operator):** the tag chips in a multi-column grid looked cluttered.
**Change:** `Tags.jsx` shows a search box, the match count (with the count of tags that are not GOOD, in
amber), and the PLC and historian lamps. Below it, a table with one row per tag: quality glyph, tag,
value, quality, and address. The table scrolls under a sticky header. STALE shows in amber and BAD in
red. A derived tag shows "calc" as its address, and the row tooltip holds the formula. The historian
rows/s and total counters are gone from this bar.
**Checked:** dev preview at 1600 x 900, incident mock: 41 rows, 4 not good. A search for "press_1" gives
6 rows. With no search, the table scrolls (883 px of rows in a 214 px window), and the header stays in
place. `npm run build` passes.
**Files:** `dashboard/app/Tags.jsx`, `dashboard/app/globals.css`, `dashboard/README.md`, `INNOVENT_LOG.md`.

**LOG-083 · 2026-09-23 · Edge cards stay pinned in role groups. LOG-080 to LOG-083 deployed on forge.**
**Why (operator):** many Edge cards showed blank gauges, and the cards moved when a state changed.
**Finding:** every workload runs. The blank gauges belong to the observability stack (Prometheus,
Grafana, Loki, kube-state-metrics, the operator, node-exporter). Their Helm charts set no CPU or memory
request or limit, so no share exists. `plc-stamping` has no entry in `/api/pod-resources` at all.
**Change:** `Edge.jsx` groups the cards: AIOps engine (aiops), Plant and SCADA (plant), PLC fleet (fleet),
Observability (observability), then other namespaces. The cards sort by name in each group, and
`useConsoleData.js` no longer sorts them by state. A gauge shows use / limit. With only a request it shows
use / request, amber above 100 % and never red. With neither it shows the absolute use (for example
"531Mi") and the quota line says "no quota". A card with no resource data says so.
**Deploy (11:58 to 11:59, screen `visr-dash`, log `/var/tmp/visr-dash.log`):** the laptop and box files
matched by sha256 (Syncthing). `make push ONLY=dashboard`, `kubectl apply -f deploy/grafana-plant-dashboard.yaml`,
`kubectl -n aiops rollout restart deploy/dashboard`: EXIT 0. The served bundle contains the layout code and no
FaultInjection. Grafana lists panel 4 "Selected asset" and the variable `asset`. The PS0 watcher stayed QUIET
through the restart. The engine, the plant, and the soak were not touched.
**Files:** `dashboard/app/Edge.jsx`, `dashboard/app/lib/useConsoleData.js`, `dashboard/app/globals.css`,
`dashboard/README.md`, `INNOVENT_LOG.md`.

**LOG-084 · 2026-09-23 · Text size control. The Selected trend gets one color per metric. Deployed.**
**Why (operator):** small text is hard to read for an operator. In the Selected trend, every line was
white or teal, which made the metrics hard to tell apart.
**Change:**
- Text size: A, A+, and A++ in the command bar set 100, 115, or 130 %. In `globals.css`, each of the 82
  fixed font sizes is now `calc(<px> * var(--fz))`. `--fz` is 1 at the root and takes the operator
  value (`--txt`) only inside `.pnl-b`. So the text in the panel bodies grows, and the command bar,
  the panel headers, the tab bar, and every panel size stay the same. The choice stays in the browser
  (`localStorage` key `visr.text`). The gauge text in the Edge cards does not scale, because it must
  fit inside the ring.
- Panel 4 of `skn-plant`: draw blue, temp orange, trip red dashed, volts purple, feeder light blue
  dashed, flow green, load yellow, 2 px lines, axis labels in the series color. The current axes have
  a soft minimum of 0, and the voltage axis a soft range of 330 to 400 V. The earlier auto scale spread
  0.3 A or 0.3 V of sensor noise over the full height. Every sample is still drawn. A value outside
  the window stretches the axis, and a Scenario 1 surge (43 to 85 A) or sag (about 17 V) stays clear.
**Checked:**
- In the dev preview at 1600 x 900, a click on A++ leaves the six panel sizes the same (assets
  360 x 594, log 360 x 236, map 808 x 526, tabs 808 x 304, verdict 400 x 510, actions 400 x 320).
  No panel body scrolls sideways. The asset rows grow to 16.25 px, and the panel titles stay 13 px.
- The live panel 4 in the browser pane shows press-1 (blue draw, orange temp, red trip line) and
  psu-a (purple volts steady at 360 V, feeder dashed). Grafana on the box has no image renderer, so the
  frame was read in the browser pane.
- `npm run build` passes. Deploy 12:08 to 12:09 (`make push ONLY=dashboard`, rollout restart): EXIT 0.
  The Grafana ConfigMap was applied three times (colors, the zero axis, the voltage window). The PS0
  watcher stayed QUIET.
**Files:** `dashboard/app/globals.css`, `dashboard/app/CommandBar.jsx`, `dashboard/app/Console.jsx`,
`deploy/grafana-plant-dashboard.yaml`, `dashboard/README.md`, `INNOVENT_LOG.md`.

**LOG-085 · 2026-09-23 · The normal range in the Assets bands is light cyan.**
**Why (operator):** the gray normal range in the rail, loop, and segment bands was hard to see.
**Change:** `.band b` in `globals.css` is teal at 24 % opacity. The marker keeps its state color.
**Deploy:** at about 12:15 forge did not answer for a few minutes: SSH timed out, and `tailscale ping`
got no reply. At 12:18 it answered again, with no reboot (up 1 day 1 h) and the soak QUIET. Then
`make push ONLY=dashboard` and a rollout restart: EXIT 0 at 12:20:36.
**Files:** `dashboard/app/globals.css`, `dashboard/README.md`, `INNOVENT_LOG.md`.

**LOG-086 · 2026-09-23 · "Confidence" is relabeled. A Scenario 1 test shows a recovery tail. Two follow-ups noted.**
**Operator test (12:24 to 12:35, fault shell):** fire PS1 12:24:10, root press-1 12:26:05 (rail, write + rail +
temporal), Execute 12:26:33, relief row 12:27:33, `r all` and Restore 12:29:09. From 12:30 to 12:33 the
verdict showed ROOT CAUSE press-1 again, with "confidence" 1.00, evidence stat + loop, and the chain
press-1 → loop cool-1 → cnc-1. The narrator said to "expect impact on cnc-1 within 15 s". STEADY at 12:34.
**Cause:** the recovery tail of the same incident ("detected in <385 s" is the 12:24 fire). The rail recovers
at once after the reset. The temperatures do not: press-1 ran at 85 A, heated, and warmed the shared loop,
and it cools with tau of about 120 s. For those minutes press-1 really is above its band and really is the
source of the coolant deviation. The fault is not in the detection. The fault is that a recovering incident
looks the same as a live one: red ROOT CAUSE, a forward-looking narrator line, and "confidence" 1.00.
**Change now (dashboard only):** the number under the root is not a probability, so the Verdict no longer
calls it "confidence". With a root edge it says "strength": the link strength, a running average that moves
40 % toward 1 on each pass that sees the link again (EDGE_ALPHA 0.4) and loses 10 % on each pass that does not
(EDGE_DECAY 0.1). Without an edge it says "share": the root's share of the candidate scores
(`ranking.py`), which is 1.00 whenever one candidate is left. The row tooltip explains which. The label
column of the verdict rows now scales with the text size.
**Follow-up 1 (after the recording, needs an engine change, a new soak, and a proof run): a RECOVERING state.**
When the root still deviates but moves back toward its band, or its own driving signal is already back in
band, the verdict shows "RECOVERING <asset>" in a neutral or amber style: no red, no forward-looking impact
line, and no act-loop proposal. Detection does not change.
**Follow-up 2 (think only, no change now): temperature needs its own parameters.**
- The engine uses one parameter set for every signal family (DEV_K, GATE_Q, RESET_WINDOW, ANALYSIS_WINDOW,
  EDGE_ALPHA, EDGE_DECAY). Only the MAD floor is set per family (`MAD_FLOOR_COOLANT_TEMP` 0.3 °C).
- Current, voltage, and speed are fast signals. They follow their cause within seconds, and they return
  within seconds after the cause ends. A deviation from the band is the event.
- Temperature is a first-order lag (tau 90 to 270 s per machine, SCENARIOS 2.9). It integrates heat, lags
  its cause by about tau, and needs about 5 tau (8 to 20 min) to settle. The shared loop couples every cooled
  machine. The quantities that matter are the headroom to the 78 °C trip and the rate toward it, not the
  distance from a band.
- Ideas, each to test on the replay harness first:
  1. Per-family windows scaled to tau: RESET_WINDOW, ANALYSIS_WINDOW, and the lag bounds of the temporal test.
  2. Judge temperature on a model residual: the measured temperature minus the temperature that the
     first-order model expects from the recent heat load (heat_k × current). A load-driven rise and a
     cool-down after a reset both give a residual near zero. A cooling fault (pump, chiller) gives a
     growing residual. This is the fix at the root of the recovery tail above.
  3. Direction-aware gating: a deviation that shrinks toward the band is recovery, not onset (feeds follow-up 1).
  4. A load-dependent baseline: the steady temperature depends on the duty and the speed setpoint.
  5. The asymptote-aware trip forecaster from LOG-071: no card when the ramp settles below the limit.
  6. Alarm hysteresis in the ISA-18.2 sense: a separate off-delay and deadband per family.
- Risk: a slower temperature gate delays the Scenario 5 trip forecasts, which already race the trips.
**Files:** `dashboard/app/Verdict.jsx`, `dashboard/app/globals.css`, `dashboard/README.md`, `POC_SCRIPT.md`, `INNOVENT_LOG.md`.

**LOG-087 · 2026-09-23 · Live console captures replace the mock screenshots in the deck.**
**Why (operator):** the deck showed a preview build on mock data. The operator asked for the slides to use
the latest VISR console, and asked Claude to fire the scenario for the capture.
**Capture method:** the laptop serves the dashboard static export and passes `/api` (GET only) and `/grafana`
through to forge. Headless Chrome, driven over the DevTools protocol from Node, saves a 1920 x 1080 PNG.
No login is used. Grafana does not finish loading in headless Chrome, so the capture opens the Fleet tab
instead of the Selected tab (the Grafana frame would show only a spinner).
**Runs on the box (fault shell, actor fault-shell):**
- 12:58:18 fire PS1. At 12:59:46 the derate proposal is live (88 s). Execute 13:00:57 (press-1 at 74.9 °C).
  The relief row reads press-1 85.4 → 44.9 A. The capture at 13:04 shows root press-1 (strength 1.00), press-1
  at 55 % and 45.0 A, rail A 359.7 V, the holding card, and TRIGGER, EXECUTE, and RELIEF in the ledger.
  Reset all and restore 13:04:58.
- 13:09:30 fire PS1 again for a capture before Execute. The API answered slowly for a moment, and the
  capture stopped at the boot self-check (fleet probe timeout 4 s). Execute 13:12:36 at 77.3 °C, 0.7 °C before
  the trip, and press-1 did not trip. Reset all and restore 13:12:58.
**Deck (local, Design_PPT is gitignored):** `stage2_assets/console_s1_live.png` is the 13:04 capture. The
overview slide (6) and the demo slide (14) use it. The captions and notes say it is a live capture of
23 September after one human-confirmed derate. The honesty note about mock screenshots now says that faults
are fired from a shell on the box and the console has no fault controls. Build: audit 0, notes 600 s and
1,459 words, validator "All validations PASSED". Backups: `stage2_build_v2_0923_prelive.py.bak`,
`SiliconKnights_Tata_VISR_Stage2_v2_0923_prelive.pptx.bak`.
**Files:** `INNOVENT_LOG.md`. Local only: `Design_PPT/stage2_build.py`, `Design_PPT/stage2_assets/console_s1_live.png`.
