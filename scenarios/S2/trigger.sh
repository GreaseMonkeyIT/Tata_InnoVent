#!/usr/bin/env bash
# S2 - large-file I/O starvation: run the archiver NOW. The job is named `log-archiver-s2` (fixed, no
# timestamp) so the engine's workload() (drops the last 2 name segments) resolves the pod
# `log-archiver-s2-<hash>` back to `log-archiver` -- a STORAGE-domain workload, so it couples to the
# shared disk and is attributed as the SOURCE. A `s2-run-<ts>` name would resolve to `s2-run` (not in
# STORAGE) and never form an edge (the LOG-075 job-naming trap).
kubectl delete job log-archiver-s2 -n factory-data --ignore-not-found
kubectl create job --from=cronjob/log-archiver log-archiver-s2 -n factory-data
echo "S2 fired - bulk write + read on the shared PVC; expect root=log-archiver (DISTINCT from S1's cooling-monitor), victim=timescaledb"
