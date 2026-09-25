#!/usr/bin/env bash
# prometheus-storage.sh: move the Prometheus TSDB to the slow disk and keep 30 days (LOG-088).
# Run it on the box as the normal user, in a terminal (apply asks for sudo once):
#   bash ~/Tata_InnoVent/deploy/prometheus-storage.sh check    # read-only: prints what apply would change
#   bash ~/Tata_InnoVent/deploy/prometheus-storage.sh apply    # the directory, the PV, the helm upgrade
#   bash ~/Tata_InnoVent/deploy/prometheus-storage.sh verify   # read-only: the checks after apply
#
# Before: the TSDB is an emptyDir with 12 h retention, so a reboot erases every series.
# After: PV prometheus-pv-slowdisk (deploy/slowdisk.yaml) on /mnt/slowdisk/prometheus, 30 days or
# 100 GB, whichever comes first (deploy/values/prometheus-storage.yaml).
# apply: stop while a soak or a proof run is active. Make the directory with owner 1000:2000 (sudo),
# apply deploy/slowdisk.yaml, then run helm upgrade with --reuse-values plus the overlay and the chart
# version that runs now. --reuse-values keeps the live scrape jobs, which differ from
# values/prometheus.yaml. The operator recreates the Prometheus pod once. The series of the last 12 h
# are lost, and the engine is noisy for a few minutes while its window fills again. When the claim
# does not bind or the pod does not start, the script runs helm rollback to the release from before.
# Grafana keeps its /grafana/ path from kubectl set env (PIVOT_SETUP 5.2b). verify puts it back if the
# upgrade removed it.
# FORCE=1 skips the soak and proof-run guard.
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
MODE=${1:-}
NS=observability
REL=prom
CHART=prometheus-community/kube-prometheus-stack
DIR=/mnt/slowdisk/prometheus
PV=prometheus-pv-slowdisk
PVC=prometheus-prom-kube-prometheus-stack-prometheus-db-prometheus-prom-kube-prometheus-stack-prometheus-0
STS=prometheus-prom-kube-prometheus-stack-prometheus
PROM=http://127.0.0.1:30090
NEED_GB=160
step() { echo; echo "== $(date +%H:%M:%S) $*"; }
pass() { echo "PASS $1"; }
info() { echo "INFO $1"; }
die() { echo "STOP $*"; exit 1; }

chart_version() {   # the chart version of the live release, e.g. 87.5.1
  helm -n "$NS" list -f "^$REL\$" -o json 2>/dev/null | python3 -c '
import json, sys
rows = json.load(sys.stdin)
print(rows[0]["chart"].rsplit("-", 1)[1] if rows else "")'
}

revision() {
  helm -n "$NS" list -f "^$REL\$" -o json 2>/dev/null | python3 -c '
import json, sys
rows = json.load(sys.stdin)
print(rows[0]["revision"] if rows else "")'
}

check() {
  step "check (read-only)"
  findmnt -n /mnt/slowdisk >/dev/null || die "/mnt/slowdisk is not mounted"
  pass "/mnt/slowdisk is mounted ($(findmnt -n -o SOURCE /mnt/slowdisk))"
  local free
  free=$(df -BG --output=avail /mnt/slowdisk | tail -1 | tr -dc 0-9)
  [ "${free:-0}" -ge "$NEED_GB" ] || die "the slow disk has ${free} GB free, it needs ${NEED_GB} GB"
  pass "the slow disk has ${free} GB free"
  VER=$(chart_version)
  [ -n "$VER" ] || die "no helm release $REL in namespace $NS"
  pass "helm release $REL runs chart $CHART $VER, revision $(revision)"
  helm show chart "$CHART" --version "$VER" >/dev/null 2>&1 || helm repo update prometheus-community >/dev/null 2>&1
  helm show chart "$CHART" --version "$VER" >/dev/null 2>&1 || die "the chart $CHART $VER is not in the helm repo cache"
  pass "the chart $VER is in the helm repo cache"
  if [ "${FORCE:-0}" != 1 ] && screen -ls 2>/dev/null | grep -qE '\.(visr-ps0|visr-proof|visr-s1cap)[[:space:]]'; then
    die "a soak, proof run, or capture run is active (screen -ls). The upgrade restarts Prometheus. FORCE=1 skips this."
  fi
  pass "no soak or proof run is active"
  info "retention now: $(kubectl -n "$NS" get prometheus -o jsonpath='{.items[0].spec.retention}')," \
       "storage now: $(kubectl -n "$NS" get prometheus -o jsonpath='{.items[0].spec.storage}' | head -c 120)"
  if kubectl get pv "$PV" >/dev/null 2>&1; then
    info "PV $PV exists: $(kubectl get pv "$PV" -o jsonpath='{.status.phase} {.spec.claimRef.name}')"
  else
    info "PV $PV does not exist yet. apply creates it."
  fi
  if [ -d "$DIR" ]; then
    info "$DIR exists, owner $(stat -c %u:%g "$DIR")"
  else
    info "$DIR does not exist yet. apply makes it with sudo."
  fi
  info "apply runs: helm upgrade $REL $CHART --version $VER --reuse-values -f deploy/values/prometheus-storage.yaml"
}

grafana_path() {   # Grafana must answer on /grafana/ (the console proxies it there)
  local code
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:30030/grafana/api/health)
  if [ "$code" = 200 ]; then pass "Grafana answers on /grafana/"; return 0; fi
  info "Grafana answers $code on /grafana/. Setting the sub-path again (PIVOT_SETUP 5.2b)."
  kubectl -n "$NS" set env deploy/prom-grafana -c grafana \
    GF_SERVER_ROOT_URL='%(protocol)s://%(domain)s:%(http_port)s/grafana/' GF_SERVER_SERVE_FROM_SUB_PATH=true
  kubectl -n "$NS" rollout status deploy/prom-grafana --timeout=180s
  code=$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:30030/grafana/api/health)
  [ "$code" = 200 ] && pass "Grafana answers on /grafana/" || echo "FAIL Grafana answers $code on /grafana/"
}

verify() {
  step "verify (read-only, except the Grafana sub-path repair)"
  local bad=0 phase ready ret n
  phase=$(kubectl -n "$NS" get pvc "$PVC" -o jsonpath='{.status.phase} {.spec.volumeName}' 2>/dev/null)
  [ "$phase" = "Bound $PV" ] && pass "claim $PVC is Bound to $PV" || { echo "FAIL claim: '$phase'"; bad=1; }
  findmnt -rn -o TARGET | grep -q "local-volume/$PV\$" && pass "the Prometheus pod mounts $PV" \
    || { echo "FAIL no kubelet mount of $PV"; bad=1; }
  ready=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$PROM/-/ready")
  [ "$ready" = 200 ] && pass "Prometheus is ready" || { echo "FAIL Prometheus /-/ready answers $ready"; bad=1; }
  ret=$(curl -s -m 10 "$PROM/api/v1/status/flags" | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print(d.get("storage.tsdb.retention.time"), d.get("storage.tsdb.retention.size"))' 2>/dev/null)
  [ "$ret" = "30d 100GB" ] && pass "retention 30d, 100GB" || { echo "FAIL retention flags: '$ret'"; bad=1; }
  n=$(curl -s -m 10 "$PROM/api/v1/query?query=count(plant_current_draw_amps)" | python3 -c '
import json, sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else 0)' 2>/dev/null)
  [ "${n:-0}" != 0 ] && pass "plant series arrive ($n machines)" || info "no plant series yet (the first scrape can take 30 s)"
  n=$(curl -s -m 10 "$PROM/api/v1/query?query=visr_engine_up" | python3 -c '
import json, sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else "none")' 2>/dev/null)
  if [ "$n" = none ]; then
    info "no visr_* series: the api image with GET /metrics or its ServiceMonitor is not deployed yet (LOG-088)"
  else
    pass "the verdict series arrive (visr_engine_up $n)"
  fi
  grafana_path
  du -sh "$DIR" 2>/dev/null | sed 's/^/INFO TSDB size /'
  [ "$bad" = 0 ] && echo "VERIFY PASS" || echo "VERIFY FAIL"
  return "$bad"
}

apply() {
  check
  local before
  before=$(revision)
  step "the directory $DIR (sudo)"
  sudo mkdir -p "$DIR" || die "sudo mkdir failed"
  sudo chown 1000:2000 "$DIR" && sudo chmod 0775 "$DIR" || die "sudo chown failed"
  pass "$DIR owner $(stat -c %u:%g "$DIR")"
  step "the PVs"
  NODE=$(kubectl get node -o jsonpath='{.items[0].metadata.labels.kubernetes\.io/hostname}')
  sed "s/<NODE_NAME>/$NODE/g" deploy/slowdisk.yaml | kubectl apply -f - || die "kubectl apply of deploy/slowdisk.yaml failed"
  step "helm upgrade $REL (chart $VER, revision $before, --reuse-values + the storage overlay)"
  if ! helm -n "$NS" upgrade "$REL" "$CHART" --version "$VER" --reuse-values \
       -f deploy/values/prometheus-storage.yaml --wait --timeout 10m; then
    echo "FAIL helm upgrade"
    rollback "$before"
  fi
  step "the claim and the pod"
  if ! kubectl -n "$NS" wait pvc/"$PVC" --for=jsonpath='{.status.phase}'=Bound --timeout=300s; then
    kubectl -n "$NS" describe pvc "$PVC" | tail -8
    rollback "$before"
  fi
  if ! kubectl -n "$NS" rollout status statefulset/"$STS" --timeout=300s; then
    kubectl -n "$NS" get pods -l app.kubernetes.io/name=prometheus
    rollback "$before"
  fi
  sleep 30
  verify
}

rollback() {
  step "rollback to helm revision $1"
  helm -n "$NS" rollback "$REL" "$1" --wait --timeout 10m || echo "FAIL helm rollback. Run: helm -n $NS rollback $REL $1"
  kubectl -n "$NS" rollout status statefulset/"$STS" --timeout=300s
  grafana_path
  die "the storage move was rolled back. The PV $PV and $DIR stay and hold no data."
}

case "$MODE" in
  check) check ;;
  apply) apply ;;
  verify) verify ;;
  *) sed -n '2,6p' "$0"; exit 1 ;;
esac
