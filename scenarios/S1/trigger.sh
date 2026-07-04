#!/usr/bin/env bash
# S1 - PVC I/O cascade (the hero). Drops the FLUSH flag; cooling-monitor's fio storm begins.
set -e
POD=$(kubectl get pod -n factory-data -l app=cooling-monitor -o name | head -1)
kubectl exec -n factory-data "$POD" -- touch /shared/cooling/FLUSH
echo "S1 fired $(date +%T) - expect: dcim write-latency (+~15s) -> tsdb fsync/probe stress -> ingest queue -> ccr p95"
