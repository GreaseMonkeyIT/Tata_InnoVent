#!/usr/bin/env bash
# S1 reset: fio unlinks its own files; just wait for IO to settle and confirm flag is gone.
POD=$(kubectl get pod -n factory-data -l app=cooling-monitor -o name | head -1)
kubectl exec -n factory-data "$POD" -- rm -f /shared/cooling/FLUSH 2>/dev/null || true
echo "cooldown 120s before next scenario"; sleep 120
