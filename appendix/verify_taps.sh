#!/usr/bin/env bash
# verify_taps.sh — P2 gate (BUILD_GUIDE P2 done-when).
# Confirms every MASTER_PLAN §2.7 observation tap is live in Prometheus/Loki.
# Read-only: only issues queries, never mutates the cluster.
#
# Transport: kubectl get --raw via the API-server SERVICE PROXY (same path P0 used:
# `kubectl get --raw .../proxy/...`). No wget/curl/shell needed inside any pod —
# the Prometheus image has none, which is why v1 of this script failed every check.
#
# Run ON the K3s node (or any box whose kubectl points at the cluster):
#   bash appendix/verify_taps.sh            # core gate: kube-prometheus-stack taps must pass
#   bash appendix/verify_taps.sh --strict   # full P2 close: also require eBPF (Caretta/OBI) + Loki streams
#   OBS_NS=observability bash appendix/verify_taps.sh
#
# Note: Syncthing strips +x on cross-OS sync (LOG-022) — invoke with `bash`, not `./`.
# Exit 0 = required groups green; 1 = a required tap is missing; 2 = setup/transport problem.
set -uo pipefail

OBS_NS="${OBS_NS:-observability}"
STRICT=0; [ "${1:-}" = "--strict" ] && STRICT=1
PASS=0; FAIL=0; WARN=0
green(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
red(){   printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
warn(){  printf '  \033[33mWARN\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
hdr(){   printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v kubectl >/dev/null || { echo "kubectl not found";  exit 2; }
command -v python3 >/dev/null || { echo "python3 not found (URL-encode + JSON parse)"; exit 2; }

# --- discover the Prometheus ClusterIP service (release-name agnostic) ---
PROM_SVC="$(kubectl get svc -n "$OBS_NS" -o name 2>/dev/null | sed 's#service/##' | grep -m1 -E 'kube-prometheus-stack-prometheus$')"
[ -z "$PROM_SVC" ] && PROM_SVC="$(kubectl get svc -n "$OBS_NS" -o name 2>/dev/null | sed 's#service/##' | grep -m1 'prometheus' | grep -vE 'operator|node-exporter|alertmanager')"
[ -z "$PROM_SVC" ] && PROM_SVC="prom-kube-prometheus-stack-prometheus"
PROM_PORT=9090

urlenc(){ python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$1"; }
pfetch(){ kubectl get --raw "/api/v1/namespaces/${OBS_NS}/services/${PROM_SVC}:${PROM_PORT}/proxy/api/v1/query?query=$(urlenc "$1")" 2>/dev/null; }

# --- preflight: prove we can reach Prometheus at all (turns a wall of red into one line) ---
PF="$(pfetch 'vector(1)')"
if ! printf '%s' "$PF" | grep -q '"status":"success"'; then
  echo "PREFLIGHT FAIL — cannot query Prometheus via service proxy ${PROM_SVC}:${PROM_PORT} (ns/$OBS_NS)."
  echo "raw response (first 300 chars): ${PF:0:300}"
  echo "check: kubectl get svc -n $OBS_NS | grep prometheus   # confirm name + that port 9090 exists"
  exit 2
fi

# promcount EXPR -> int series count of count(EXPR); 0 = metric absent; -1 = query error
promcount(){
  pfetch "count($1)" | python3 -c 'import sys,json
try:
  r=json.load(sys.stdin)["data"]["result"]; print(int(float(r[0]["value"][1])) if r else 0)
except Exception: print(-1)'
}
chk(){  local n; n="$(promcount "$2")"
  if   [ "$n" -gt 0 ] 2>/dev/null; then green "$1 ($n series)"
  elif [ "$n" = "0" ];            then red   "$1 — metric absent"
  else                                 red   "$1 — query error"; fi; }
chkw(){ local n; n="$(promcount "$2")"
  if   [ "$n" -gt 0 ] 2>/dev/null; then green "$1 ($n series)"
  else                                 warn  "$1 — not present yet"; fi; }

echo "Prometheus svc: ${PROM_SVC}:${PROM_PORT}   namespace: $OBS_NS   strict=$STRICT   (preflight OK)"

hdr "A · Core Prometheus taps (§2.7: cadvisor / kubelet / node-exporter / kube-state)"
chk "cadvisor CPU   (container_cpu_usage_seconds_total, factory-*)" 'container_cpu_usage_seconds_total{namespace=~"factory-.*"}'
chk "cadvisor mem   (working_set_bytes)"                            'container_memory_working_set_bytes{namespace=~"factory-.*"}'
chk "cadvisor throttle (cfs_throttled_periods)"                     'container_cpu_cfs_throttled_periods_total{namespace=~"factory-.*"}'
chk "cadvisor PSI cpu  *the differentiator*"                        'container_pressure_cpu_stalled_seconds_total{namespace=~"factory-.*"}'
chk "cadvisor PSI mem"                                              'container_pressure_memory_stalled_seconds_total{namespace=~"factory-.*"}'
chk "cadvisor PSI io"                                               'container_pressure_io_stalled_seconds_total{namespace=~"factory-.*"}'
chk "kubelet PVC stats (kubelet_volume_stats_used_bytes)"           'kubelet_volume_stats_used_bytes'
chk "node-exporter disk io_time"                                    'node_disk_io_time_seconds_total'
chk "node-exporter TCP retransmits"                                 'node_netstat_Tcp_RetransSegs'
chk "kube-state restarts"                                           'kube_pod_container_status_restarts_total{namespace=~"factory-.*"}'
chk "kube-state OOM/last-terminated ready"                          'kube_pod_container_status_last_terminated_reason'
chk "l0-fast 5s job up"                                             'up{job="l0-fast"}'
chk "cadvisor-fast 5s job up"                                       'up{job="cadvisor-fast"}'

hdr "A2 · PSI is rate-able (needs ≥2 scrapes — run ~60s after install)"
chkw "rate(container_pressure_io_stalled[30s]) on factory-data" 'rate(container_pressure_io_stalled_seconds_total{namespace="factory-data"}[30s])'

hdr "C · eBPF taps (later P2 steps: Caretta, OBI/Beyla — the kernel-7.0 cliffhanger)"
if [ "$STRICT" = 1 ]; then
  chk  "Caretta links (caretta_links_observed_total)" 'caretta_links_observed_total'
  chk  "OBI/Beyla RED http"                           '{__name__=~"http_server_request_duration_seconds_count|http_server_duration_milliseconds_count|beyla_.+|obi_.+"}'
else
  chkw "Caretta links (caretta_links_observed_total)" 'caretta_links_observed_total'
  chkw "OBI/Beyla RED http"                           '{__name__=~"http_server_request_duration_seconds_count|http_server_duration_milliseconds_count|beyla_.+|obi_.+"}'
fi

hdr "B · Loki log streams (Alloy-shipped — P2 step 2)"
LOKI_SVC="$(kubectl get svc -n "$OBS_NS" -o name 2>/dev/null | sed 's#service/##' | grep -m1 -E '^loki$|^loki-')"
[ -z "$LOKI_SVC" ] && LOKI_SVC="loki"
NSVALS="$(kubectl get --raw "/api/v1/namespaces/${OBS_NS}/services/${LOKI_SVC}:3100/proxy/loki/api/v1/label/namespace/values" 2>/dev/null)"
if   printf '%s' "$NSVALS" | grep -q 'factory-core'; then green "Loki has factory-core streams (svc=$LOKI_SVC)"
elif [ "$STRICT" = 1 ];                              then red   "Loki: no factory-core streams (Alloy not shipping?)"
else                                                      warn  "Loki up but no factory-core streams yet — deploy Alloy (P2 step 2)"; fi

hdr "D · Ground-truth channel separation (D-004) — reported, never blocks"
TRUTH="$(promcount 'up{channel="truth"}')"
if [ "$TRUTH" -gt 0 ] 2>/dev/null; then green "channel=truth job present ($TRUTH targets up)"
else warn "no channel=truth label: l0-fast scrapes app /metrics unseparated — add a relabel before P3 so the engine can't read ground truth (D-004)"; fi

hdr "Summary"
printf "  PASS=%d  WARN=%d  FAIL=%d   (strict=%d)\n" "$PASS" "$WARN" "$FAIL" "$STRICT"
if [ "$FAIL" -gt 0 ]; then echo "  -> gate RED (a required tap is missing)"; exit 1; fi
if [ "$STRICT" = 1 ] && [ "$WARN" -gt 0 ]; then echo "  -> strict gate RED (WARN blocks in --strict)"; exit 1; fi
echo "  -> gate GREEN"; exit 0
