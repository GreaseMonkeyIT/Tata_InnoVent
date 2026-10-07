# Soak / stress-test recorder

This harness cycles the PS fault set (SCENARIOS.md: PS1 PS2 PS3 PS4A PS4B PS5) for a few hours. It samples the live
causal verdict every few seconds and builds a **self-contained HTML report** that opens by
double-click.

The harness fakes nothing. Each fault fires through the API scenario route (`POST /api/scenarios/<id>/trigger`),
which perturbs the physics model in the plant sim. The recorder only watches `/api/graph` and writes
down what the engine decided, so a mis-root shows up **as-is**. That is the point: the report shows
a real engine on a live model, not a scripted replay.

## Files

| File | What it does |
|---|---|
| `soak.sh` | The loop: baseline, fire a fault, sample the verdict, reset, cool down. It repeats for `DURATION_H`. |
| `record.py` | Flattens each `/api/graph` snapshot to `samples.jsonl` + `timeline.csv`, and builds `report.html`. |
| `report_template.html` | The report page (dark theme, vanilla SVG, no external libraries, works offline). |
| `/var/tmp/visr-soak/<id>/` (`OUT_ROOT`) | One folder per run on the box: `samples.jsonl`, `timeline.csv`, `meta.json`, `soak.log`, `report.html`. |
| `capture.sh` | One fire with the full engine answer, for slide evidence. It saves every `/api/graph` answer (every 3 s) to `graphs.jsonl`, the graph, narrative, plant and tags at the first finding, and `markers.json` (fire, detection and reset times). Default `SCENARIO=PS4B`. Not for PS4A or PS6. Output: `/var/tmp/visr-capture-<id>-<time>/`. |

## Requirements

Run it **on the box**, where `kubectl` talks to the cluster. It needs **bash, kubectl, curl, and
python3**. It needs no extra packages and no internet.

## Run it

1. Warm the stack first. **PS0 must be silent** (`/api/graph` shows `findings: []`) before you trust
   cycle 1. After a restart or a `deploy/factory-up.sh`, let the PS0 watcher pass (the last 30 lines of
   `/var/tmp/visr-ps0-soak.log` QUIET), then lock the baselines with `deploy/engine-baselines.sh lock`.
   Locked, the faults of this harness cannot teach the engine that a fault is normal (LOG-089). This
   harness fires faults, so it cannot run the PS0 soak itself.
2. Start the soak from the repo root on the box. It finds the api by its ClusterIP and reads the operator
   token from Secret `aiops/visr-auth` (LOG-102), so it needs no port-forward and no pasted token:

   ```bash
   bash soak/soak.sh                  # 3 hours, scenarios PS1 PS2 PS3 PS4A PS4B PS5, sample every 12s
   ```

   For the one-day run, start it in a screen, so it survives the SSH session:

   ```bash
   screen -dmS visr-soak24 bash -c 'DURATION_H=24 bash ~/Tata_InnoVent/soak/soak.sh'
   ```

3. Stop early with **Ctrl-C** (or `screen -S visr-soak24 -X quit`). The script still builds the report
   from the captured samples. At the end it prints the report path, `/var/tmp/visr-soak/<id>/report.html`.
   Copy that file to the laptop to open it.

**One cycle takes about 69 minutes** with the defaults: 6.5 min for each of PS1, PS3, PS4A, PS4B, and PS5,
and 36 min for PS2 (1 min baseline, 10 min fault, 25 min for the loop to cool). A 24 h run gives about 21
cycles. Nothing else may use the plant during the run: no demo, no recording, and no proof run.

`API_BASE` overrides the api address. With no ClusterIP, the script falls back to the kubectl service
proxy (`/api/v1/namespaces/aiops/services/api:8088/proxy/...`), which cannot send the token, so it works
only with auth disabled. If the API enforces auth and no token is found, the preflight check stops the run.

### Knobs (all env vars)

| Var | Default | Meaning |
|---|---|---|
| `DURATION_H` | `3` | Total run length (hours). |
| `SCENARIOS` | `"PS1 PS2 PS3 PS4A PS4B PS5"` | Which scenarios to cycle, in order. Run PS6 on its own (`SCENARIOS=PS6`): it OOM-kills the tag server. |
| `SAMPLE_S` | `12` | Seconds between verdict samples. |
| `BASELINE_S` / `OBSERVE_S` / `COOLDOWN_S` | `60` / `180` / `150` | Watch windows before the fire, during the fault, and after the reset. |
| `NARR_EVERY` | `5` | Capture `/api/narrative` every Nth sample (it is LLM-backed, so the script keeps it sparse). |
| `OBSERVE_<ID>` / `COOLDOWN_<ID>` | `OBSERVE_PS2=600`, `COOLDOWN_PS2=1500`, `OBSERVE_PS6=420`, `COOLDOWN_PS6=240` | Per-id windows. They win over the global values. |
| `EWS` | `1` | Start `rogue-ews` before the PS4A baseline and stop it after the reset (LOG-095). The baseline gives the new pod 60 s to become reachable. `0` leaves it alone. |
| `OUT_ROOT` | `/var/tmp/visr-soak` | Where runs go. Box-local, so a run never lands in the synced repo folder (LOG-102). |
| `API_BASE` | the api ClusterIP | The api address for `curl`. With no ClusterIP, the kubectl service proxy. |
| `VISR_OPERATOR_TOKEN` | Secret `aiops/visr-auth` | The 2E operator token. The script sends it as `X-Auth-Token` on the curl path. |

## Capture one fire for evidence

`soak.sh` keeps a summary row per sample. `capture.sh` keeps the full JSON of one fire. It changes the plant, so the
operator starts it. Do not run it at the same time as another fire.

```bash
screen -dmS visr-cap4b bash -c 'bash ~/Tata_InnoVent/soak/capture.sh > /var/tmp/visr-cap4b.log 2>&1'
```

Knobs: `SCENARIO` (`PS4B`), `BASELINE_S` (60), `OBSERVE_S` (180), `COOLDOWN_S` (150), `POLL_S` (3), `OUT`.

## Rebuild the report from an existing run

The report is a view over the captured data. You can rebuild it at any time, also mid-run:

```bash
python3 soak/record.py report /var/tmp/visr-soak/<id> soak/report_template.html
```

## What the report shows

- **Stat tiles:** duration, cycles, samples, scenarios.
- **Per-scenario outcome:** expected root vs. the actual dominant root, the "correct" rate, and the
  median time-to-detect. PS5 reports whether the trip forecast card fired, and the best ETA seen.
- **Timeline:** a phase ribbon (which fault was active), detection dots (green = root matched the
  expected source, red = mismatch), forecast ticks, and the accepted-edge and finding counts.
- **Notable events:** the first detection per cycle and the forecast cards, latest first.

## Notes

- A fresh deploy needs the engine warm-up (LOG-035) before PS0 is silent. Start the soak after that.
- The first one-day run started on 2026-09-27 at 01:51. A power cut stopped it at 03:45, in cycle 2
  (LOG-106). A full one-day run is still open.
- Every fire and reset lands in the 2E audit ledger with the actor `soak`.
- A run grows over a long run (a few MB of JSONL). It stays in `/var/tmp/visr-soak` on the box. An
  older run in `soak/runs/` is ignored by git.
- The harness only *reads* the verdict and *fires the existing console scenarios*. It changes
  nothing in the engine or the product.
