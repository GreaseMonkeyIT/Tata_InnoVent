#!/usr/bin/env bash
# psi_watch.sh - live per-pod PSI stall rate (io/cpu/mem) for factory-data. Read-only.
# The end-to-end proof that L0 pathology -> L1 PSI observation works.
#   bash appendix/psi_watch.sh                 # one snapshot
#   watch -n2 'bash appendix/psi_watch.sh'     # live during an S1/S3 trigger
set -u
OBS=observability
PROM=$(kubectl get svc -n $OBS -o name 2>/dev/null | sed 's#service/##' | grep -m1 'kube-prometheus-stack-prometheus$')
PROM=${PROM:-prom-kube-prometheus-stack-prometheus}
enc(){ python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$1"; }
q(){ kubectl get --raw "/api/v1/namespaces/$OBS/services/$PROM:9090/proxy/api/v1/query?query=$(enc "$1")" 2>/dev/null; }
for res in io cpu mem; do
  echo "== PSI $res  (stall rate by pod, factory-data; >0.2 = suffering) =="
  q "sum by (pod) (rate(container_pressure_${res}_stalled_seconds_total{namespace=\"factory-data\"}[30s]))" \
  | python3 -c 'import sys,json
try:
  r=json.load(sys.stdin)["data"]["result"]
except Exception: r=[]
if not r: print("  (no data)")
for s in sorted(r,key=lambda x:-float(x["value"][1])):
    v=float(s["value"][1])
    if v>0.0001: print("  %-30s %.4f%s"%(s["metric"].get("pod","?"),v,"  <-- stalled" if v>0.2 else ""))'
done
