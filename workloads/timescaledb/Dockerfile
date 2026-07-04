FROM timescale/timescaledb:latest-pg16
COPY init.sql /docker-entrypoint-initdb.d/90-init.sql
# Checkpointing relaxed from 60s/256MB (LOG-087): the old aggressive cadence kept idle psi_io chronically
# ~0.15 (right at the alert line), which the structural cooling-monitor->timescaledb backbone re-lit as a
# phantom root at idle -> S0 not reliably silent. At 300s/1GB idle is quiet; under S1/S2 timescaledb still
# stalls from the EXTERNAL shared-disk storm (the victim story survives, and the cause is now cleanly
# attributable to the aggressor, not self-checkpoints). Tune checkpoint_timeout up if S0 still flickers.
CMD ["postgres", "-c", "max_wal_size=1GB", "-c", "checkpoint_timeout=300s", "-c", "shared_buffers=256MB"]
