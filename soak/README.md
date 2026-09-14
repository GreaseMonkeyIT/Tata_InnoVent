# Soak / stress-test recorder

This harness cycles the PS-series plant faults (PS1, PS2, PS5) for a few hours. It samples the live
causal verdict every few seconds and builds a **self-contained HTML report** that opens by
double-click.

The harness fakes nothing. Each fault fires through the API console route (`POST /api/scenarios/<id>/trigger`),
which perturbs the physics model in the plant sim. The recorder only watches `/api/graph` and writes
down what the engine decided, so a mis-root shows up **as-is**. That is the point: the report shows
a real engine on a live model, not a scripted replay.

## Files

| File | What it does |
|---|---|
| `soak.sh` | The loop: baseline, fire a fault, sample the verdict, reset, cool down. It repeats for `DURATION_H`. |
| `record.py` | Flattens each `/api/graph` snapshot to `samples.jsonl` + `timeline.csv`, and builds `report.html`. |
| `report_template.html` | The report page (dark theme, vanilla SVG, no external libraries, works offline). |
| `runs/<timestamp>/` | One folder per run: `samples.jsonl`, `timeline.csv`, `meta.json`, `soak.log`, `report.html`. |

## Requirements

Run it **on the box**, where `kubectl` talks to the cluster. It needs **bash, kubectl, curl, and
python3**. It needs no extra packages and no internet.

## Run it

1. Warm the stack first. **PS0 must be silent** (`/api/graph` shows `findings: []`) before you trust
   cycle 1. After a restart, run the long PS0 soak from `POC_SCRIPT.md` step 0.3.
2. If the API enforces 2E auth, forward the API port and export the operator token:

   ```bash
   kubectl port-forward svc/api -n aiops 8088:8088 &
   export API_BASE=http://localhost:8088
   export VISR_OPERATOR_TOKEN=<operator-token from the visr-auth Secret>
   ```

3. Start the soak from the repo root:

   ```bash
   bash soak/soak.sh                  # 3 hours, scenarios PS1 PS2 PS5, sample every 12s
   ```

4. Stop early with **Ctrl-C** if you need to. The script still builds the report from the captured
   samples. At the end it prints the report path. Open `soak/runs/<id>/report.html` in a browser.

Without `API_BASE`, the script uses the **kubectl service proxy**
(`/api/v1/namespaces/aiops/services/api:8088/proxy/...`). That path cannot send the operator token,
so it works only with auth disabled. If the API enforces auth and no token is set, the preflight
check stops the run.

### Knobs (all env vars)

| Var | Default | Meaning |
|---|---|---|
| `DURATION_H` | `3` | Total run length (hours). |
| `SCENARIOS` | `"PS1 PS2 PS5"` | Which scenarios to cycle, in order. |
| `SAMPLE_S` | `12` | Seconds between verdict samples. |
| `BASELINE_S` / `OBSERVE_S` / `COOLDOWN_S` | `60` / `180` / `150` | Watch windows before the fire, during the fault, and after the reset. |
| `NARR_EVERY` | `5` | Capture `/api/narrative` every Nth sample (it is LLM-backed, so the script keeps it sparse). |
| `OUT_ROOT` | `soak/runs` | Where runs go. Point it elsewhere to keep runs off the synced folder. |
| `API_BASE` | _(unset)_ | If set (for example `http://localhost:8088`), the script uses `curl`. Otherwise it uses the kubectl service proxy. |
| `VISR_OPERATOR_TOKEN` | _(unset)_ | The 2E operator token. The script sends it as `X-Auth-Token` on the curl path. |

## Rebuild the report from an existing run

The report is a view over the captured data. You can rebuild it at any time, also mid-run:

```bash
python3 soak/record.py report soak/runs/<id> soak/report_template.html
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
- Every fire and reset lands in the 2E audit ledger with the actor `soak`.
- `runs/` grows over a long run (a few MB of JSONL). It lives in the synced folder. Set
  `OUT_ROOT=/var/tmp/soak` to keep runs local to the box.
- The harness only *reads* the verdict and *fires the existing console scenarios*. It changes
  nothing in the engine or the product.
