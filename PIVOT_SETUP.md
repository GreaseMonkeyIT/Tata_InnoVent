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

## Resume after a pause (the box already has k3s, the volumes, and the registry)

Two scripts do sections 4 to 7 for an existing install. They run on the box as the normal user.

1. The operator reboots when `/var/run/reboot-required` exists: `sudo reboot`. Never reboot during a soak.
2. The operator runs `bash ~/Tata_InnoVent/deploy/resume.sh`. It writes `registries.yaml`, starts k3s,
   waits for the node, and creates the 2E Secrets (typed passwords) and the 2H Secrets. It is the only
   script that needs sudo. At the end it starts step 3 in the screen session `visr-golive`
   (set `GOLIVE=0` to skip that).
3. `deploy/golive.sh` writes its log to `/var/tmp/visr-golive.log`. To run it by hand:
   `bash ~/Tata_InnoVent/deploy/golive.sh 2>&1 | tee /var/tmp/visr-golive.log`. It removes
   the old-bench leftovers, applies every manifest in order, restarts every Deployment onto the
   registry images, and verifies the front door, the plant, the PLCs, SCADA, the engine, and the api.
   It exits with the number of failed checks. It does not wipe the engine memory.
4. When the engine must learn the plant again (after a long pause, or after a change to the plant, the
   fleet, or the signal set), run `deploy/factory-up.sh` in screen:
   `screen -dmS visr-factory bash -c 'bash ~/Tata_InnoVent/deploy/factory-up.sh > /var/tmp/visr-factory.log 2>&1'`.
   It removes the UI-created PLCs, pushes the images, runs `golive.sh`, backs up and wipes the engine
   memory while the engine is stopped (step 2 below), and starts the PS0 watcher. The watcher writes one
   verdict line per minute to `/var/tmp/visr-ps0-soak.log`. `WIPE=0` keeps the memory. `PUSH=0` skips
   the push.

Over SSH, export `KUBECONFIG=~/.kube/config` first. A non-interactive shell does not set it, and
kubectl then reads the root-only k3s config. Run long jobs under `screen -dmS`, because user linger
is off and `systemd-run --user` jobs stop with the SSH session.

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

`deploy/factory-up.sh` step 4 does the wipe safely: it stops the engine, copies the memory to
`~/visr-backups`, wipes it from a maintenance pod, and starts the engine. Do not wipe during a rolling
restart. The engine keeps its database files open and creates some of them on first use, so the old
pod can write its learned state into the new files. The manual order:

```bash
kubectl -n aiops scale deploy/correlation-engine --replicas=0   # wait until the pod is gone
# start a pod that mounts engine-memory-pvc (the pod in deploy/factory-up.sh step 4), copy the
# files out as a backup, then run: rm -f /var/lib/skn/memory/*.db*
kubectl -n aiops scale deploy/correlation-engine --replicas=1
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

## 4. Build and push the images (local registry, no sudo per deploy)

The box runs a local registry on `127.0.0.1:5000`. Every skn manifest pulls
`localhost:5000/skn/<name>:v0.1` with `imagePullPolicy: Always`. A build, a push, and a rollout
restart deploy a new image, and no step needs sudo.

**4.0 One-time setup.** Run this once, before k3s starts:

```bash
docker volume create visr-registry
docker run -d --restart=always --name visr-registry \
  -p 127.0.0.1:5000:5000 -v visr-registry:/var/lib/registry registry:2
# k3s must pull localhost:5000 over plain HTTP. This file is read when k3s starts.
sudo tee /etc/rancher/k3s/registries.yaml >/dev/null <<'EOF'
mirrors:
  "localhost:5000":
    endpoint:
      - "http://localhost:5000"
EOF
```

**4.1 Build and push.**

```bash
cd "$REPO"
make push                              # all eight images: build, tag, push
make push ONLY="api dashboard"         # only the named images
curl -s http://127.0.0.1:5000/v2/_catalog
# openplc is a SLOW source build (~10-15 min). Skip it when the cached image has the upload fix
# (LOG-036): this prints 1 or more when the fix is present.
docker run --rm --entrypoint grep skn/openplc:v0.1 -c prog_file /entrypoint.sh
# On an existing box, change the openplc image with deploy/openplc-rollout.sh, not make push (LOG-077).
# The script builds on top of the running image in seconds, so the OpenPLC runtime stays the same.
# historian uses the public timescale/timescaledb:latest-pg16. k3s pulls it on first schedule.
```

**4.2 Restart after a push.** The tag stays `v0.1`, so `kubectl apply` rolls a Deployment only when
its manifest changed. Restart the Deployment of each pushed image. The new pod pulls the new push:

```bash
kubectl -n aiops rollout restart deploy/correlation-engine deploy/api deploy/dashboard
kubectl -n plant rollout restart deploy/plant-sim deploy/tag-server
kubectl -n fleet rollout restart deploy/plc-stamping
```

Fallback without the registry: `make import` imports into the k3s containerd with sudo. Then set
the manifests back to `skn/<name>:v0.1` with `imagePullPolicy: IfNotPresent`.

## 5. Deploy the stack

```bash
cd "$REPO"

# 5.0 the 2E Secrets FIRST. The dashboard does not start without them (LOG-053).
#     openssl makes the password hashes, so no apt install and no sudo. The passwords stay out
#     of the shell history and the process list. A re-run is safe, but it makes a NEW operator
#     token: then restart deploy/api and deploy/dashboard together.
kubectl get ns aiops >/dev/null 2>&1 || kubectl create ns aiops
umask 077; D=$(mktemp -d)
read -rsp 'viewer password: ' VP; echo
read -rsp 'operator password: ' OP; echo
printf 'viewer:%s\noperator:%s\n' \
  "$(printf '%s' "$VP" | openssl passwd -6 -stdin)" \
  "$(printf '%s' "$OP" | openssl passwd -6 -stdin)" > "$D/htpasswd"
unset VP OP
openssl rand -hex 24 | tr -d '\n' > "$D/token"
openssl req -x509 -newkey rsa:2048 -nodes -days 730 -subj "/CN=visr.local" \
  -keyout "$D/tls.key" -out "$D/tls.crt" 2>/dev/null
kubectl -n aiops create secret generic visr-auth --from-file=htpasswd="$D/htpasswd" \
  --from-file=operator-token="$D/token" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n aiops create secret tls visr-tls --cert="$D/tls.crt" --key="$D/tls.key" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -rf "$D"

# 5.0b the 2H fleet Secrets (FLEET.md section 12). Random keys, no human passwords.
#      enroll-key signs every PLC token. scada-write-token lets only the api write setpoints.
#      Namespace fleet comes from deploy/fleet.yaml, so create it first if it is missing.
kubectl get ns plant >/dev/null 2>&1 || kubectl create ns plant
kubectl get ns fleet >/dev/null 2>&1 || kubectl create ns fleet
umask 077; D=$(mktemp -d)
openssl rand -hex 32 | tr -d '\n' > "$D/enroll-key"
openssl rand -hex 24 | tr -d '\n' > "$D/scada-write-token"
for ns in aiops plant; do
  kubectl -n "$ns" create secret generic visr-fleet --from-file="$D/enroll-key" \
    --from-file="$D/scada-write-token" --dry-run=client -o yaml | kubectl apply -f -
done
# the base PLC token = HMAC-SHA256(enroll-key, plc name), the same rule the api uses for new PLCs
printf '%s' plc-stamping | openssl dgst -sha256 -hmac "$(cat "$D/enroll-key")" -r | cut -d' ' -f1 \
  | tr -d '\n' > "$D/token"
kubectl -n fleet create secret generic plc-stamping-token --from-file=token="$D/token" \
  --dry-run=client -o yaml | kubectl apply -f -
rm -rf "$D"
# A re-run makes NEW keys. Then restart api, tag-server, and every PLC together.

# 5.0c the historian Secret (LOG-076). The historian and the tag server read the database password
#      from Secret plant/historian-auth. No manifest holds it. The script makes a random password.
#      If the historian already runs, the script changes the password in the database first.
#      deploy/golive.sh runs the same script, and a second run changes nothing.
bash deploy/historian-auth.sh

# 5.0d the OpenPLC web Secret (LOG-077). The OpenPLC web UI login is user openplc with the password
#      from Secret plant/openplc-auth. The script makes a random password when the Secret does not exist.
#      It does not touch the running pod. plc/entrypoint.sh sets the password at each pod start, before
#      the web server starts, so the vendor default login never works. deploy/golive.sh runs the same
#      script, and a second run changes nothing. Read the password for a web UI login:
#      kubectl -n plant get secret openplc-auth -o jsonpath='{.data.password}' | base64 -d; echo
bash deploy/openplc-auth.sh

# 5.1 engine + dashboard via skctl. NO telemetry component on an existing install.
./deploy/skctl up --components engine,language,dashboard
#   - re-applies the aggregator ConfigMap from aggregator/queries.yaml  <- the plant query pack
#   - deploys aggregator + correlation-engine + api + dashboard into aiops
# The telemetry component is for a FRESH install only. skctl does not pin helm chart versions,
# so on an existing install it upgrades kube-prometheus-stack past its CRDs and wipes the
# Prometheus data. The box helm auto-updated to v4 (snap). The helm releases come back with k3s.

# 5.2 the Grafana dashboards (d-solo panels in the console Trends tab; sidecar reloads ~30s):
#     skn-psi = io+cpu+mem pressure · skn-plant = bus voltage / current draw / coolant temps
kubectl apply -f deploy/grafana-psi-dashboard.yaml
kubectl apply -f deploy/grafana-plant-dashboard.yaml

# 5.2b Grafana serves from /grafana/ (LOG-062). The console is HTTPS, and a browser blocks a
#      plain-HTTP frame inside it as mixed content, so nginx proxies /grafana/ on the console's own
#      origin, behind the same login. Use kubectl set env, NOT a helm upgrade. The same values again
#      change nothing. Direct access moves to http://<node>:30030/grafana/.
kubectl -n observability set env deploy/prom-grafana -c grafana \
  GF_SERVER_ROOT_URL='%(protocol)s://%(domain)s:%(http_port)s/grafana/' GF_SERVER_SERVE_FROM_SUB_PATH=true
kubectl -n observability rollout status deploy/prom-grafana --timeout=180s
curl -s http://127.0.0.1:30030/grafana/api/health        # expect "database": "ok"

# 5.3 the plant: namespace, PVCs (bind to the claimRef'd PVs), sim, historian, ServiceMonitor
kubectl apply -f plant/deploy.yaml

# 5.3b the PLC (2F, optional: the sim runs open-loop without it): Modbus :502, web UI :30081
#      on the LAN and Tailscale. Log in as user openplc with the password from step 5.0d (LOG-077).
#      The pod mounts Secret plant/openplc-auth. Without the Secret the trip loop still starts, but
#      nobody can log in. The image has the REST API (port 8443) off. If the headless program upload
#      fails, upload plc/program.st once via the web UI per pod restart. See plc/OPENPLC.md.
#      Check the login and the program: bash deploy/openplc-rollout.sh probe
#      prints "200 302 plant_trips 000" (vendor default refused, Secret login taken, program runs,
#      nothing on port 8443). An image from before LOG-077 gives other words: see step 4.1.
kubectl apply -f deploy/openplc.yaml

# 5.3c the SCADA tag server (2F.2): read-only Modbus client -> tag DB + historian + /tags.
#      It ships WITHOUT a ServiceMonitor on purpose. Apply scada/cutover-servicemonitor.yaml and
#      delete the plant-sim ServiceMonitor ONLY after the tag values match the sim through a
#      PS1 + PS5 run (LOG-055). Rollback = the reverse pair.
kubectl apply -f scada/deploy.yaml

# 5.3d the 2H virtual PLC fleet: namespace fleet, the api Role (fleet only), the base PLC
#      plc-stamping (S7-1200 profile, controls press-1 and press-2), and the /metrics/fleet
#      ServiceMonitor. plant-sim fails OPEN while plc-stamping is absent, so the order is free.
kubectl apply -f deploy/fleet.yaml
kubectl -n fleet rollout status deploy/plc-stamping --timeout=120s
kubectl -n plant exec deploy/tag-server -- python -c \
  "import urllib.request as u; print(u.urlopen('http://127.0.0.1:9300/fleet').read()[:300])"
#   expect plc-stamping with "connected": true and GOOD tags

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
# Notes (the operator console, LOG-062, one screen at 1920x1080 and 100 % zoom):
#  - Assets (left) is the PRIMARY plant view: machines grouped by rail and coolant loop, fed by
#    /api/plant (sim /state proxy). A click opens the machine in the Selected tab: its values, a
#    Grafana trend of that asset only (skn-plant panel 4), and its SCADA tags (/api/tags). The Edge tab holds the pod gauges. The Trends tab holds the Grafana
#    panels through /grafana/ (step 5.2b).
#  - Event log (left, bottom) is the audit ledger. The console has no fault controls (LOG-081).
#    Fire and reset faults from the fault shell on the box: bash ~/Tata_InnoVent/deploy/faults.sh
#    (over SSH: ssh -t forge ...). It calls /api/scenarios/<id>/trigger, /reset, and /reset-all
#    with the operator token. The api sends each id to its owner: plant-sim /fault, rogue-ews
#    (PS4A), or the tag server /chaos (PS6).
#  - the Trends graphs stay blank until step 5.2b has run. The browser console then shows 404s
#    from /grafana/, not a Mixed Content error.
```

## 7. What works now vs what's next (honest state)

| Works after this runbook | Pending (the box session, `POC_SCRIPT.md` step 0) |
|---|---|
| 64Gi/5Gi volumes laid out and **claimRef-pinned** | **Box-verify after the soak (`deploy/proof-run.sh`):** PS0 silent, PS1 roots press-1, Execute relief, PS5 forecast lead, PS2 roots compressor-1 with the loop hop, PS3 roots hmi-gw, PS4A and PS4B integrity findings, PS6 leak card then the blind SCADA view. `deploy/refusals.sh`: 401, 409, 403 with ledger rows. |
| Plant physics live: PS1 to PS6 injectable (SCENARIOS.md), cascades emerge | **OpenPLC box-verify: DONE (LOG-060).** Headless upload compiled, PS5 tripped press-1 and furnace-1, the latch held, one reset cleared it. |
| **2C′ (LOG-033):** rail/loop domain witnesses, `PLANT_SOURCES`, sag inversion, trip forecast. Env baked in `deploy/engine.yaml`. Fixtures green (`correlation/tests/test_plant.py`). | Tag-server stability watch, then the ServiceMonitor cutover pair (LOG-055) |
| **2E front door (LOG-053):** TLS + basic auth, operator token gate, hash-chained audit ledger | **2H box-verify:** plc-stamping connected over S7comm, Add PLC shows six real phases, Load task swaps the cadence, Execute derates press-1 and the relief row lands |
| **2F.2 tag server + tag browser (LOG-055):** tag DB, quality, historian ingest into `plant_tags` | LOG-035 soak (`soak/soak.sh`), then the recording |
| **2G 3D plant floor (LOG-038):** FLOOR/EDGE toggle, N rails, PLC cabinets per cell | |
| **2H virtual PLC fleet (LOG-058, `FLEET.md`):** vPLC runtime, S7comm and Modbus profiles, enrollment, run-time domains | |
| **3D act loop verb 1 (LOG-058):** Execute derate with cite-or-die, action ledger, measured relief | |
| Plant families ENABLED (engine.yaml). Rollback = drop them from `ENGINE_SIGNALS` | |
| PLC trip loop in the sim (closed-loop when OpenPLC answers; trips latch, `/reset` pulses the reset word) | |
| **OpenPLC web login from Secret `plant/openplc-auth`, REST API off (LOG-077):** tested on the box in throwaway containers only | **LOG-077 rollout:** `deploy/openplc-rollout.sh test`, then `deploy`. Never during a soak, a proof run, or a recording. |
