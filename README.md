# Tata InnoVent 2026 — Team SiliconKnights

**VISR** — an edge causal-AIOps brain for industrial systems (category §3.2.2.5, *Edge AI for
Connected, Secure & Intelligent Industrial Systems*). One engine watches two planes: a
**physics-simulated plant floor** (DC rails with source impedance, a shared coolant loop, a real
OpenPLC trip interlock — faults perturb the model and the symptoms *emerge*) and **the edge box
itself** (real kernel pressure signals). It detects deviation from learned baselines, attributes
root cause across declared shared media (witness + temporal evidence, threshold-free), forecasts
failures (OOM and thermal-trip ETAs), and narrates the verdict. The simulated substrate is
labeled as such everywhere; the inference on top of it is real.

## Layout

| Path | What |
|---|---|
| `correlation/` · `plant/` · `plc/` · `api/` · `dashboard/` · `deploy/` | The build — engine, plant sim, PLC, API, VISR dashboard, deploy manifests |
| `INNOVENT_PLAN.md` | Current state at a glance |
| `INNOVENT_MASTER_PLAN.md` | The Stage-2/3 build plan (phases, gates, fallacy guards) |
| `INNOVENT_LOG.md` | Append-only decision log (LOG-001…) — the authoritative history |

## Run it

- **Box bring-up** (single-node k3s): follow `PIVOT_SETUP.md`.
- **Physics invariants**: `python -m pytest plant/tests -q`
- **Engine fixtures**: `python -m pytest correlation/tests -q`
- **Fire a fault**: dashboard → Scenarios → PS1 (rail-sag cascade) · PS5 (coolant ramp-to-trip).