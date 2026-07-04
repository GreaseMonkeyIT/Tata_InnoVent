"""analytics-batch: 5-minute KPI rollup. Demands ~2 cores for ~120s inside a 500m
CPU limit -> CFS throttling + CPU PSI on the node. Runs as a CronJob (*/5).
"""
import os, time
import numpy as np
import psycopg2

BURST_S = int(os.environ.get("BURST_S", "120"))
THREADS = os.environ.get("NUMPY_THREADS", "2")
os.environ["OMP_NUM_THREADS"] = THREADS

def rollup():
    try:
        dsn = os.environ.get("PG_DSN", "host=timescaledb.factory-data.svc user=factory password=factory dbname=telemetry")
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*), avg(length(payload)) FROM readings WHERE ts > now() - interval '5 minutes'")
            print("rollup:", cur.fetchone(), flush=True)
    except Exception as e:
        print("db rollup skipped:", e, flush=True)

if __name__ == "__main__":
    rollup()
    print(f"KPI burst: ~{THREADS} cores for {BURST_S}s under the 500m limit", flush=True)
    t0 = time.time()
    a = np.random.rand(1500, 1500)
    while time.time() - t0 < BURST_S:
        a = a @ a.T / 1500.0  # sustained FPU load
    print("burst done", flush=True)
