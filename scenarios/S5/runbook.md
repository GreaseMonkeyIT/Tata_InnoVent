# S5 - memory leak -> OOM (forecast before the kernel kills it)

Trigger: `./trigger.sh` -> `kubectl set env deploy/vision-qc LEAK_ENABLED=true` (a real frame-cache
leak, ~5-6 MB/s toward the 512Mi limit). This is the "we told you before the kernel did" beat.

| t | expected |
|---|---|
| +0s | vision-qc restarts with the leak armed; working_set begins climbing |
| +30-60s | the engine forecasts OOM (`incipient` finding) once the pod has its sample window |
| ~+80s | OOMKilled; kubelet restarts the pod |
| after | the leak re-arms each cycle -> the forecast re-fires on every restart |

Mechanism: a memory leak is **self-caused** (source == victim), so it forms NO causal edge. The
engine instead projects `working_set` (the `mem` signal) to the pod's memory limit (`mem_limit`,
kube-state) via `engine/forecast.py`, fitting the slope over the ACTIVE climb (onset-anchored) so the
ETA is realistic, not stretched by the pre-leak baseline.

Expected (box-verified, LOG-085): `/api/graph.incipient` shows
`{pod: vision-qc, class: leak, signal: mem, eta_s: ~N, headroom_frac}` BEFORE the OOMKill; the
dashboard **AI insight feed** renders `forecast · vision-qc · OOM in ~Ns · NN% of limit` (visible even
when a disk incident holds the causal verdict). With NO concurrent causal root, `/api/narrative`
returns the model-free line `"Early warning: vision-qc … projected OOM in ~Ns" (source: forecast)`.

Tune (no rebuild): `FORECAST_HORIZON_S` on the engine (only warn within this window). Note the verdict
shows the forecast in the insight feed regardless of a concurrent S1/S2 disk root (the narrative line
is suppressed while a causal root exists -- by design; the feed is the always-on surface).

Reset: `./reset.sh` (`LEAK_ENABLED=false` + restart); the incipient finding clears once working_set
stops climbing. S0 must stay silent (no leak = no forecast).
