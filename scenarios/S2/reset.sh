#!/usr/bin/env bash
# S2 reset: remove the archiver job so the shared PVC returns to baseline. The archiver deletes its
# own payload on completion (and the next run overwrites it), so nothing else to clean. The verdict
# self-clears ~2-3 min after the storm ends via the engine's recency gate (RESET_WINDOW).
kubectl delete job log-archiver-s2 -n factory-data --ignore-not-found
echo "S2 reset - archiver job removed; verdict clears in ~2-3 min (recency gate)"
