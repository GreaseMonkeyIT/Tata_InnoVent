#!/bin/sh
# log-archiver: S2 large-file I/O starvation on the shared PVC -- a sustained, CONCURRENT, fsync-heavy
# fio storm (O_DIRECT), the same recipe S1's cooling-monitor uses, but rooted at a DISTINCT workload.
# Why fio, not a single dd (LOG-091): a lone sequential `dd oflag=direct` saturated the archiver's OWN
# bandwidth (it self-stalled -> psi_io ~2.6) but did NOT create the concurrent small-fsync IOPS
# contention that stalls a co-resident database. timescaledb's WAL fsync (continuous from
# telemetry-ingest commits) slipped through between the big sequential blocks, so it never crossed
# DEV_K -> no victim -> the engine (correctly, by its real-victim rule, LOG-074) formed no edge ->
# root []. fio's JOBS concurrent fsync'd O_DIRECT writers thrash the shared HDD queue so timescaledb's
# WAL fsync stalls (the psi_io victim). O_DIRECT keeps it OOM-safe (no dirty-page pile-up against the
# 256Mi limit -- preserves the LOG-087 fix). Both PVCs sit on the same slowdisk HDD (deploy/slowdisk.yaml).
set -x
SRC="${DATA_DIR:-/shared}"
DST="$SRC/archive"
SEED_MB="${S2_SEED_MB:-2048}"           # TOTAL on-disk footprint (MB), split across JOBS; keep < the 5Gi PVC
JOBS="${S2_JOBS:-4}"                     # concurrent writers -- the IOPS-contention lever (S1 uses 4)
FSYNC="${S2_FSYNC:-2}"                   # fsync every N writes; lower = more victim stall (S1 uses 2)
RUNTIME="${S2_RUNTIME:-120}"            # sustained storm seconds -- fills the engine's ~12-sample window
PER_JOB=$(( SEED_MB / JOBS ))           # per-job file size; PER_JOB x JOBS = SEED_MB footprint
mkdir -p "$DST"
# concurrent O_DIRECT write storm with frequent fsync -> real device I/O (no page cache, no OOM) that
# thrashes the shared spindle. --unlink=1 drops the job files at the end so the PVC stays bounded.
# (If the alpine fio build lacks the libaio engine, swap --ioengine=libaio -> --ioengine=psync.)
fio --name=s2archive --directory="$DST" --rw=write --bs=1M \
    --size="${PER_JOB}m" --numjobs="$JOBS" --fsync="$FSYNC" --direct=1 \
    --ioengine=libaio --time_based --runtime="$RUNTIME" --group_reporting --unlink=1 2>/dev/null
