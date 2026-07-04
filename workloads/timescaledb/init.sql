-- TimescaleDB telemetry store. 14-day rolling raw window (D-013 / LOG-029):
-- 1h chunks, native compression after 1h, retention drops chunks > 14 days.
-- Sized for tsdb-pvc=64Gi (14d compressed ~15-30GB + recent uncompressed + WAL).
CREATE TABLE IF NOT EXISTS readings(ts timestamptz NOT NULL, topic text, payload text);
SELECT create_hypertable('readings', 'ts',
       chunk_time_interval => INTERVAL '1 hour', if_not_exists => TRUE);

-- ingest is append-only + time-ordered, so compressing closed chunks is safe
ALTER TABLE readings SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'topic',
  timescaledb.compress_orderby   = 'ts DESC'
);
SELECT add_compression_policy('readings', INTERVAL '1 hour',  if_not_exists => TRUE);
SELECT add_retention_policy(  'readings', INTERVAL '14 days', if_not_exists => TRUE);
