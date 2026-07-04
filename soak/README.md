# Soak / stress-test recorder

A small harness that runs the **real** fault scenarios (S1, S2, S3, S5) back-to-back for a few
hours, samples the live causal verdict onto the SSD every few seconds, and builds a **self-contained
HTML report** you open by double-click — think `powercfg /batteryreport`, but for the causal engine.

Nothing is faked: faults fire through the canonical `scenarios/<id>/trigger.sh` scripts (real `fio`
storms, real CPU bursts, the real OOM-killer). The recorder only watches `/api/graph` and writes
down what the engine decided — so S2's known mis-root and S3's physics gap show up **as-is**. That
honesty is the point: it's evidence of a real system under real load, not a scripted replay.

## What's here

| File | What it does |
|---|---|
| `soak.sh` | The loop: fire each scenario → sample the verdict → reset → cool down, repeated for `DURATION_H`. |
| `record.py` | Flattens each `/api/graph` snapshot to `samples.jsonl` + `timeline.csv`; builds `report.html`. |
| `report_template.html` | The simplified dashboard (Grafana-dark, vanilla SVG, no external libs → works offline). |
| `runs/<timestamp>/` | One folder per run: `samples.jsonl`, `timeline.csv`, `meta.json`, `soak.log`, `report.html`. |

## Requirements

Runs **on the box** (where `kubectl` talks to the cluster). Needs only **bash, kubectl, python3** —
all already present. No extra packages, no internet.

## Run it

```bash
# from the repo root, on the box:
bash soak/soak.sh                  # 3 hours, scenarios S1 S2 S3 S5, sample every 12s
```

Make sure the stack is warm first — **S0 should be silent** (`/api/graph` → `findings: []`) before
you trust the first cycle, or the cold-start noise muddies it (see the main README's warm-up note).

Stop early any time with **Ctrl-C** — the report is still built from whatever was captured. When it
finishes it prints the report path; open `soak/runs/<id>/report.html` in any browser.

### Knobs (all env vars)

| Var | Default | Meaning |
|---|---|---|
| `DURATION_H` | `3` | Total run length (hours). |
| `SCENARIOS` | `"S1 S2 S3 S5"` | Which scenarios to cycle, in order. |
| `SAMPLE_S` | `12` | Seconds between verdict samples. |
| `BASELINE_S` / `OBSERVE_S` / `COOLDOWN_S` | `60` / `180` / `150` | Watch windows before-fire / during-fault / after-reset. |
| `NARR_EVERY` | `5` | Capture `/api/narrative` every Nth sample (it's LLM-backed, so kept sparse). |
| `OUT_ROOT` | `soak/runs` | Where runs are written (point elsewhere to keep them off the synced repo). |
| `API_BASE` | _(unset)_ | If set (e.g. `http://localhost:8088`), fetch with `curl`; otherwise use the `kubectl` service proxy. |

### How it reaches the API

By default it calls the engine API through the **kubectl service proxy** (no port-forward needed):
`/api/v1/namespaces/aiops/services/api:8088/proxy/api/graph`. If you'd rather use a port-forward:

```bash
kubectl port-forward svc/api -n aiops 8088:8088 &
API_BASE=http://localhost:8088 bash soak/soak.sh
```

## Rebuild the report from an existing run

The report is just a view over the captured data — rebuild it any time (e.g. mid-run to peek):

```bash
python3 soak/record.py report soak/runs/<id> soak/report_template.html
```

## What the report shows

- **Stat tiles** — duration, cycles, samples, scenarios.
- **Per-scenario outcome** — expected root vs. actual dominant root, "correct" rate, median
  time-to-detect; S5 reports whether the OOM forecast fired (and the best ETA seen). The known
  S2/S3 limits are labelled inline.
- **Timeline** — a phase ribbon (which fault was active), detection dots (green = root matched
  expected, red = mismatched), OOM-forecast ticks, and the accepted-edge / finding counts over time.
- **Notable events** — first detection per cycle, OOM warnings, latest first.

## Notes

- The faults are real, so a fresh deploy needs the usual ~15–20 min warm-up before S0 is silent;
  start the soak after that.
- `runs/` can grow over a long run (a few MB of JSONL); it lives under the repo working copy, which
  syncs — set `OUT_ROOT=/var/tmp/soak` if you'd rather keep it local to the box.
- This harness only *reads* the verdict and *fires the existing scenarios*; it changes nothing in
  the engine or the product.
