#!/usr/bin/env bash
# restart_test.sh - prove tsdb-pvc telemetry survives a desktop reboot (LOG-028).
# Run on the box. Invoke with bash (Syncthing strips +x).
#   bash appendix/restart_test.sh record     # BEFORE reboot
#   sudo reboot          (or smart-plug OFF -> 10s -> ON, per D-011)
#   bash appendix/restart_test.sh verify      # AFTER the box is back + pods Running
set -uo pipefail
NS=factory-data; DEP=timescaledb; DB=telemetry; U=factory
STATE="${STATE:-/tmp/restart_pre.txt}"
psqlq(){ kubectl exec -n "$NS" deploy/"$DEP" -- psql -U "$U" -d "$DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
pvcuid(){ kubectl get pvc tsdb-pvc -n "$NS" -o jsonpath='{.metadata.uid}' 2>/dev/null; }

case "${1:-}" in
record)
  c=$(psqlq "SELECT count(*) FROM readings;")
  printf "count=%s\nmin=%s\nmax=%s\npvc_uid=%s\n" \
    "$c" "$(psqlq "SELECT min(ts) FROM readings;")" "$(psqlq "SELECT max(ts) FROM readings;")" "$(pvcuid)" | tee "$STATE"
  echo ">> now: sudo reboot   (or smart-plug cycle). When back + pods Running: bash $0 verify"
  ;;
verify)
  [ -f "$STATE" ] || { echo "no $STATE - run 'record' first"; exit 1; }
  PRE_C=$(grep '^count=' "$STATE" | cut -d= -f2)
  PRE_U=$(grep '^pvc_uid=' "$STATE" | cut -d= -f2)
  C=$(psqlq "SELECT count(*) FROM readings;"); U=$(pvcuid)
  SPAN=$(psqlq "SELECT date_trunc('second', max(ts)-min(ts)) FROM readings;")
  echo "pre : count=$PRE_C pvc=$PRE_U"
  echo "post: count=$C pvc=$U  | window span=$SPAN"
  rc=0
  [ -n "$U" ] && [ "$U" = "$PRE_U" ] && echo "PASS  PVC rebound (same volume - reboot kept it)" || { echo "FAIL  PVC uid changed -> data NOT persisted"; rc=1; }
  { [ -n "$C" ] && [ "$C" -ge "$PRE_C" ] 2>/dev/null; } && echo "PASS  rows persisted + ingest resumed ($C >= $PRE_C)" || { echo "FAIL  row count dropped ($C < $PRE_C)"; rc=1; }
  echo "note: window span should settle to <= ~14 days once the retention policy has run."
  exit $rc
  ;;
*) echo "usage: bash $0 record|verify"; exit 1;;
esac
