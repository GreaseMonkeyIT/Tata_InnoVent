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
| `plant/` · `plc/` · `scada/` | Plant physics sim, OpenPLC trip program, SCADA tag server + historian writer |
| `aggregator/` · `correlation/` | L2 telemetry window, L3 deterministic causal engine |
| `api/` · `dashboard/` | L4 API (operator gate + audit ledger), VISR dashboard |
| `deploy/` | K3s manifests, Helm values, `skctl` bootstrap |
| `soak/` | Soak recorder: cycles PS1/PS2/PS5 and builds an HTML evidence report |
| `PIVOT_SETUP.md` | Box bring-up runbook (single-node K3s) |
| `POC_SCRIPT.md` | Stage 2 PoC recording script |
| `INNOVENT_PLAN.md` | Current state at a glance |
| `INNOVENT_MASTER_PLAN.md` | The Stage 2/3 build plan (phases, gates, fallacy guards) |
| `INNOVENT_LOG.md` | Append-only decision log (LOG-001 onward), the authoritative history |

## Run it

- **Box bring-up** (single-node K3s): follow `PIVOT_SETUP.md`.
- **Tests**: `make test`, or `python -m pytest -q` inside `correlation/`, `plant/`, `api/`, or `scada/`.
- **Fire a fault**: dashboard → Scenarios → PS1 (rail-sag cascade) · PS2 (duty-cycle aggressor) ·
  PS5 (coolant ramp-to-trip).
