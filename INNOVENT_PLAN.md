# Tata Technologies InnoVent 2026: Project Plan
**Team SiliconKnights · VISR, an edge causal-AIOps brain for industrial systems**

> **Status (2026-09-14):** registration submitted on 2026-07-05 (LOG-050). Stage 2 is code-complete
> locally (LOG-051 to LOG-055). The remaining Stage 2 work is ONE box session: deploy, verify,
> soak, and record per `POC_SCRIPT.md`.
> **Target category:** §3.2.2.5 **Edge AI for Connected, Secure & Intelligent Industrial Systems**.
> **Repo:** `GreaseMonkeyIT/Tata_InnoVent`. One working folder (`Tata InnoVent`) on the laptop.
> **Runs on:** the Linux desktop (single-node K3s, needs real-kernel PSI). Full decision history: `INNOVENT_LOG.md`.

---

## 0. Decisions at a glance
| # | Decision |
|---|---|
| Entry | The team's own causal engine, reframed for industrial systems and reskinned as VISR (LOG-003). |
| Demo substrate | A **physics-simulated plant**: DC rails with source impedance, a shared coolant loop, and 8 assets. Faults perturb the model, and the symptoms emerge (LOG-027 to LOG-029). |
| Scenarios | **PS-series**: PS0 steady plant · PS1 rail-sag cascade · PS2 duty-cycle aggressor · PS5 coolant pump degradation (LOG-028). |
| Industrial data path | Physics → OpenPLC registers → Modbus → SCADA tag server → tag DB + TimescaleDB historian (LOG-055). |
| Secure | TLS + basic auth front door, operator token gate, hash-chained audit ledger (LOG-053). |
| Honesty rail | The engine is deterministic statistical inference behind a witness gate. The LLM narrator is a spokesperson only. Simulated values carry a simulation label. |
| Reskin | Restrained Halo "VISR" look: clarity is the product, and Halo is the accent (~90/10). |
| Repair agents | The act loop (explain → recommend → act) is Stage 3. |

## 1. Scope by stage
- **Stage 1: Registration** (done 2026-07-05): deck, subtitled demo video, form.
- **Stage 2: Virtual PoC** (10 min presentation + 5 min jury Q&A, online): the PoC package is
  `POC_SCRIPT.md`. The official Stage 2 template and rules sit in the local `New_Rules_PPT/` folder.
- **Stage 3: Final demo day** (January 2027): the act loop, the hardware rungs (ESP32, PLC), and the
  finals package. The prototype is due by mid-December 2026.
- The step-by-step build plan for Stages 2 and 3 is `INNOVENT_MASTER_PLAN.md`.

## 2. What runs where
- **Plant plane:** `plant/` (physics sim), `plc/` (OpenPLC trip program), `scada/` (tag server).
- **Engine layers:** `aggregator/` (PromQL pack → `/window`), `correlation/` (the deterministic
  engine), `api/` (FastAPI gateway, operator gate, audit ledger), `dashboard/` (VISR, Next.js static export).
- **Ops:** `deploy/` (manifests, Helm values, `skctl`), `soak/` (evidence recorder), `PIVOT_SETUP.md` (box runbook).

## 3. Reskin: design system (restrained "VISR"), SHIPPED
- **Font:** Industry (Fontfabric), self-hosted via next/font/local. Trial "Test" weights today. The licensed swap is open.
- **Palette:** dark slate base · cyan `#36c5e0` single accent · meaning-colors red (source) / amber (victim) / teal `#5dcaa5` (healthy).
- **Sections:** Boot · Causal Monitor (FLOOR and EDGE views) · Machines (+ SCADA tag browser) · Pods · Scenarios · Recommendations · Audit.

## 4. Repair agents (Stage 3)
The third loop step is **explain → recommend → act**. The system uses a closed action vocabulary,
so it cannot invent actions. Every action cites the causal evidence it acts on (cite-or-die). A
human confirms every action. The layer stays advisory and never enters the safety-critical control
loop. Machine actions: derate / throttle / cooldown / isolate / raise work-order / safe-mode. The
act loop writes into the 2E audit ledger.

## 5. Outstanding actions / gates
- [x] Registration submitted (LOG-050).
- [x] Stage 2 code-complete locally: Boot, 2A truth pass, 2E secure pass, PoC script, 2F tag server (LOG-051 to LOG-055).
- [x] `claimRef` baked into `deploy/slowdisk.yaml` (LOG-054).
- [ ] **Box session:** deploy LOG-051 to LOG-055, 2A/2E box-verify on the PS-series, OpenPLC latch re-confirm, tag-server cutover, LOG-035 soak, and the recording per `POC_SCRIPT.md`.
- [ ] Stage 2 deck on the official template: add a Solution Architecture slide, and back Novelty with prior-art search and benchmarks.
- [ ] Swap the trial Industry "Test" weights for licensed files.
- [ ] **Stage 3:** act loop, ESP32/PLC rungs, finals package.

## 6. Workflow
Development happens on the **laptop (Windows)** in ONE folder: `Tata InnoVent` is the git working
copy and the Syncthing folder. **Git runs on the laptop only.** `.stignore` keeps `.git`,
`node_modules`, and build output out of the sync. The Linux desktop runs the stack from its synced
copy (`~/Tata_InnoVent`). Decisions persist in `INNOVENT_LOG.md`.
