# Tata Technologies InnoVent 2026 — Project Plan
**Team SiliconKnights · reskin + reframe of the existing ABB-finalist causal engine**

> **Status:** engine running; **VISR reskin SHIPPED & live on `mark-two`** — full section redesigns + Industry font + Pod circular meters + Grafana PSI (LOG-013/014). Now in the **registration push**.
> **Target category:** §3.2.2.5 **Edge AI for Connected, Secure & Intelligent Industrial Systems** (Industrial Heavy Machinery).
> **Registration deadline:** 2026-07-05 (one-shot form).
> **Working repo:** `ABB_Accelerator_Codex` (full clone, origin `GreaseMonkeyIT/ABB_Accelerator_Proto`) on branch **`mark-two`** — the canonical/runnable copy (working `skctl`/deps). Code flows laptop→desktop via **git** (the synced `ABB_Accelerator_Proto/` clone is degraded — LOG-013).
> **Runs on:** the Linux desktop (K3s, needs real-kernel PSI). Full decision history: `INNOVENT_LOG.md`.

---

## 0. Decisions at a glance
| # | Decision |
|---|---|
| Direction | **Perfect the existing ABB engine** as the entry. The "VISR platform" idea (OS-skin, Advisor/Runtime, board integration) is **dropped**. |
| Vertical/theme | Industrial Heavy Machinery → **§3.2.2.5 Connected, Secure & Intelligent Industrial Systems**. |
| Engine framing | Connected = dependency map · Secure = anomaly/deviation detection · Intelligent = causal root-cause + forecast + narrate. |
| July 5 | **Show the current build as-is** (no hi-fi), reframed + reskinned. **Reskin done** — registration is now deck/demo/form + gates. |
| Repair agents | The **"act" loop** (explain→recommend→**act**) is a **Stage-3 / final-display** feature, not July 5. |
| Data publishing | Other pods emit **simulated, namespace-relevant** signals that **react to scenarios** — **Stage 2**, not built. |
| Reskin | **Restrained Halo Infinite ("VISR") look — clarity is the product, Halo is the accent (~90/10).** **SHIPPED** (LOG-013/014). |
| Font | **Industry** (Fontfabric) — self-hosted via next/font/local (LOG-014). |
| Reskin impl | **Full section redesigns** (Causal Monitor · Pods · Pressure/Grafana · Scenarios · Recommendations), live-wired, shipped on `mark-two`. **No engine/logic changes.** Boot pending. |

---

## 1. What the product is (post-redirect)
An **on-edge brain** that watches a connected set of nodes via kernel signals (PSI/cgroup/eBPF — **no app instrumentation**), and: detects anomalies as **deviation from a learned baseline**, maps **inter-node dependencies**, attributes **causal root cause** (witness + temporal, threshold-free), **forecasts** failures (e.g. OOM), and **narrates** the verdict in plain language. Demonstrated on the K8s "factory" — a simulated connected industrial system. Already an ABB Accelerator 2026 finalist build.

## 2. What we dropped, and why
The **"VISR platform"** (Kubernetes-for-the-physical-edge: unified-node model, a self-aware OS-skin, an Advisor + Runtime portability layer, external dev-board integration). It became infrastructure/middleware, fit **no** InnoVent "Edge AI for X" theme, and over-reached (Karpathy `RULES/`: simplicity first). **The VISR *name* and its restrained HUD *skin* survive as the dashboard reskin; the platform does not.** External-board integration is parked.

## 3. Scope by stage
- **Stage 1 — Registration (→ 2026-07-05):** current build, **reskinned + reframed** to industrial. **Reskin SHIPPED.** Remaining: deck, virtual demo (S0 silent → S1 verdict), Drive folder + college IDs, form — both §8 gates RESOLVED (LOG-016).
- **Stage 2 — Virtual PoC (October 2026):** the **scenario fixes** + **per-pod simulated domain data** (§5) + **secure pass** (login/roles/audit — master plan 2E); richer, more believable demo.
- **Stage 3 — Final display (January 2027):** the **repair agents** (§6) + **hardware ingest** (ESP32/PLC) + physical/functional polish. North star framing = master plan §8 ("VISR OS"); device ladder incl. Raspberry Pi = §9 (parked).
- Judged on: novelty · feasibility · diversity · real-world impact · functional-prototype strength (public InnoVent pages). Partners this edition: Emerson + AWS.
- **→ The step-by-step build plan for Stages 2-3 is `INNOVENT_MASTER_PLAN.md`** (phases 2A-2D, 3A-3E, done-when gates, fallacy guards — LOG-018). Stage 1 is unaffected by it.

## 4. Reskin — design system (restrained "VISR") — SHIPPED
**Principle: clarity is the product; Halo is the accent (~90/10).**
- **Font:** **Industry** (Fontfabric), self-hosted via next/font/local (trial "Test" weights now; licensed swap later).
- **Palette:** dark slate base · **cyan `#36c5e0`** single accent · meaning-colors **red (source) / amber (victim) / teal `#5dcaa5` (healthy)** · calm, non-neon. Aligned to the 3D graph node roles.
- **Sections (built & live-wired):** **Causal Monitor** (3D graph + ROOT CAUSE) · **Pods** (was "Nodes" — status-ranked **card matrix**, circular CPU/MEM/IO meters, % centre, hover use|req|lim mini-table — LOG-017) · **Pressure (PSI)** (Grafana I/O+CPU+mem `d-solo` embeds) · **Scenarios** (Fire/Reset) · **Recommendations** (throttle/resize/reclaim + fairness bar). **Boot — pending.**
- **Implementation:** full section rebuild over `dashboard/` (Next.js static export), wired to the live `/api/*`. **No engine/graph-logic changes.** Deploy = rebuild `skn/dashboard:v0.1` (+import +rollout) **and** `kubectl apply -f deploy/grafana-psi-dashboard.yaml`.

## 5. Data-publishing plan (Stage 2 — planned, not built)
Each factory pod exposes a tiny Prometheus `/metrics` with **simulated, role-relevant** values; add to `aggregator/queries.yaml`; the existing engine + dashboard pick them up.
| Namespace | Example signals |
|---|---|
| factory-core | PLC scan rate/cycle time · MQTT msgs/s + queue depth · relay actuations + latency · interlock state/trips |
| factory-data | DB query rate/IOPS/latency · coolant temp + fan RPM · rack power + PUE · ingest rate + backlog |
| factory-edge | inspection rate + defect % · alerts/s · cache-hit % + OTA pulls · notifications/s · sessions |
**Key design:** the sim values must **react to scenarios** (during S1: DB latency ↑, vision-qc throughput ↓) so the causal graph correlates **domain symptoms**, not just PSI — that's why it pairs with the scenario fixes.

## 6. Repair agents (Stage 3 — final display)
The **third loop step** (BOOK.md §6.5): **explain → recommend → act**. A **closed action vocabulary**; a small fine-tuned orchestrator maps `verdict → one bounded action` + params (cannot invent actions); **cite-or-die** (every action cites causal evidence); **safe because it acts on a known cause**; human-confirm. Machine actions: derate / throttle / cooldown / isolate / raise work-order / safe-mode. Demo on the testbed/sim; advisory/supervisory, **not** in the safety-critical control loop. (The Recommendations section already renders throttle/resize/reclaim — Stage 3 adds the confirmed *execute* path.)

## 7. Engine / ops (restored)
Runs on the Linux desktop under K3s, from `ABB_Accelerator_Codex` on `mark-two`. Restart runbook + storage fix in `INNOVENT_LOG.md` (LOG-008/010). Key: slowdisk PVs need `claimRef` pinning + `/mnt/slowdisk/{tsdb,shared-logs}` to exist; `alloy` CrashLoop is a known, ignorable log-collector issue. Architecture: **L2 aggregator** (`aggregator/`, Go — PromQL pack → `/window`) feeds the **L3 engine** (`correlation/`, Python), exposed via the **L4 API** (`api/main.py`, FastAPI) to the dashboard.

## 8. Outstanding actions / gates
- [x] **ABB reuse cleared** — no NDAs; ABB uses our code as a base, no IP/originality conflict (LOG-016).
- [x] **Female teammate added** (diversity criterion §6.0) (LOG-016).
- [ ] Bake `claimRef` into `deploy/slowdisk.yaml` so the cross-bind never recurs.
- [x] **Single working repo copy** — resolved: canonical = `ABB_Accelerator_Codex` @ `mark-two`; code flows via git (LOG-013).
- [x] **Reskin** — SHIPPED: full section redesigns + Industry font, live on `mark-two` (LOG-013/014). Remaining: the **Boot** section.
- [ ] **Stage-1 deliverables:** deck refresh (VISR shots + §3.2.2.5 reframing), demo recording (S0→S1→verdict/recs→reset), Drive folder + college IDs, the form.
- [ ] Swap trial Industry "Test" weights → licensed files.
- [ ] **Stage 2:** scenario fixes (S2/S3/S5) + per-pod simulated domain data (§5).
- [ ] **Stage 3:** repair agents / act-loop (§6).

## 9. Workflow
Dev on the **laptop (Windows)**; **run on the Linux desktop**; code flows via **git** (branch `mark-two`); decisions persist in **memory** + `INNOVENT_LOG.md`. Follow `RULES/` (Karpathy: think-first · simplicity · surgical · goal-driven).
