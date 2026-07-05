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
