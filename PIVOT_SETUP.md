# PIVOT_SETUP: ground-up bring-up of the Tata InnoVent (plant-physics) build

**What this is.** The copy-pasteable runbook to (1) prepare the 64Gi/5Gi slowdisk volumes with
**claimRef pinning** (cross-bind-proof) and (2) bring up the VISR stack from this repo: engine
layers, the physics-simulated plant, the OpenPLC trip interlock, and the SCADA tag server.
Companion docs: `INNOVENT_MASTER_PLAN.md` (phases 2B′/2C′/2F) and `plant/sim/main.py` (the emulator).

**Re-runs are safe.** `skctl up` and `kubectl apply` are idempotent over an existing install.

Conventions: run everything **on the box** unless marked otherwise. `$REPO` = this repo's path on
the box (the Syncthing-synced `Tata_InnoVent` folder). Syncthing does not carry `.git`,
`node_modules`, or build output. Git runs on the laptop only.

---

## 0. One-time prerequisites (skip what already exists — the box has all of these)

```bash
# k3s (single node, containerd)
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get node                      # sanity
mkdir -p ~/.kube && sudo k3s kubectl config view --raw > ~/.kube/config
export KUBECONFIG=~/.kube/config

# helm
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# docker (for image builds; k3s runs containerd, we import into it)
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER

# ollama + the narrator model (optional — /api/narrative falls back to a template without it)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4:e4b-it-qat
```

## 1. Repo prep (fixes the "could not run ./deploy/skctl" problem)

Syncthing from Windows strips execute bits. Restore them every time the tree is freshly synced:

```bash
REPO=~/Tata_InnoVent     # <- the box's synced path
cd "$REPO"
chmod +x deploy/skctl soak/*.sh plc/*.sh 2>/dev/null
# belt-and-suspenders: anything that lost its bit
find . -name "*.sh" -exec chmod +x {} + 2>/dev/null
bash -n deploy/skctl && echo "skctl parses OK"
```

If `helm`/`kubectl` complain about permissions: `export KUBECONFIG=~/.kube/config` (never run
helm as root against k3s's config).

## 2. Fresh engine memory (only when the baselines must re-learn)

The engine keeps learned baselines, edges, and cases in its memory DB. Wipe it after a change to
the signal set or after a long pause. Then run the LOG-035 soak before any demo.

```bash
ENGINE_POD=$(kubectl -n aiops get pod -l app=correlation-engine -o name | head -1)
kubectl -n aiops exec "$ENGINE_POD" -- sh -c 'rm -f /var/lib/skn/memory/*.db*' || true
kubectl -n aiops rollout restart deploy/correlation-engine
```

## 3. Prepare the 64Gi/5Gi volumes (with claimRef stickiness)

```bash
# 3.1 lay out the directories on the slow HDD
sudo mkdir -p /mnt/slowdisk/historian /mnt/slowdisk/plant-shared
df -h /mnt/slowdisk                     # sanity: the HDD is mounted

# 3.2 apply the PVs. claimRef is baked in the manifest, so historian-pv can ONLY
#     bind plant/historian-data and plant-shared-pv can ONLY bind plant/plant-shared.
#     The LOG-008 cross-bind (5Gi claim grabbing the 64Gi volume) is impossible.
cd "$REPO"
NODE=$(kubectl get node -o jsonpath='{.items[0].metadata.labels.kubernetes\.io/hostname}')
sed "s/<NODE_NAME>/$NODE/g" deploy/slowdisk.yaml | kubectl apply -f -
kubectl get pv                          # CLAIM column pre-set to plant/...
```

## 4. Build and import the images

```bash
cd "$REPO"
docker build -t skn/aggregator:v0.1         aggregator/
docker build -t skn/correlation-engine:v0.1 correlation/
docker build -t skn/api:v0.1                api/
docker build -t skn/dashboard:v0.1          dashboard/
docker build -t skn/plant-sim:v0.1          plant/
docker build -t skn/tag-server:v0.1         scada/   # 2F.2 SCADA tag server
docker build -t skn/openplc:v0.1            plc/     # 2F: SLOW source build (~10-15 min, once)

for img in skn/aggregator:v0.1 skn/correlation-engine:v0.1 skn/api:v0.1 skn/dashboard:v0.1 \
           skn/plant-sim:v0.1 skn/tag-server:v0.1 skn/openplc:v0.1; do
  docker save $img | sudo k3s ctr images import -
done
sudo k3s ctr images ls | grep skn/      # all seven present
# historian uses the public timescale/timescaledb:latest-pg16. k3s pulls it on first schedule.
```

`make import` runs the same builds and imports in one command.

## 5. Deploy the stack

```bash
cd "$REPO"

# 5.0 the 2E Secrets FIRST. The dashboard readiness probe fails closed without them (LOG-053).
kubectl get ns aiops >/dev/null 2>&1 || kubectl create ns aiops
sudo apt-get install -y apache2-utils   # htpasswd (once)
htpasswd -nbB viewer   '<viewer-pass>'   > /tmp/htpasswd
htpasswd -nbB operator '<operator-pass>' >> /tmp/htpasswd
TOKEN=$(openssl rand -hex 24)
openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=visr.local" \
  -keyout /tmp/tls.key -out /tmp/tls.crt
kubectl -n aiops create secret generic visr-auth \
  --from-file=htpasswd=/tmp/htpasswd --from-literal=operator-token="$TOKEN"
kubectl -n aiops create secret tls visr-tls --cert=/tmp/tls.crt --key=/tmp/tls.key
rm /tmp/htpasswd /tmp/tls.key /tmp/tls.crt

# 5.1 telemetry + engine + dashboard via skctl (idempotent over an existing observability)
./deploy/skctl up --components telemetry,engine,language,dashboard
#   - installs/upgrades kube-prometheus-stack (+ loki; alloy may fail = known-ignorable) + caretta
#   - re-applies the aggregator ConfigMap from aggregator/queries.yaml  <- the plant query pack
#   - deploys aggregator + correlation-engine + api + dashboard into aiops

# 5.2 the Grafana dashboards (d-solo panels embedded in VISR; sidecar reloads ~30s):
#     skn-psi = io+cpu+mem pressure · skn-plant = bus voltage / current draw / coolant temps
kubectl apply -f deploy/grafana-psi-dashboard.yaml
kubectl apply -f deploy/grafana-plant-dashboard.yaml

# 5.3 the plant: namespace, PVCs (bind to the claimRef'd PVs), sim, historian, ServiceMonitor
kubectl apply -f plant/deploy.yaml

# 5.3b the PLC (2F, optional: the sim runs open-loop without it): Modbus :502, web UI :30081
#      (login openplc/openplc). If the headless program upload fails, upload plc/program.st
#      once via the web UI per pod restart. See plc/REGISTER_MAP.md + plc/entrypoint.sh.
kubectl apply -f deploy/openplc.yaml

# 5.3c the SCADA tag server (2F.2): read-only Modbus client -> tag DB + historian + /tags.
#      It ships WITHOUT a ServiceMonitor on purpose. Apply scada/cutover-servicemonitor.yaml and
#      delete the plant-sim ServiceMonitor ONLY after the tag values match the sim through a
#      PS1 + PS5 run (LOG-055). Rollback = the reverse pair.
kubectl apply -f scada/deploy.yaml

# 5.4 verify storage stuck to the right pods: THE claimRef check
kubectl get pvc -n plant
#   historian-data   Bound   historian-pv-slowdisk      64Gi
#   plant-shared     Bound   plant-shared-pv-slowdisk    5Gi
kubectl get pods -n plant -w            # plant-sim, historian-db-0, openplc, tag-server Running
```

## 6. Verify end-to-end (plant physics → Prometheus → aggregator window)

```bash
# 6.1 the sim speaks
kubectl -n plant port-forward svc/plant-sim 9200:9200 &
curl -s localhost:9200/healthz
curl -s localhost:9200/metrics | grep -E "plant_(bus_voltage|current_draw)" | head
curl -s localhost:9200/state | head -30

# 6.2 Prometheus scrapes it (ServiceMonitor picked up; target "plant-sim" Up)
kubectl -n observability port-forward svc/prom-kube-prometheus-stack-prometheus 9090:9090 &
curl -s 'localhost:9090/api/v1/query?query=plant_bus_voltage_volts' | head -c 400; echo

# 6.3 the aggregator window carries plant keys (plane 2) AND aiops psi keys (plane 1)
kubectl -n aiops port-forward svc/aggregator 9000:9000 &
curl -s localhost:9000/window | python3 -c "import json,sys; ks=list(json.load(sys.stdin)); \
print(len(ks),'keys'); print('\n'.join(k for k in ks if k.startswith('plant/'))[:600])"

# 6.4 fire the first physics fault and WATCH the cascade emerge (no engine needed yet)
curl -s -X POST localhost:9200/fault/PS1        # press-1 bearing friction
sleep 20 && curl -s localhost:9200/state        # press-1 amps UP, rail psu-a volts DOWN,
                                                # cnc-1/qa-scanner-1 throughput sliding
curl -s -X POST localhost:9200/reset

# 6.5 dashboard (VISR)
echo "https://<box-ip-or-tailscale>:30443"     # 30080 redirects here. Log in as viewer or operator.
# Notes:
#  - the MACHINES section is the PRIMARY view: plant assets grouped by rail + coolant loop,
#    V/A/°C/throughput tiles + sparklines + 3 skn-plant Grafana trend embeds, fed by /api/plant
#    (sim /state proxy), plus the SCADA tag browser (/api/tags). The Pods matrix is secondary.
#  - the Scenarios console is the PS-series: PS1/PS2/PS5 Fire/Reset buttons hit
#    /api/scenarios/PS*/trigger -> plant-sim /fault (operator login when 2E auth is enforced)
```

## 7. What works now vs what's next (honest state)

| Works after this runbook | Pending (the box session, `POC_SCRIPT.md` step 0) |
|---|---|
| 64Gi/5Gi volumes laid out and **claimRef-pinned** | **2A/2E box-verify:** PS0 silent, PS1 roots press-1, PS2 roots compressor-1, PS5 forecast card. Login wall, 401 without a token, audit rows. |
| Plant physics live: PS1/PS2/PS5 injectable, cascades emerge | **OpenPLC box-verify:** headless program upload (`plc/entrypoint.sh`) and the trip latch re-confirm. Until then the sim runs open-loop. |
| **2C′ (LOG-033):** rail/loop domain witnesses, `PLANT_SOURCES`, sag inversion, trip forecast. Env baked in `deploy/engine.yaml`. Fixtures green (`correlation/tests/test_plant.py`). | Tag-server stability watch, then the ServiceMonitor cutover pair (LOG-055) |
| **2E front door (LOG-053):** TLS + basic auth, operator token gate, hash-chained audit ledger | LOG-035 soak (`soak/soak.sh`), then the recording |
| **2F.2 tag server + tag browser (LOG-055):** tag DB, quality, historian ingest into `plant_tags` | **2G 3D plant floor** (FLOOR/GRAPH toggle) |
| Plant families ENABLED (engine.yaml). Rollback = drop them from `ENGINE_SIGNALS` | |
| PLC trip loop in the sim (closed-loop when OpenPLC answers; trips latch, `/reset` pulses the reset word) | |
