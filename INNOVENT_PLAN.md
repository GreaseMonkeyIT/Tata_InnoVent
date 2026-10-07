# Tata Technologies InnoVent 2026: Project Plan
**Team SiliconKnights · VISR, an edge causal-AIOps brain for industrial systems**

> **Status (2026-10-03):** Stage 2 is submitted. The deck and the demo video went in on 2026-09-30,
> and the pre-read went to the organizers on 2026-10-02 (LOG-106). The virtual PoC presentation is in
> October 2026, on MS Teams. On 2026-09-28 the stack on forge ran the code of `main` (`7f698cd`): the realism pass
> (LOG-100), four static PLCs, learned display bands (LOG-103), and the floor map of LOG-105. The last
> full test run passed all seven scenarios (2026-09-22, LOG-079). A watch of every scenario on the
> LOG-100 plant followed on 2026-09-26 (LOG-103). Section 5 lists what is open.
> **Target category:** §3.2.2.5 **Edge AI for Connected, Secure & Intelligent Industrial Systems**.
> **Repo:** `GreaseMonkeyIT/Tata_InnoVent`. One working folder (`Tata InnoVent`) on the laptop.
> **Runs on:** the Linux desktop (single-node K3s, needs real-kernel PSI). Full decision history: `INNOVENT_LOG.md`.

---

## 0. Decisions at a glance
| # | Decision |
|---|---|
| Entry | The team's own causal engine, reframed for industrial systems and reskinned as VISR (LOG-003). |
| Demo substrate | A **physics-simulated plant**: DC rails with source impedance, a shared coolant loop, and 8 assets. Faults perturb the model, and the symptoms emerge (LOG-027 to LOG-029). |
| Scenarios | **PS-series** (`SCENARIOS.md`, LOG-068): PS0 steady plant · PS1 rail-sag cascade · PS2 a failed pressure sensor overheats the loop (LOG-100) · PS3 control network storm · PS4A setpoint write with no record · PS4B current report contradicts the feeder · PS5 coolant pump degradation · PS6 the monitor runs out of memory. Each one is anchored on a real incident. |
| Industrial data path | Physics → OpenPLC registers → Modbus → SCADA tag server → tag DB + TimescaleDB historian (LOG-055). |
| Secure | TLS + basic auth front door, operator token gate, hash-chained audit ledger (LOG-053). |
| Honesty rail | The engine is deterministic statistical inference behind a witness gate. The LLM narrator is a spokesperson only. Simulated values carry a simulation label. |
| Reskin | Restrained Halo "VISR" look: clarity is the product, and Halo is the accent (~90/10). |
| Virtual PLC fleet | Soft PLCs with real S7comm and Modbus protocol profiles, Structured Text tasks, signed enrollment, real onboarding phases (LOG-058, `FLEET.md`). |
| Act loop | Shipped for Stage 2: `derate` a machine to 55 %, `stop` compressor-1 (LOG-100), and `restore`. Cite-or-die proposal, human confirm, SCADA setpoint write, measured relief (LOG-058, LOG-091). No action on chiller-1. Advisory suggestions for the rest (LOG-093). More verbs and hardware rungs are Stage 3. |

## 1. Scope by stage
- **Stage 1: Registration** (done 2026-07-05): deck, subtitled demo video, form.
- **Stage 2: Virtual PoC** (10 min presentation + 5 min jury Q&A, online): deck and demo video
  submitted 2026-09-30, pre-read 2026-10-02. `POC_SCRIPT.md` records how the video was made. The
  official Stage 2 template and rules sit in the local `New_Rules_PPT/` folder.
- **Stage 3: Final demo day** (January 2027): the act loop, the hardware rung (a real PLC and a power analyzer), and the
  finals package. The prototype is due by mid-December 2026.
- The step-by-step build plan for Stages 2 and 3 is `INNOVENT_MASTER_PLAN.md`.

## 2. What runs where
- **Plant plane:** `plant/` (physics sim), `plc/` (OpenPLC trip program), `scada/` (tag server).
- **Engine layers:** `aggregator/` (PromQL pack → `/window`), `correlation/` (the deterministic
  engine), `api/` (FastAPI gateway, operator gate, audit ledger), `dashboard/` (VISR, Next.js static export).
- **Ops:** `deploy/` (manifests, Helm values, `skctl`), `soak/` (evidence recorder), `PIVOT_SETUP.md` (box runbook).

## 3. Reskin: design system (restrained "VISR"), SHIPPED
- **Font:** Industry (Fontfabric), self-hosted via next/font/local. Trial "Test" weights today. The licensed swap is open.
- **Palette (LOG-062):** the Stage 2 deck colors, used sparsely on a near-black navy base. Teal `#12C6B3` is the normal state and the accent. Blue `#0000B3` fills operator commands only. Amber `#FF9C00` is warning, red `#F2495C` is alarm, and black `#000000` marks live data wells.
- **Layout (LOG-062):** one static operator console, no page scroll. Boot overlay, then: Assets and Event log (left) · Map with FLOOR/EDGE and 3D/PLAN, plus the Selected, Fleet, Tags, Trends, and Edge tabs (center) · Verdict and Actions (+ inline Execute) (right). The operator resizes panels by dragging the gaps. Faults run from `deploy/faults.sh` on the box, not the console (LOG-081).

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
- [x] Phase 2H virtual PLC fleet + act loop verb 1, local tests green (LOG-058).
- [x] Box session: deploy, box-verify, OpenPLC latch, tag-server cutover (the SCADA read switch, LOG-100), soak, full test run (LOG-060 to LOG-079, LOG-097 to LOG-103).
- [x] Stage 2 deck on the official template, with the Solution Architecture slide, prior art, and the benchmark. Submitted with the demo video on 2026-09-30. Pre-read sent on 2026-10-02 (LOG-106).
- [x] The one-day soak (`soak/soak.sh`, LOG-107): 20 cycles in 24 h on 2026-10-04 and 05. Every fault that fired got the right result, 103 of 103.
- [x] A Scenario 4A-only run with the fixed `soak.sh` (`SCENARIOS=PS4A DURATION_H=1`), 2026-10-05: 9 of 9 fires accepted, `unsigned_write` in every fire, median 49 s (LOG-108).
- [ ] Open from the scenario watch and the soak (LOG-103, LOG-107): a false current-balance finding on the `plc-utilities` compressor AMPS channel in Scenario 3 (15 of 19 soak cycles), the last root that stays after a reset (after Scenario 1, a median of 138 s), a chiller-1 root that stays after Scenarios 2 and 5 (cleared within 450 s in 3 of 19 cycles), trip cards on cnc-1 (Scenario 5) and press-1 (Scenario 4B) that settle just under the trip, and derate proposals for every cooled machine in Scenarios 2 and 5.
- [ ] Scenario 7 (supply dip): the explained-load test in the engine source path (SCENARIOS.md 4.5).
- [x] PIVOT_SETUP 5.2c on forge: Prometheus keeps 30 days on the slow disk, and the Prometheus and Grafana NodePorts are closed (2026-10-03, LOG-106).
- [ ] The API auth Secrets are still `optional: true` (FIELD_RESEARCH S5): make the api fail closed.
- [ ] Swap the trial Industry "Test" weights for licensed files.
- [ ] **Stage 3:** act loop, the hardware rung (a real PLC and a power analyzer), finals package.
- [ ] **Long-horizon learning.** This needs the history of a real plant. The simulated plant has no wear.
  - Learn baselines per operating mode (shift, product, load) from the historian.
  - Detect slow drift per asset, for example motor current at the same load that rises week after week.
  - Match a new incident to a past incident in the case library.
  - Add learned forecast models as witnesses. Retrain them on a schedule, version them, and freeze them
    between retrains. Check each version before it goes live. The deterministic engine keeps the verdict.
- [ ] **Plant model without real plant data.** Evaluate a LabVIEW (NI) plant model that uses the technical
  data of real equipment. Validate it against the traces of the hardware rung.

## 6. Workflow
Development happens on the **laptop (Windows)** in ONE folder: `Tata InnoVent` is the git working
copy and the Syncthing folder. **Git runs on the laptop only.** `.stignore` keeps `.git`,
`node_modules`, and build output out of the sync. The Linux desktop runs the stack from its synced
copy (`~/Tata_InnoVent`). Decisions persist in `INNOVENT_LOG.md`.
