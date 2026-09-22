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
2. Deploy the images still pending from the local work (LOG-051 to LOG-058): dashboard, api,
   correlation-engine, plant-sim, tag-server, vplc. Create the 2E and 2H Secrets FIRST
   (`PIVOT_SETUP.md` steps 5.0 and 5.0b), then apply `deploy/fleet.yaml` (step 5.3d). The base PLC
   `plc-stamping` must run through the whole soak, so the baselines learn the plant it controls.
   Delete every rehearsal PLC before the soak starts (console → Fleet tab → Remove → Confirm remove).
3. **Baseline soak (LOG-035, non-negotiable):** wipe engine memory → long PS0 soak (hours;
   overnight is best) → baselines matured, PS0 silent. **Never fire PS1 cold after a restart.**
   `deploy/factory-up.sh` does steps 2 and 3 in one run and logs the PS0 verdicts to
   `/var/tmp/visr-ps0-soak.log` (`PIVOT_SETUP.md`, Resume step 4).
4. Verify the demo set once end-to-end (this is the rehearsal): PS0 silent · PS1 roots press-1
   with `[write,rail,temporal]`-class evidence in a compressor-OFF window · PS5 forecast card
   appears before the trip · reset returns to calm. Capture `kubectl get --raw` service-proxy
   reads if screenshots are wanted for the appendix (LOG-046 pattern).
5. Attach the soak report (`soak/` recorder output) as the evidence appendix of the submission.
6. Screen: 1920×1080, the console at 100 % zoom, full screen (F11), close everything else. The
   console fits one screen, so never zoom and never scroll. Recording tool of choice + mic check.
   The IST clock in the command bar is fine (it's honest).
7. Warm the narrator 10 min before recording. On forge, run
   `curl -s http://127.0.0.1:11434/api/generate -d '{"model":"gemma4:e4b-it-qat","prompt":"ready","keep_alive":"60m"}'`.
   A cold model takes more than 15 s on the 4 GB GPU (proof run of 2026-09-19).

## 1 · Cold open — the secured front door (~40 s)

| Beat | Do | Say |
|---|---|---|
| 1.1 | Incognito browser → `https://<node>:30443` → **login wall** | "Nobody unauthenticated sees this floor — IEC 62443-style zones; TLS; per-role accounts. Aligned to 62443 thinking, not certified — and we say so." |
| 1.2 | Log in as **operator** | "Viewer can look; operator can act. Every action you'll see lands in a tamper-evident audit ledger." |
| 1.3 | **Boot screen** runs its six real probes (engine core · telemetry · workloads · causal graph · plant sim · plc fleet), each with its measured round-trip → "all systems nominal" → **enter console** | "Every line is a real probe — nothing on this screen animates unless it actually happened. That honesty rule holds everywhere." (Use `?boot=hold` if you want to talk over it, click to enter.) |

## 2 · The calm plant — PS0 (~60 s)

| Beat | Do | Say |
|---|---|---|
| 2.1 | **Map** (center), FLOOR + ISO: slow drag to rotate the 3D hall (two lines, two rails, the coolant trench), then **PLAN** for the schematic, then **ISO** (a click on the active preset resets the camera) | "Eight machines on two power rails and a shared coolant loop. This plant is physics — Ohm's law on the rails, first-order thermal lags on the loop — not scripted traces." |
| 2.2 | Point at the **Verdict** panel (right column, top): **STEADY** | "The engine has learned each signal's normal band. A calm plant stays silent — no alert fatigue. That took a baseline soak, not a demo switch." |
| 2.3 | No scrolling: point at **Assets** (left column, live V/A/°C), then open the **Edge** tab (the AIOps stack watching ITSELF, plane 2), then the **Trends** tab → edge · psi (PSI graphs) | "Two planes, one brain: the simulated plant, and the real edge box underneath — same engine watches both. The box plane is real kernel telemetry." |

## 2b · The fleet — a virtual PLC comes online (~70 s)

| Beat | Do | Say |
|---|---|---|
| 2b.1 | Open the **Fleet** tab (or click the `plc-stamping` cabinet on the map, which opens the same card): S7-1200 profile, S7comm :102, `virtual` badge, scan time, SCADA RTT | "The stamping line has its own process controller. It speaks real S7comm to our SCADA. It is a virtual PLC with a Siemens protocol profile, not Siemens firmware, and the card says so. OpenPLC stays the separate trip interlock, the way a real plant splits process and safety." |
| 2b.2 | **Add PLC** (the form opens inside the Fleet tab, the map stays in view) → task `packaging-cell`, profile IEC 61131-3 soft PLC, rail `psu-c` → **Create PLC** | "A new packaging cell. The task is IEC 61131-3 Structured Text, the same language a real PLC runs." |
| 2b.3 | Watch the six phases fill in: pod · runtime · enrolled · scada good · engine window | "Every step is a real timestamp: Kubernetes scheduled the pod, the runtime started, the controller enrolled with a signed token, SCADA read good tags over Modbus, and the engine window picked it up. Nothing here runs on a timer." |
| 2b.4 | Map: the new cabinet lamp turns teal on rail C. Assets: the pack machines appear under rail psu-c. Click one: the **Selected** tab shows its `drive` row | "Its machines now draw real simulated current on the spare feeder, commanded by the PLC over the field bus." |

## 3 · The fault — PS1 rail-sag cascade (~2 min, the hero beat)

| Beat | Do | Say |
|---|---|---|
| 3.1 | **Fault injection** (left column, bottom) → **Fire PS1**. (Fire in a compressor-OFF window: watch compressor-1 draw fall in Assets first.) | "I'm injecting one parameter: bearing friction on press-1 rises ×1.9. That's all. Everything downstream must EMERGE from the physics." |
| 3.2 | Map and Assets (~5 s): the amber PS1 banner appears, press-1 draw climbs ~43→85 A, rail psu-a sags 361→344 V, cnc-1 / qa-scanner throughput slides; conduits light along the fixed wiring. At about 20 s a forecast card appears: press-1 trips in about 100 s | "More friction → more current → the shared rail sags → voltage-sensitive machines degrade. A dozen simultaneous symptoms — this is the alarm-flood moment every SCADA operator knows. And the press is heating toward its trip." |
| 3.3 | **Verdict** panel (about 80 to 90 s after the fire): **ROOT CAUSE press-1**, confidence, the chain line (press-1 → rail psu-a → victims), narrative. The map brackets press-1 and the Selected tab opens on it. Point at the evidence chips | "One verdict, not twelve alarms: press-1, with evidence — write, rail, temporal. It waited until the evidence held for most of two minutes, so a normal compressor cycle never convicts. The 'rail' chip is the witness gate: no shared physical medium declared, no edge." |
| 3.4 | Map header: toggle **EDGE** briefly and back to **FLOOR** | "Same incident from the pod plane — topology genuinely discovered by eBPF. The floor's wiring, by contrast, pre-exists; runtime only weights it. We show each plane the way it really is." |
| 3.5 | **Event log** (right column, bottom): the PS1 `fired` row, with the verdict it cited | "Who fired what, when, citing which evidence — hash-chained, so an edited history breaks visibly." |
| 3.6 | **Actions** (right column): the **derate press-1 → 55 % via plc-stamping** card → **Execute** → read the confirmation inside the card (the verdict stays in view above it) → **Confirm and execute** | "Now we act, with a human. One bounded verb, and it cites the verdict it acts on. If the verdict changes before I confirm, the API refuses. SCADA writes one setpoint over S7comm to the stamping PLC." |
| 3.7 | Selected tab (press-1): `drive` falls to 55 %, its amps fall (offline: about 85→45 A). Assets: press-1 shows ▼55 % and rail psu-a climbs back (about 344→360 V). The trip card goes away, and press-1 never trips. After ~60 s the Event log shows the `relief` row with volts before and after | "The relief is measured, not claimed: the ledger records the rail voltage before and after the action. And the trip that was coming never happens." |
| 3.8 | **Reset plant** (Fault injection header), then **Restore** press-1 to 100 % (Actions) | "Reset clears the fault. Restore returns the setpoint, and that is audited too." |

## 4 · The forecast — PS5 coolant degradation (~2.5 min, cut the waits)

Offline with the 2026-09-19 thermal constants: the first cards come at 20 to 30 s, the root chiller-1 at
about 80 s, and the first trip (furnace-1) at about 2 min, 86 s after its own card.


| Beat | Do | Say |
|---|---|---|
| 4.1 | **Reset plant** → the floor calms in ~5 s. The verdict clears in about 3 min while the temperatures recover (cut the wait in the edit). Then **Fire PS5** | "Second fault family: the coolant pump degrades. Flow drops; every cooled machine's temperature starts a slow ramp. Watch what the engine does BEFORE anything breaks." |
| 4.2 | The Verdict names **chiller-1** (the pump) as the root, with a trip ETA per machine and a headroom bar under it (coolant temps ramp toward the 78 °C latch). The map and Assets mark those machines amber | "The cause is the chiller pump, not the hottest machine. And it extrapolates the ramp and forecasts the trip: maintenance gets a clock, not a post-mortem." |
| 4.3 | Let the trip land: PLC latches, machine stops (⌀ OPEN on the map and in Assets), rail recovers | "The trip is a real interlock in a real IEC 61131-3 runtime — OpenPLC latched it over Modbus. The plant fails safe; the engine explains why it happened." |
| 4.4 | **Reset plant** → calm returns | "And back to steady. Deterministic, reproducible — fire it again and you get the same verdict for the same physics." |

## 4b · Secure: a setpoint nobody signed, and the refusals (~75 s, SCENARIOS.md PS4A)

| Beat | Do | Say |
|---|---|---|
| 4b.1 | **Reset plant**, wait for calm (cut in the edit). **Fire PS4A** | "Now an attack, not a fault. A client that is not our SCADA writes one setpoint on the stamping PLC over S7comm, as Stuxnet and FrostyGoop did. S7comm and Modbus have no authentication." |
| 4b.2 | In 35 to 45 s the red **INTEGRITY** band appears: press-1 DERATE 100→30 with no signed ledger row, client rogue-ews. Actions shows **BLOCKED** and an **UNSIGNED** hold. The event log shows a red UNSIGNED row | "VISR does not block the writer. It sees that a setpoint changed with no signed row, it names the client it saw on the wire, and it refuses to act through a controller it cannot trust." |
| 4b.3 | Press **Restore** on the unsigned hold. A signed restore row lands, and the finding clears | "The operator puts it back, and that write is signed." |
| 4b.4 | Log in as `viewer` and press Fire (or run `deploy/refusals.sh` in a terminal): 401, then a stale Execute gets 409. The red rows land in the ledger | "Every refused command lands in the hash-chained ledger." |

## 5 · Close (~30 s)

Architecture slide or README on screen: "Everything you saw ran on one edge box, fully offline:
K3s, Prometheus, eBPF, the causal engine, the PLC, the narrator model, about 6 GB, no cloud. Detect,
explain, forecast, act with a human, and check the controllers against the physics: all live on the
box." Cut.

---

## Fallbacks (pre-decided; do not improvise on camera)

- **Narrator down** → template verdict renders; either say the honesty line or restart Ollama off-camera.
- **PS1 verdict roots compressor-1 with low confidence** → young-baseline / ON-window artifact
  (LOG-046): reset, wait for a compressor-OFF window, fire again. If it repeats, the soak was too
  short — go back to step 0.3; do not record.
- **Plant plane misbehaves entirely** → do not record. Diagnose off-camera, then repeat from step 0.3.
- **Recording quality**: capture beats 3.2–3.5 in one unbroken take; everything else can be cut together.
- **Add PLC stalls in a phase** → the card shows exactly which phase. Do not record around it. Remove
  the PLC off-camera, check `kubectl -n fleet get pods` and the tag server `/fleet`, and retake 2b.
- **No Execute card during PS1** → a proposal needs a root with evidence, plc-stamping connected, and a
  GOOD DERATE tag at 100. Check `/api/actions`. Never write the setpoint by hand on camera.
- **press-1 still derated at PS5** → press **Restore** first. PS5 needs full-speed presses.
- **No INTEGRITY band 60 s after PS4A** → the first finding waits GRACE_S (35 s). Check that
  `/api/integrity` answers `source: live` and that rogue-ews answers `/state`. Reset PS4A before a retake.

## Timing budget

About 10 min raw: 0:40 door · 1:00 calm · 0:40 fleet · 3:30 PS1 with the act beat · 2:30 PS5 ·
1:15 Secure · 0:30 close. Cut the waits in the edit (the 80 s to the PS1 verdict, the PS5 ramp, and
every wait for a verdict to clear) to stay near 8.5 min. Say on camera that the waits are cut.
If it runs long, trim beat 2.3, the EDGE toggle (3.4), and beat 2b.4. Never trim the evidence-chip
beat (3.3) or the Execute beat (3.6): together they are the USP, explain and then act.
