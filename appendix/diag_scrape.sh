#!/usr/bin/env bash
# diag_scrape.sh — why are kubelet/cadvisor metrics (incl. PSI) missing?
# Shows up-value per scrape job + whether container_*/kubelet_* exist at all.
# Read-only. Run on the box:  bash appendix/diag_scrape.sh
set -uo pipefail
OBS_NS="${OBS_NS:-observability}"
PROM_SVC="$(kubectl get svc -n "$OBS_NS" -o name 2>/dev/null | sed 's#service/##' | grep -m1 -E 'kube-prometheus-stack-prometheus$')"
[ -z "$PROM_SVC" ] && PROM_SVC=prom-kube-prometheus-stack-prometheus
enc(){ python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$1"; }
raw(){ kubectl get --raw "/api/v1/namespaces/${OBS_NS}/services/${PROM_SVC}:9090/proxy/api/v1/query?query=$(enc "$1")" 2>/dev/null; }

echo "== up value per scrape job (want 1/1; 0/N = scrape failing) =="
raw 'up' | python3 -c '
import sys,json
from collections import defaultdict
g=defaultdict(lambda:[0,0])
for s in json.load(sys.stdin)["data"]["result"]:
    j=s["metric"].get("job","?"); g[j][0]+=1 if s["value"][1]=="1" else 0; g[j][1]+=1
for j in sorted(g):
    up,tot=g[j]; print("  %d/%d up   %s"%(up,tot,j))'

echo
echo "== do kubelet-sourced metrics exist AT ALL (no namespace filter)? =="
for m in container_cpu_usage_seconds_total container_pressure_io_stalled_seconds_total kubelet_volume_stats_used_bytes kube_pod_container_status_restarts_total; do
  n=$(raw "count($m)" | python3 -c 'import sys,json
try:
 r=json.load(sys.stdin)["data"]["result"];print(int(float(r[0]["value"][1])) if r else 0)
except:print("ERR")')
  printf "  %-48s %s series\n" "$m" "$n"
done

echo
echo "== namespaces seen on container_cpu_usage_seconds_total (label-vs-deadscrape check) =="
raw 'count by (namespace) (container_cpu_usage_seconds_total)' | python3 -c '
import sys,json
r=json.load(sys.stdin)["data"]["result"]
print("  (none — metric absent => scrape dead, not a label issue)") if not r else [print("  %-16s %s"%(s["metric"].get("namespace","(none)"),s["value"][1])) for s in r]'

echo
echo "== proof the kernel still serves PSI at the endpoint (scrape problem, not kernel) =="
NODE=$(kubectl get nodes -o name | head -1 | cut -d/ -f2)
kubectl get --raw "/api/v1/nodes/${NODE}/proxy/metrics/cadvisor" 2>/dev/null | grep -m2 container_pressure || echo "  (endpoint check failed — rerun P0 step 5)"
