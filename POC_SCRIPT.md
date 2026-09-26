# VISR: demo video plan (Stage 2)

The video introduces the operator console first, then runs one test case, Scenario 1, from the fault
to a measured relief. Grafana and a raw SCADA feed stay on screen beside the console during the test
case. A human narrator speaks the video. The narration script is written after the take, from frames
of the real recording (section 6), so every sentence matches what the screen shows.

**Status (2026-09-26):** not recorded. The take happens only after every fix in the `HANDOFF.md`
to-do is done and deployed. The feed page (`deploy/feed.py`) and the window and capture scripts
(`video/layout.ps1`, `video/record.ps1`) are built (LOG-096).

**Honesty rails for the narrator:**
- The plant is physics-simulated and labeled as such. The inference on top of it is real.
- The engine is deterministic statistical inference behind a witness gate. Do not call it AI or ML.
  The LLM only explains a verdict that exists without it.
- Say "Scenario 1" on camera. The API, the scripts, and `SCENARIOS.md` keep the ID PS1 (LOG-078).
- Faults come from the fault shell on the box, not from the console (LOG-081). The console is the
  operator's view only, as in a real plant.

---

## 1. Before the take (not on camera)

1. **Fixes deployed.** Every item of the `HANDOFF.md` to-do that touches the console, the engine, or
   the narrator is done, tested, and deployed.
2. **Deploy and wipe.** In the forge terminal:
   `screen -dmS visr-factory bash -c 'bash ~/Tata_InnoVent/deploy/factory-up.sh > /var/tmp/visr-factory.log 2>&1'`.
   The script pushes the images, runs `deploy/golive.sh`, backs up and wipes the engine memory, and
   starts the PS0 watcher.
3. **Baseline soak, then lock.** Wait until the last 30 lines of `/var/tmp/visr-ps0-soak.log` are QUIET.
   That takes 2 h at least. Overnight is best. Never fire Scenario 1 on a cold engine. Then lock the
   baselines: `bash ~/Tata_InnoVent/deploy/engine-baselines.sh lock`, so the rehearsals cannot teach
   the engine a fault (LOG-089).
4. **Rehearse twice.** Run the full take twice without recording. If a beat fails twice, stop and fix
   it. Do not record around a flake. Let the verdict go calm after each rehearsal.
5. **Warm the narrator model** 10 min before the take. On forge:
   `curl -s http://127.0.0.1:11434/api/generate -d '{"model":"gemma4:e4b-it-qat","prompt":"ready","keep_alive":"60m"}'`.
   A cold model takes more than 15 s on the 4 GB GPU.
6. **Screen hygiene.** Windows Focus Assist on. Browser bookmarks bar hidden. Open the console and
   Grafana in app or kiosk windows, so no address bar shows the node address. Browser zoom 100 %.
   Type the dashboard password before the capture starts, or cut it in the edit.
7. **Fault shell ready.** In a terminal that is not on screen:
   `ssh -t forge 'bash ~/Tata_InnoVent/deploy/faults.sh'`. Type `s`. Every row must show `-`, and
   row 0 must show `NOW`.

## 2. Screen layouts

The laptop has one 1920×1080 display. The capture records the full display.

| Layout | Used for | Windows |
|---|---|---|
| A | the console tour (section 3.1) | the console, full screen, 1920×1080 |
| B | Scenario 1 (section 3.2) | left: the console, 1280×1080. Top right: Grafana, 640×540. Bottom right: the feed page, 640×540 |

- **Grafana (layout B):** dashboard `skn-plant` in kiosk mode, the last 10 minutes, refresh every 5 s.
  Show the panels "Bus voltage (V), per rail", "Current draw (A), per machine", and "Coolant temps (C),
  vs trip". The console also shows the Grafana panel of the selected asset in its Selected tab.
- **Feed page (`deploy/feed.py`):** read-only, served by a small Python process on forge. It listens on
  localhost only. Once per second it prints one line from the tag server (the SCADA view): press-1
  current, temperature, and `DERATE_PCT` with their quality, and the rail psu-a voltage. It also prints
  each new ledger row: a fault shell fire or reset, an Execute with its write, a measured relief. It
  cannot run commands. It writes every line with its time to `/var/tmp/visr-feed-<start>.log` for the edit.
  - On forge: `screen -dmS visr-feed python3 ~/Tata_InnoVent/deploy/feed.py`
  - On the laptop: `ssh -N -L 8765:127.0.0.1:8765 forge`, then open `http://localhost:8765` as an app window.
- **Window placement (`video/layout.ps1`):** moves the three windows by title, so no dragging shows on
  screen: `powershell -ExecutionPolicy Bypass -File video\layout.ps1 A` (or `B`).

## 3. The take

### 3.1 The console tour (layout A, about 2 min)

| Beat | Do | On screen |
|---|---|---|
| 1 | Open the console in a private window | the login wall (TLS, per-role accounts) |
| 2 | Log in as **operator** | the boot screen runs its six real probes, then "all systems nominal" |
| 3 | Enter the console. **Map**, FLOOR + ISO: drag slowly to rotate the hall. Then **PLAN**, then **ISO** | eight machines, two rails, the coolant trench |
| 4 | Point at the **Verdict** panel | STEADY |
| 5 | **Assets** (left column): live V, A, °C per machine | rails psu-a, psu-b, psu-c, the loop |
| 6 | **Edge** tab | the edge box watched by the same engine, the eBPF traffic map |
| 7 | **Trends** tab → edge · psi | kernel pressure (PSI) graphs |
| 8 | **Fleet** tab | `plc-stamping`: S7-1200 profile, S7comm :102, the `virtual` badge, scan time, SCADA RTT |
| 9 | **Tags** tab: type `PRESS_1` in the search | the SCADA tag table with quality |
| 10 | Click press-1. **Selected** tab | its values, its tags, and its Grafana panel |
| 11 | **Event log** (left column, bottom) | the hash-chained ledger rows |

### 3.2 Scenario 1: the rail-sag cascade (layout B, about 4 min raw)

Fire in a compressor-OFF window: watch the compressor-1 current fall in Assets, then fire. The times
below come from the full-length run of 2026-09-25 (the "+" times count from the fire). The currents,
voltages, and temperatures follow the LOG-100 plant (0.12 ohm rails, the 80 °C press trip), computed from
the model. Recheck all of them in the rehearsal. The rail sag is now about 5 V, so rail psu-a keeps its
normal color on the console. The engine still sees it, because the rail noise is about 0.15 V.

| Beat | Do | On screen | Time |
|---|---|---|---|
| 1 | Switch to layout B | the three windows | 0 |
| 2 | Fault shell (off screen): `f 1` | the feed page shows the fire | +0 s |
| 3 | Wait | press-1 current 42 → 80 A and rail psu-a 387 → 382 V, in the feed, in Grafana, and in Assets | +5 s |
| 4 | Wait | the forecast card: press-1 heads for about 83 °C and its 80 °C trip | about +35 s |
| 5 | Wait | the **Verdict**: root cause press-1, the evidence chips, the chain, the narrator text. The **Actions** card: derate press-1 to 55 % through plc-stamping | about +85 s |
| 6 | **Actions** → **Execute** → read the confirmation → **Confirm** | the feed page shows the write acknowledgement for `DERATE_PCT` = 55 | after beat 5 |
| 7 | Wait | press-1 falls to about 44 A and rail psu-a returns to about 386 V (LOG-100 model). The trip card closes. The Event log shows the `relief` row with volts before and after | about 60 s after beat 6 |
| 8 | Fault shell: `r all`. **Actions** → **Restore** press-1 to 100 % | the reset and the restore rows in the Event log | end |

Record beats 2 to 7 in one unbroken take.

## 4. Recording

- **Capture:** ffmpeg on the laptop records the full display at 30 frames per second into a Matroska
  file, so a crash cannot corrupt the take. `powershell -ExecutionPolicy Bypass -File video\record.ps1
  start take1` starts it (`video\takes\take1.mkv`, gitignored), and `... record.ps1 stop` ends it.
- **Audio:** none. The narrator records the voice after the take.
- **Time marks:** the feed page log and the ledger give the exact time of the fire, the Execute, the
  relief, and the reset. The edit uses them for the cuts and the captions.
- **Remux before the edit:** the capture stops abruptly, so the file has no index. `ffmpeg -i
  take3.mkv -c copy take3_fixed.mkv` writes one without a new encode. Edit the `_fixed` file.
- **A take that Claude drives (2026-09-27, take 3):** Claude clicks the console through the Claude in
  Chrome extension, fires and resets through the fault shell, and places the windows. The operator
  approves the Execute, Confirm, and Restore clicks for that take first.
  - `record.ps1 start take3 -NoCursor` leaves the still mouse pointer out. The extension shows its own
    on-page pointer at each click.
  - `layout.ps1` takes `CONSOLE_TITLE` (for example `[InPrivate]`) when two windows show the console.
    `CONSOLE_TOP` moves the top of a normal browser window above the screen. Windows limits a window
    to about the screen height, so the top strip usually stays, and the edit covers it with a caption band.
  - The capture shows what a screenshot tool hides: a Claude window that stays on top, and the orange
    glow of a screen-control session. Minimize the Claude window, and do not use screen-control tools in
    the turn that records. Edge shows "Claude started debugging this browser" in every window of that
    browser, and the extension draws an orange frame around the controlled window.
  - `reset all` restores DERATE_PCT to 100 by itself, so the Restore click is needed only when the
    operator restores before the reset.
- **Take 3 (2026-09-27, 01:22 to 01:33):** tour, then Scenario 1: fire 7:15, card 8:10 (forecast), root
  press-1 8:42, Execute and Confirm 9:19, relief 9:23 (44 A, peak 75 °C, no trip), reset 10:19. Cut the
  wait for the compressor-OFF window (about 2:00 to 7:15).
- **Optional next take:** layout A only. The console alone stays full screen while Scenario 1 fires,
  with no Grafana and no feed window.

## 5. Edit

1. Cut the login password and every dead second of the tour.
2. Speed up each wait (the 85 s to the verdict, the relief) and keep a visible clock, so the viewer
   sees that time passed. Do not hide a wait.
3. Add a caption at each beat of section 3.2, and a title card and an end card.
4. Export 1920×1080 H.264 MP4. Put the link in the "ADD: demo video link" chip of deck slide 15.

## 6. The narration script

1. After the edit, extract one frame at each beat of section 3 with ffmpeg, at the times from the
   time marks.
2. Write the narration beat by beat from those frames, with the length of each beat in seconds, so the
   words fit the picture. Use the honesty rails at the top of this file.
3. The narrator records the voice to the edited video. Mix it in with ffmpeg.

## 7. Fallbacks (decided before the take)

- **Narrator model down:** the template verdict renders anyway. Restart Ollama off camera, then retake.
- **Scenario 1 roots anything other than press-1:** reset, wait for a compressor-OFF window, and fire
  again. If it repeats, the soak was too short. Go back to step 1.3. Do not record.
- **No Execute card at beat 5:** a proposal needs a root with evidence, plc-stamping connected, and a
  GOOD `DERATE_PCT` tag at 100. Check `/api/actions`. Never write the setpoint by hand on camera.
- **press-1 trips before Execute:** the derate came too late. Reset, let the verdict go calm, retake.
- **The plant plane misbehaves:** do not record. Diagnose off camera, then repeat from step 1.3.
