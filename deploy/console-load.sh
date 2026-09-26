#!/usr/bin/env bash
# console-load.sh: the request pattern of one open operator console, read-only (LOG-100). No sudo.
# The engine watches the edge node too, so the api's own load is part of normal. On 2026-09-26 a
# console opened after the soak made the api the root by CPU, and a leak card fired, because the
# soak had learned an api with no console attached. factory-up.sh starts this next to the PS0
# watcher, so the baselines learn the api the way the demo runs it: with the console open.
# The endpoints and cadence copy dashboard/app/lib/useConsoleData.js: 11 reads every 5 s, the audit
# ledger every 10 s, the recommendations every 30 s. GET only: it never changes anything.
#
#   screen -dmS visr-console-load bash ~/Tata_InnoVent/deploy/console-load.sh
set -uo pipefail
export KUBECONFIG="$HOME/.kube/config"
HOURS=${HOURS:-24}
API=${API:-http://$(kubectl -n aiops get svc api -o jsonpath='{.spec.clusterIP}'):8088}
FAST="/api/graph /api/narrative /api/health /api/topology /api/pods /api/pod-resources /api/plant /api/tags /api/fleet /api/actions /api/incident"
end=$(( $(date +%s) + HOURS * 3600 ))
n=0
while [ "$(date +%s)" -lt "$end" ]; do
  for p in $FAST; do curl -s -o /dev/null -m 8 "$API$p" & done
  [ $((n % 2)) = 0 ] && curl -s -o /dev/null -m 8 "$API/api/audit" &
  [ $((n % 6)) = 0 ] && curl -s -o /dev/null -m 8 "$API/api/recommendations" &
  wait
  n=$((n + 1))
  sleep 5
done
