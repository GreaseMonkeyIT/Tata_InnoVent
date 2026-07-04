# PIVOT_SETUP — ground-up bring-up of the Tata InnoVent (plant-physics) build

**What this is.** The complete, copy-pasteable runbook to (1) take the old ABB factory off the
remote box *without harming anything else*, (2) erase and reallocate the 64Gi/5Gi slowdisk
volumes with **claimRef pinning** (cross-bind-proof), and (3) bring up the pivot stack from this
clone: engine layers + the physics-simulated plant. Companion docs: `INNOVENT_MASTER_PLAN.md`
(phases 2B′/2C′) and `plant/sim/main.py` (the emulator).

**What is deliberately NOT touched:** k3s itself, the `observability` stack (Prometheus/Grafana),
`aiops` (engine/api/dashboard), `caretta`. The frozen registration build lives in
`ABB_Accelerator_Codex` and is not modified by anything here.

Conventions: run everything **on the box** unless marked otherwise. `$REPO` = this clone's path
on the box (the Syncthing-synced folder).

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
REPO=~/Sync/Tata\ InnoVent/ABB_Accelerator_Proto     # <- adjust to the box's synced path
cd "$REPO"
chmod +x deploy/skctl appendix/*.sh scenarios/*/*.sh soak/*.sh 2>/dev/null
# belt-and-suspenders: anything that lost its bit
find . -name "*.sh" -exec chmod +x {} + 2>/dev/null
bash -n deploy/skctl && echo "skctl parses OK"
```

If `helm`/`kubectl` complain about permissions: `export KUBECONFIG=~/.kube/config` (never run
helm as root against k3s's config).

## 2. Teardown of the old factory (safe — nothing else is touched)

```bash
# 2.1 remove the factory workloads (helm release "factory" from the old deploy)
helm uninstall factory 2>/dev/null || true

# 2.2 delete the factory namespaces (takes their PVCs with them)
kubectl delete ns factory-core factory-data factory-edge --wait=true

# 2.3 confirm the survivors are healthy — MUST all still be Running
kubectl get pods -n observability
kubectl get pods -n aiops
kubectl get pods -n caretta 2>/dev/null || true

# 2.4 wipe the engine's learned memory (baselines/edges/cases are keyed by the OLD
#     factory workload names — stale state, demo data, safe to clear)
ENGINE_POD=$(kubectl -n aiops get pod -l app=correlation-engine -o name | head -1)
kubectl -n aiops exec "$ENGINE_POD" -- sh -c 'rm -f /var/lib/skn/memory/*.db*' || true
kubectl -n aiops rollout restart deploy/correlation-engine
```

## 3. Erase the 64Gi/5Gi volumes and reallocate them (with claimRef stickiness)

```bash
# 3.1 the old PVs are Retain — released, not deleted, by the namespace teardown
kubectl get pv | grep slowdisk          # expect tsdb-pv-slowdisk + shared-logs-pv-slowdisk (Released)
kubectl delete pv tsdb-pv-slowdisk shared-logs-pv-slowdisk

# 3.2 ERASE the data and lay out the new directories
sudo rm -rf /mnt/slowdisk/tsdb /mnt/slowdisk/shared-logs
sudo mkdir -p /mnt/slowdisk/historian /mnt/slowdisk/plant-shared
df -h /mnt/slowdisk                     # sanity: the HDD is mounted and now ~empty

# 3.3 apply the NEW PVs — claimRef is baked in the manifest, so historian-pv can ONLY
#     bind plant/historian-data and plant-shared-pv can ONLY bind plant/plant-shared.
#     The LOG-008 cross-bind (5Gi claim grabbing the 64Gi volume) is now impossible.
cd "$REPO"
NODE=$(kubectl get node -o jsonpath='{.items[0].metadata.labels.kubernetes\.io/hostname}')
sed "s/<NODE_NAME>/$NODE/g" deploy/slowdisk.yaml | kubectl apply -f -
kubectl get pv                          # both Available, CLAIM column pre-set to plant/...
```

## 4. Build and import the images

```bash
cd "$REPO"
docker build -t skn/aggregator:v0.1         aggregator/
docker build -t skn/correlation-engine:v0.1 correlation/
docker build -t skn/api:v0.1                api/
docker build -t skn/dashboard:v0.1          dashboard/
docker build -t skn/plant-sim:v0.1          plant/
docker build -t skn/openplc:v0.1            plc/     # 2F: SLOW source build (~10-15 min, once); box-verify step

for img in skn/aggregator:v0.1 skn/correlation-engine:v0.1 skn/api:v0.1 skn/dashboard:v0.1 skn/plant-sim:v0.1; do
  docker save $img | sudo k3s ctr images import -
done
sudo k3s ctr images ls | grep skn/      # all five present
# historian uses the public timescale/timescaledb:latest-pg16 — k3s pulls it on first schedule
```

## 5. Deploy the stack

```bash
cd "$REPO"

# 5.1 telemetry + engine + dashboard via skctl (idempotent over an existing observability)
./deploy/skctl up --components telemetry,engine,language,dashboard
#   - installs/upgrades kube-prometheus-stack (+ loki; alloy may fail = known-ignorable)
#   - re-applies the aggregator ConfigMap from aggregator/queries.yaml  <- the PIVOT pack
#   - deploys aggregator + correlation-engine + api + dashboard into aiops

# 5.2 the Grafana dashboards (d-solo panels embedded in VISR; sidecar reloads ~30s):
#     skn-psi = io+cpu+mem pressure · skn-plant = bus voltage / current draw / coolant temps
kubectl apply -f deploy/grafana-psi-dashboard.yaml
kubectl apply -f deploy/grafana-plant-dashboard.yaml

# 5.3 the plant: namespace, PVCs (bind to the claimRef'd PVs), sim, historian, ServiceMonitor
kubectl apply -f plant/deploy.yaml

# 5.3b the PLC (2F, optional — the sim runs open-loop without it): Modbus :502, web UI :30081
#      (login openplc/openplc). If the headless program upload fails, upload plc/program.st
#      once via the web UI per pod restart — see plc/REGISTER_MAP.md + plc/entrypoint.sh.
kubectl apply -f deploy/openplc.yaml

# 5.4 verify storage stuck to the right pods — THE claimRef check
kubectl get pvc -n plant
#   historian-data   Bound   historian-pv-slowdisk      64Gi
#   plant-shared     Bound   plant-shared-pv-slowdisk    5Gi
kubectl get pods -n plant -w            # plant-sim Running, historian-db-0 Running
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

# 6.5 dashboard — full VISR (reskin + Industry font + Pods matrix, synced from Codex 2026-07-02)
echo "http://<box-ip-or-tailscale>:30080"
# Honest interim notes:
#  - the MACHINES section (2026-07-03) is the PRIMARY view: plant assets grouped by rail +
#    coolant loop, V/A/°C/throughput tiles + sparklines + 3 skn-plant Grafana trend embeds,
#    fed by /api/plant (sim /state proxy). The Pods matrix is secondary (aiops+scada hosts).
#  - the Scenarios console is the PS-series now (2026-07-03): PS1/PS2/PS5 Fire/Reset buttons hit
#    /api/scenarios/PS*/trigger -> plant-sim /fault; the S-series is retired to the bench
```

## 7. What works now vs what's next (honest state)

| Works after this runbook | Pending (next code session) |
|---|---|
| Old factory gone; observability/aiops intact | **2F.2 tag server** (SCADA gateway): Modbus poll → ISA-88 tags + quality → TimescaleDB historian ingest + `/metrics` repoint + `/tags` |
| 64Gi/5Gi wiped, reallocated, **claimRef-pinned** | **2G 3D plant floor** (FLOOR/GRAPH toggle; needs 2C′ edges, which now exist) |
| Plant physics live: PS1/PS2/PS5 injectable, cascades emerge | **OpenPLC box-verify**: `skn/openplc` image build + headless program upload (`plc/entrypoint.sh`) — until proven, the sim runs open-loop and everything else is unaffected |
| **2C′ SHIPPED (2026-07-03, LOG-033):** rail/loop domain witnesses, `PLANT_SOURCES`, sag inversion, trip forecast — env baked in `deploy/engine.yaml`; fixtures green (`correlation/tests/test_plant.py`) | Tags UI (per-machine popover + browser) once the tag server exists |
| **PS-series console SHIPPED:** Fire/Reset via `/api/scenarios/PS*` → plant-sim; Machines section (tiles + units + trends) live | 2C′ **box** verification: PS1 → root=press-1 with `rail` evidence chips; PS0 soak silent |
| Plant families ENABLED with the patch (engine.yaml); rollback = drop them from `ENGINE_SIGNALS` | |
| PLC trip loop in the sim (closed-loop when OpenPLC answers; trips latch, `/reset` pulses the reset word) | |

## 8. Rollback (registration insurance)

The frozen `ABB_Accelerator_Codex` deploy can resurrect the old factory at any time: re-apply its
`deploy/slowdisk.yaml` (old dirs recreated by hand), `./deploy/skctl up` from Codex, re-import its
images. Nothing in this runbook forecloses that — the disks are the only shared resource, and
re-purposing them back is the same §3 dance in reverse.
