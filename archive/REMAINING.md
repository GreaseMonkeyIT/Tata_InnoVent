# REMAINING — open items after the stateful-engine R&D (LOG-059 → 063)

The L0→L3 engine corrections are **done and cluster-verified**: persistence/ghost (A1),
source attribution + write→staller direction, structural baseline + source/gate hardening,
Route-1 case-merge, and deviation-gated detection. **S0 is silent; S1 roots cooling-monitor.**
This file tracks everything still open so there are no loose ends. Companion to
`BUILD_GUIDE.md` (phase plan) and `DESIGN_stateful_engine_and_case_library.md` (engine proposal).

---

## 1. Engine residual — S1 root stability / victim-cascade case  (KNOWN, deferred, not blocking)

The **live** verdict during a storm pulse is correct: `root = cooling-monitor` via the live
`cooling→timescaledb [write+pvc+temporal]` edge. **Between** pulses the live source edges decay to
the structural floor (`source=memory`, `r≈0`), and a stale psi **victim-cascade** edge
(`dcim→timescaledb`, evidence `stat`) can transiently outrank → an occasional **wrong-direction
case** (`dcim→timescaledb`) gets promoted.

- **Root cause:** `_promote_case` fires on any `root + edges`, including a victim-cascade in passes
  where the source edge isn't live.
- **Fix (proposed, recommended):** promote a case **only from a source-rooted verdict** — require the
  ranked root to have an outgoing source/write-evidenced edge (a real aggressor), not a psi-only
  cascade. S0 silence is already handled by the deviation gate; this only changes *which incidents
  become cases*.
- **Alt:** hold source edges longer / prefer source over `stat`-only memory edges in ranking.
- **Decision owner:** Soumyadip. Do *after* the rest of the build, as agreed.

## 2. Remaining phases

- **P5 — Narrator (the one LLM). ✅ DONE (LOG-066).** `GET /api/narrative` renders `/graph` into one
  operator sentence via local **gemma4** (Ollama at host `OLLAMA_HOST`), grounded to disk I/O with
  source/victim roles, `think:false`, a verdict-signature cache, and an always-on **deterministic
  template fallback** — the demo never depends on the model.
- **P6 — Dashboard. ✅ DONE (LOG-064→066).** Next.js launcher dashboard (nginx static + `/api/` proxy):
  a **3D** force-directed causal graph (red source / amber victims / teal idle; edges coloured by
  contention; particles on live edges), gemma4 verdict, blast radius, scenario console (Fire S1), and
  an **embedded Grafana PSI panel** (`d-solo`). Per-component NodePorts (API 30088 / Grafana 30030 /
  Prometheus 30090 / dashboard 30080), reachable over Tailscale. **Still open:** the **Loki logs
  panel** (pending the `alloy → promtail` fix) — provision Loki as a non-default Grafana datasource then.
- **P7 — Scenarios S2–S5 + rehearsal ledger.** S2 large-file I/O, S3 CPU throttle (no network path),
  S4 network latency + retries, S5 memory leak → OOM. Plus the 20× pass/fail ledger. Double-duty:
  tunes the engine on more fault types AND populates the case library with **multiple families**
  (the real test of the merge + `DEV_K` beyond S1).
- **P8 — Hardening.** Soak; prune unbounded `graph_snapshots` / `case_observations` growth; confirm
  baseline + memory behaviour across pod restarts; demo dry-run.

## 3. Deferred dependencies (P2-red; richer witnesses)

- **eBPF:** Caretta (service map → `ebpf` witness edges), OBI/Beyla (`latency_p95` → the 4th hop to
  critical-control-relay), **Inspektor Gadget** (per-pod block-IO → real disk attribution, replacing
  the static `STORAGE` heuristic and hardening the source attribution). Beyla is up; wire it.
- **Logs:** fix the `alloy` CrashLoop; Loki + Drain3 → a log-error signal.

## 4. Strategy & branches

Working-well-enough engine → **P5 → P6 → P7** → final debug pass → wire deferred deps → next
(learned classifier, time-store). Branches: **mark-zero** = baseline `1d4315a`; **mark-one** =
corrected engine (this R&D); **mark-two** = 14-day time-store. Cut mark-one once the build is
complete and stable (after the from-scratch test passes end-to-end).

## 5. Tuning knobs (env, no rebuild)

`ANALYSIS_WINDOW`, `ENGINE_SIGNAL`, `WRITE_SIGNAL`, `POLL_S`;
`EDGE_ALPHA/DECAY/SHOW/HIDE/PRIOR/FLOOR_FRAC`; `CASE_TAU_MERGE`, `CASE_TAU_FAMILY`;
`BASE_ALPHA`, `DEV_K`, `MAD_FLOOR`, `BASE_MIN_N`. (e.g. if S0 ever blips:
`kubectl set env deploy/correlation-engine -n aiops DEV_K=5`.)
