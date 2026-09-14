# VISR — Virtual PoC Recording Script (Stage 2D-4)

> **Done-when (master plan):** the video exists, a teammate who didn't build VISR can re-run the
> demo from this script alone, and the box can do it twice in a row without hand-holding.
> **Rule: rehearse the full script twice before recording.** If any beat misfires twice, stop and
> fix — never record around a flake.

**Story in one line:** a calm physics-simulated plant develops a real emergent fault; VISR explains
it — root, mechanism, blast radius, evidence — on the edge box, offline, in seconds; a second fault
shows forecasting; everything state-changing is authenticated and audit-logged.

**Honesty rails (say these on camera, they are the brand):**
- "The plant is **physics-simulated and labeled as such**; the inference is real. The edge box
  itself is watched by the same engine — that plane is real end to end."
- Never call the engine AI/ML. It is deterministic statistical inference behind a witness gate;
  the LLM is a **spokesperson only** — the verdict exists before and without it.
- If the narrator (gemma) is down, the template verdict renders anyway — that is a feature; say so.

---

## 0 · Prep (day before — NOT on camera)

1. Resume the stack: `sudo systemctl enable --now k3s`, wait ~90 s, all pods Ready
   (`kubectl get pods -A`). The box was paused for the gap — see LOG-049.
2. Deploy the images still pending from the local work (LOG-051 to LOG-055): dashboard, api,
   correlation-engine, plant-sim, tag-server, openplc. Create the 2E Secrets FIRST (`PIVOT_SETUP.md` step 5.0).
3. **Baseline soak (LOG-035, non-negotiable):** wipe engine memory → long PS0 soak (hours;
   overnight is best) → baselines matured, PS0 silent. **Never fire PS1 cold after a restart.**
4. Verify the demo set once end-to-end (this is the rehearsal): PS0 silent · PS1 roots press-1
   with `[write,rail,temporal]`-class evidence in a compressor-OFF window · PS5 forecast card
   appears before the trip · reset returns to calm. Capture `kubectl get --raw` service-proxy
   reads if screenshots are wanted for the appendix (LOG-046 pattern).
5. Attach the soak report (`soak/` recorder output) as the evidence appendix of the submission.
6. Screen: 1920×1080, dashboard at 100 % zoom, dark theme, close everything else. Recording
   tool of choice + mic check. IST clock visible in the statusbar is fine (it's honest).

## 1 · Cold open — the secured front door (~40 s)

| Beat | Do | Say |
|---|---|---|
| 1.1 | Incognito browser → `https://<node>:30443` → **login wall** | "Nobody unauthenticated sees this floor — IEC 62443-style zones; TLS; per-role accounts. Aligned to 62443 thinking, not certified — and we say so." |
| 1.2 | Log in as **operator** | "Viewer can look; operator can act. Every action you'll see lands in a tamper-evident audit ledger." |
| 1.3 | **Boot screen** runs its five real probes (engine · telemetry · workloads · causal graph · plant sim), each with its measured round-trip → "all systems nominal" | "Every line is a real probe — nothing on this screen animates unless it actually happened. That honesty rule holds everywhere." (Use `?boot=hold` if you want to talk over it, click to enter.) |

## 2 · The calm plant — PS0 (~60 s)

| Beat | Do | Say |
|---|---|---|
| 2.1 | Causal Monitor, **FLOOR** view: slow orbit of the 3D hall — two lines, two rails, coolant trench | "Eight machines on two power rails and a shared coolant loop. This plant is physics — Ohm's law on the rails, first-order thermal lags on the loop — not scripted traces." |
| 2.2 | Point at the verdict box: **steady** | "The engine has learned each signal's normal band. A calm plant stays silent — no alert fatigue. That took a baseline soak, not a demo switch." |
| 2.3 | Scroll: Machines (live V/A/°C), Pods (the AIOps stack watching ITSELF — plane 2), PSI graphs | "Two planes, one brain: the simulated plant, and the real edge box underneath — same engine watches both. The box plane is real kernel telemetry." |

## 3 · The fault — PS1 rail-sag cascade (~2 min, the hero beat)

| Beat | Do | Say |
|---|---|---|
| 3.1 | Scenarios → **Fire PS1**. (Fire in a compressor-OFF window — watch compressor-1 idle first.) | "I'm injecting one parameter: bearing friction on press-1 rises ×1.9. That's all. Everything downstream must EMERGE from the physics." |
| 3.2 | Floor (~5 s): press-1 draw climbs ~43→85 A, rail-A sags 361→344 V, cnc-1 / qa-scanner throughput slides; conduits light along the fixed wiring | "More friction → more current → the shared rail sags → voltage-sensitive machines degrade. A dozen simultaneous symptoms — this is the alarm-flood moment every SCADA operator knows." |
| 3.3 | Verdict box (~10–15 s): **root = press-1**, confidence, narrative, blast radius; point at the evidence chips | "One verdict, not twelve alarms: press-1, with evidence — write, rail, temporal. The 'rail' chip is the witness gate: no shared physical medium declared, no edge. Correlation alone never convicts here." |
| 3.4 | Toggle **EDGE** view briefly and back | "Same incident from the pod plane — topology genuinely discovered by eBPF. The floor's wiring, by contrast, pre-exists; runtime only weights it. We show each plane the way it really is." |
| 3.5 | Audit section: the PS1 `fired` row, with the verdict it cited | "Who fired what, when, citing which evidence — hash-chained, so an edited history breaks visibly." |

## 4 · The forecast — PS5 coolant degradation (~90 s)

| Beat | Do | Say |
|---|---|---|
| 4.1 | **Reset plant** → floor calms (~5 s; verdict clears in ~10–15 s). Then **Fire PS5** | "Second fault family: the coolant pump degrades. Flow drops; every cooled machine's temperature starts a slow ramp. Watch what the engine does BEFORE anything breaks." |
| 4.2 | Forecast card appears: projected trip in ~N s (coolant temps ramp toward the 78 °C latch) | "It extrapolates the ramp and forecasts the trip — maintenance gets a clock, not a post-mortem." |
| 4.3 | Let the trip land: PLC latches, machine stops (⌀ OPEN on the floor), rail recovers | "The trip is a real interlock in a real IEC 61131-3 runtime — OpenPLC latched it over Modbus. The plant fails safe; the engine explains why it happened." |
| 4.4 | **Reset plant** → calm returns | "And back to steady. Deterministic, reproducible — fire it again and you get the same verdict for the same physics." |

## 5 · Close (~30 s)

Architecture slide or README on screen: "Everything you saw ran on one edge box, fully offline:
K3s, Prometheus, eBPF, the causal engine, the PLC, the narrator model — ~6 GB, no cloud. Detect →
explain → forecast is live today; the audited act loop is Stage 3." Cut.

---

## Fallbacks (pre-decided; do not improvise on camera)

- **Narrator down** → template verdict renders; either say the honesty line or restart Ollama off-camera.
- **PS1 verdict roots compressor-1 with low confidence** → young-baseline / ON-window artifact
  (LOG-046): reset, wait for a compressor-OFF window, fire again. If it repeats, the soak was too
  short — go back to step 0.3; do not record.
- **Plant plane misbehaves entirely** → do not record. Diagnose off-camera, then repeat from step 0.3.
- **Recording quality**: capture beats 3.2–3.5 in one unbroken take; everything else can be cut together.

## Timing budget

~5–6 min total: 0:40 door · 1:00 calm · 2:00 PS1 · 1:30 PS5 · 0:30 close. If it runs long, trim
beat 2.3 and the EDGE toggle (3.4) — never trim the evidence-chip beat (3.3); it is the USP.
