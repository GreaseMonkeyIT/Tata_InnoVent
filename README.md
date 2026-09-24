# Tata InnoVent 2026: Team SiliconKnights

**VISR** is an edge causal-AIOps brain for industrial systems (category §3.2.2.5, *Edge AI for
Connected, Secure & Intelligent Industrial Systems*). One engine watches two planes. The first is a
**physics-simulated plant floor**: DC rails with source impedance, a shared coolant loop, and a real
OpenPLC trip interlock. Faults perturb the model, and the symptoms *emerge*. The second plane is
**the edge box itself**, through real kernel pressure signals. The engine detects deviation from
learned baselines and attributes root cause across declared shared media (witness + temporal
evidence, threshold-free). It also forecasts failures (OOM and thermal-trip ETAs) and narrates the
verdict. The simulated substrate is labeled as such everywhere. The inference on top of it is real.

## Layout

| Path | What |
|---|---|
| `BOOK.md` | The whole solution end to end, in plain words: the problem, every part, the inspirations, the proof, the plan |
| `plant/` · `plc/` · `scada/` | Plant physics sim, OpenPLC trip program, SCADA tag server + historian writer |
| `vplc/` | Virtual PLC runtime: Structured Text tasks, S7comm and Modbus TCP protocol profiles (2H) |
| `FLEET.md` | Design and interface contract of the virtual PLC fleet and the act loop |
| `SCENARIOS.md` | The PS fault set (PS0 to PS6), its real-incident anchors, and its interface contract |
| `aggregator/` · `correlation/` | L2 telemetry window, L3 deterministic causal engine |
| `api/` · `dashboard/` | L4 API (operator gate + audit ledger), VISR dashboard |
| `deploy/` | K3s manifests, Helm values, `skctl` bootstrap |
| `soak/` | Soak recorder: cycles the PS set and builds an HTML evidence report with a false-positive count |
| `PIVOT_SETUP.md` | Box bring-up runbook (single-node K3s) |
| `POC_SCRIPT.md` | Stage 2 PoC recording script |
| `INNOVENT_PLAN.md` | Current state at a glance |
| `INNOVENT_MASTER_PLAN.md` | The Stage 2/3 build plan (phases, gates, fallacy guards) |
| `INNOVENT_LOG.md` | Append-only decision log (LOG-001 onward), the authoritative history |

## Run it

- **Box bring-up** (single-node K3s): follow `PIVOT_SETUP.md`.
- **Tests**: `make test`, or `python -m pytest -q` inside `correlation/`, `plant/`, `api/`, `scada/`, or `vplc/`.
- **The console** is one screen: Assets and the event log on the left, the map and the detail tabs
  in the center, the verdict and actions on the right. The operator drags the gaps to resize panels.
  `dashboard/README.md` has the map.
- **Fire a fault**: from the fault shell on the box, not the console:
  `ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'`, then `f 1` (fire), `r all` (reset).
  Each scenario is anchored on a real incident:
  PS1 rail-sag cascade · PS2 power sag trips the chiller · PS3 control network storm · PS4A setpoint
  write with no record · PS4B current report contradicts the feeder · PS5 coolant ramp-to-trip ·
  PS6 the monitor runs out of memory. `SCENARIOS.md` is the contract for the set.
- **Show the refusals**: `bash deploy/refusals.sh` on the box. The api refuses a fire without a token
  (401), an execute with a stale proposal (409), and a delete of the base PLC (403), and each refusal
  writes a ledger row.
- **Add a virtual PLC**: console → Fleet tab → Add PLC. Pick a task and a protocol profile, then watch
  the six onboarding phases arrive. A virtual PLC is a protocol profile, not vendor firmware.
- **Act on a verdict**: console → Actions → Execute, then Confirm and execute inside the card. SCADA
  writes one setpoint over the PLC protocol, and the event log records the citation and the measured relief.
