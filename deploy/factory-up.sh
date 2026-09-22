#!/usr/bin/env bash
# factory-up.sh: bring the plant up clean on the box, then start the quiet PS0 soak. No sudo.
# Use it when the stack is live (deploy/golive.sh passed once) and the engine must learn the plant
# again: after a change to the plant, the fleet, or the signal set, or after a long pause.
#
#   screen -dmS visr-factory bash -c 'bash ~/Tata_InnoVent/deploy/factory-up.sh > /var/tmp/visr-factory.log 2>&1'
#
# Steps:
#   1. Remove every UI-created PLC through the api: the Deployment, the SCADA registration, the plant
#      cell, and an audit row. The soak runs with the base PLC plc-stamping only.
#   2. Push the images in ONLY (default: the six images that the PS2-PS6 build changed). PUSH=0 skips it.
#   2b. Upgrade the alloy log shipper with deploy/values/alloy.yaml at its installed chart version, so
#      that no pod in a watched namespace restarts in a loop during the soak. ALLOY=0 skips it.
#      A failure gives a WARN line only.
#   3. Run deploy/golive.sh: apply, restart, verify. The script stops when a check fails.
#   4. Stop the engine, back up its memory to ~/visr-backups, wipe the memory, start the engine.
#      WIPE=0 skips this step. The script never wipes without a checked backup.
#   5. Start the PS0 watcher in the screen session visr-ps0. It writes one read-only verdict line per
#      minute to /var/tmp/visr-ps0-soak.log for WATCH_H hours (default 24). It never fires a fault.
#      QUIET means no finding, no root, no forecast card, and no open integrity finding.
#      An old soak log moves to <log>.<timestamp> first, so the log holds one soak only.
#
# Restore a backup: scale deploy/correlation-engine to 0, start a pod like the one in step 4, extract
# the tar into /var/lib/skn/memory, delete the pod, then scale the engine to 1.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
ONLY=${ONLY:-"vplc api dashboard tag-server plant-sim correlation-engine"}
PUSH=${PUSH:-1}
ALLOY=${ALLOY:-1}
WIPE=${WIPE:-1}
WATCH_H=${WATCH_H:-24}
WATCH_LOG=${WATCH_LOG:-/var/tmp/visr-ps0-soak.log}
BACKUP_DIR=${BACKUP_DIR:-$HOME/visr-backups}
API=http://127.0.0.1:30088
GRAPH=/api/v1/namespaces/aiops/services/api:8088/proxy/api/graph
PLANT=/api/v1/namespaces/aiops/services/api:8088/proxy/api/plant
MAINT=engine-memory-maint
MEM=/var/lib/skn/memory
ENGINE_STOPPED=0
step() { echo; echo "== $(date +%H:%M:%S) $*"; }
die() { echo "STOP $*"; exit 1; }

# The engine never stays stopped: every exit path after step 4 starts it again.
start_engine() {
  kubectl -n aiops delete pod "$MAINT" --ignore-not-found --wait=false >/dev/null 2>&1
  kubectl -n aiops scale deploy/correlation-engine --replicas=1 >/dev/null
  kubectl -n aiops rollout status deploy/correlation-engine --timeout=300s >/dev/null \
    || echo "WARN the engine rollout did not finish in 300 s"
  ENGINE_STOPPED=0
}
on_exit() { if [ "$ENGINE_STOPPED" = 1 ]; then echo "starting the engine again"; start_engine; fi; }
trap on_exit EXIT
trap 'exit 1' INT TERM HUP

# ---- watch mode: the PS0 soak log (step 5 starts it in screen) ----------------------------------
if [ "${1:-}" = "watch" ]; then
  end=$(( $(date +%s) + WATCH_H * 3600 ))
  echo "# PS0 soak watcher started $(date -Is) for ${WATCH_H} h. QUIET = no finding, root, forecast card, or integrity finding." >> "$WATCH_LOG"
  while [ "$(date +%s)" -lt "$end" ]; do
    # line 1: the verdict graph. Line 2: the plant state (to log a chiller overload trip).
    { kubectl get --raw "$GRAPH" 2>/dev/null; echo; kubectl get --raw "$PLANT" 2>/dev/null; echo; } | python3 -c '
import json, sys, time
stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
parts = [p for p in sys.stdin.read().splitlines() if p.strip()]
try:
    g = json.loads(parts[0])
except (ValueError, IndexError):
    print(stamp, "ENGINE-UNAVAILABLE")
    sys.exit()
try:
    plant = json.loads(parts[1]) if len(parts) > 1 else {}
except ValueError:
    plant = {}
m = g.get("meta") or {}
if m.get("status") == "error":
    print(stamp, "ENGINE-ERROR", m.get("error", ""))
    sys.exit()
roots = " ".join("%s:%.2f" % (r.get("pod"), float(r.get("score") or 0)) for r in (g.get("root") or [])[:3]) or "-"
findings = len(g.get("findings") or [])
incipient = len(g.get("incipient") or [])
integrity = len(g.get("integrity") or [])
chiller = bool(((plant.get("devices") or {}).get("chiller-1") or {}).get("tripped"))
quiet = findings == 0 and roots == "-" and incipient == 0 and integrity == 0
print(stamp, "QUIET" if quiet else "NOISY", "findings=%d" % findings, "edges=%d" % len(g.get("edges") or []),
      "incipient=%d" % incipient, "integrity=%d" % integrity, "chiller_tripped=%s" % chiller,
      "cases=%s" % m.get("cases"), "memory_edges=%s" % m.get("edge_memory"), "root=%s" % roots)
' >> "$WATCH_LOG"
    sleep 60
  done
  echo "# PS0 soak watcher ended $(date -Is)" >> "$WATCH_LOG"
  exit 0
fi

step "0 preflight"
kubectl get nodes 2>/dev/null | grep -q " Ready" || die "k3s is not Ready. Run deploy/resume.sh first."
curl -sf -m 5 http://127.0.0.1:5000/v2/_catalog >/dev/null || die "the registry on 127.0.0.1:5000 does not answer"
curl -s -m 8 "$API/api/health" | grep -q '"auth":"enforced"' || die "the api does not answer with auth enforced"
echo "PASS node Ready, registry up, api up"

step "1 remove the UI-created PLCs"
TOKEN=$(kubectl -n aiops get secret visr-auth -o jsonpath='{.data.operator-token}' | base64 -d)
[ -n "$TOKEN" ] || die "the visr-auth Secret has no operator token"
for plc in $(kubectl -n fleet get deploy -l visr/managed=ui -o jsonpath='{.items[*].metadata.name}'); do
  code=$(curl -s -o /dev/null -m 60 -w '%{http_code}' -X DELETE -H "X-Auth-Token: $TOKEN" \
    -H "X-Remote-User: factory-up" "$API/api/fleet/plcs/$plc")
  [ "$code" = 200 ] || die "the api answered $code to DELETE $plc"
  echo "removed $plc"
done
unset TOKEN
[ -z "$(kubectl -n fleet get deploy -l visr/managed=ui -o name)" ] || die "UI-created PLCs are still present"
echo "PASS fleet Deployments: $(kubectl -n fleet get deploy -o jsonpath='{.items[*].metadata.name}')"

if [ "$PUSH" = 1 ]; then
  step "2 push the images: $ONLY"
  make push ONLY="$ONLY" || die "make push failed"
  echo "PASS pushed $ONLY"
fi

if [ "$ALLOY" = 1 ]; then
  step "2b the alloy log shipper"
  # The same chart version as the installed release, so this step changes only the config.
  ver=$(helm list -n observability -f '^alloy$' -o json 2>/dev/null \
    | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["chart"].rsplit("-",1)[1] if r else "")')
  if [ -z "$ver" ]; then
    echo "WARN the alloy release is not installed. Run deploy/skctl up --components telemetry."
  elif ! helm upgrade alloy grafana/alloy -n observability --version "$ver" \
         -f deploy/values/alloy.yaml >/dev/null; then
    echo "WARN helm upgrade alloy $ver failed"
  # The upgrade changes only the ConfigMap, so no new pod starts. A restart loads the config now,
  # and it clears an old "progress deadline exceeded" state that rollout status reports at once.
  elif ! kubectl -n observability rollout restart deploy/alloy >/dev/null \
       || ! kubectl -n observability rollout status deploy/alloy --timeout=180s >/dev/null; then
    echo "WARN alloy did not become Ready in 180 s. Read: kubectl -n observability logs deploy/alloy -c alloy"
  else
    echo "PASS alloy $ver runs"
    sleep 30
    labels=$(kubectl get --raw /api/v1/namespaces/observability/services/loki:3100/proxy/loki/api/v1/labels 2>/dev/null)
    case "$labels" in
      *'"pod"'*) echo "PASS Loki gets pod logs" ;;
      *) echo "WARN Loki has no pod label yet: ${labels:-no answer}" ;;
    esac
  fi
fi

step "3 deploy/golive.sh (full log: /var/tmp/visr-golive.log)"
bash deploy/golive.sh > /var/tmp/visr-golive.log 2>&1
rc=$?
grep -E "^(PASS|FAIL|INFO)" /var/tmp/visr-golive.log
[ "$rc" = 0 ] || die "golive.sh reported $rc failed checks"
echo "vplc-tasks keys: $(kubectl -n aiops get cm vplc-tasks -o jsonpath='{.data}' | python3 -c 'import json,sys; print(" ".join(sorted(json.load(sys.stdin))))')"

if [ "$WIPE" = 1 ]; then
  step "4 engine memory: stop, back up, wipe, start"
  sleep 60                       # the golive restarts settle first, so no deploy churn gets learned
  ENGINE_STOPPED=1
  kubectl -n aiops scale deploy/correlation-engine --replicas=0 >/dev/null
  for _ in $(seq 1 60); do
    [ -z "$(kubectl -n aiops get pod -l app=correlation-engine -o name)" ] && break
    sleep 3
  done
  [ -z "$(kubectl -n aiops get pod -l app=correlation-engine -o name)" ] \
    || die "the engine pod did not stop. Nothing was wiped."
  echo "PASS the engine is stopped, so the memory files are closed"

  # A maintenance pod mounts the memory volume while the engine is stopped. It uses the engine image,
  # which the node already holds, so this step needs no internet.
  kubectl -n aiops delete pod "$MAINT" --ignore-not-found --wait=true >/dev/null
  kubectl -n aiops apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: $MAINT
spec:
  restartPolicy: Never
  securityContext: { fsGroup: 65534 }
  containers:
    - name: maint
      image: localhost:5000/skn/correlation-engine:v0.1
      imagePullPolicy: IfNotPresent
      command: ["sleep", "900"]
      volumeMounts: [{ name: memory, mountPath: $MEM }]
  volumes:
    - name: memory
      persistentVolumeClaim: { claimName: engine-memory-pvc }
YAML
  kubectl -n aiops wait --for=condition=Ready "pod/$MAINT" --timeout=120s >/dev/null \
    || die "the maintenance pod did not start. Nothing was wiped."

  files=$(kubectl -n aiops exec "$MAINT" -- sh -c "find $MEM -type f | wc -l")
  if [ "${files:-0}" -eq 0 ]; then
    echo "PASS the memory is already empty. Nothing to back up or wipe."
  else
    mkdir -p "$BACKUP_DIR"
    tarball="$BACKUP_DIR/engine-memory-$(date +%Y%m%d-%H%M%S).tar"
    kubectl -n aiops exec "$MAINT" -- tar -C "$MEM" -cf - . > "$tarball" \
      || die "the backup copy failed. Nothing was wiped."
    bytes=$(kubectl -n aiops exec "$MAINT" -- sh -c "find $MEM -type f -exec cat {} + | wc -c")
    got_files=$(tar -tf "$tarball" | grep -vc '/$')
    got_bytes=$(tar -xOf "$tarball" | wc -c)
    [ "$got_files" = "$files" ] && [ "$got_bytes" = "$bytes" ] \
      || die "the backup holds $got_files of $files files and $got_bytes of $bytes bytes. Nothing was wiped."
    echo "PASS backup $tarball ($files files, $bytes bytes)"
    kubectl -n aiops exec "$MAINT" -- sh -c "rm -f $MEM/*.db* && ls -la $MEM" \
      || die "the wipe failed. The backup is $tarball."
    echo "PASS the memory is wiped"
  fi
  start_engine
  echo "PASS the engine runs"
  sleep 45
  kubectl get --raw "$GRAPH" 2>/dev/null | python3 -c 'import json,sys; m=json.load(sys.stdin).get("meta") or {}; print("engine after the wipe: cases=%s memory_edges=%s" % (m.get("cases"), m.get("edge_memory")))' \
    || echo "WARN the engine graph does not answer yet"
fi

step "5 start the PS0 watcher (screen visr-ps0, log $WATCH_LOG)"
screen -S visr-ps0 -X quit >/dev/null 2>&1 || true
if [ -s "$WATCH_LOG" ]; then
  mv "$WATCH_LOG" "$WATCH_LOG.$(date +%Y%m%d-%H%M%S)" || die "the old soak log did not move"
  echo "PASS the old soak log moved aside"
fi
screen -dmS visr-ps0 bash "$REPO/deploy/factory-up.sh" watch
sleep 2
screen -ls | grep -q visr-ps0 || die "the watcher did not start"
echo "PASS the watcher runs"
echo
echo "== DONE $(date +%H:%M:%S). Let the plant run for 2 h or more. Fire no faults during the soak."
echo "   The soak passes when $WATCH_LOG shows only QUIET lines for the last 30 minutes."
