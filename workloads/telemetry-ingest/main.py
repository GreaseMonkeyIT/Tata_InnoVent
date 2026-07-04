"""telemetry-ingest: consumes sensors/# from MQTT, batch-INSERTs into TimescaleDB.

Backpressure made visible: queue depth gauge rises when the DB slows (S1 link).
"""
import os, queue, threading, time

import paho.mqtt.client as mqtt
import psycopg2
from prometheus_client import Gauge, Counter, start_http_server

Q = queue.Queue(maxsize=50000)
QDEPTH = Gauge("ingest_queue_depth", "pending rows")
INSERTED = Counter("ingest_rows_total", "rows written")

def on_msg(_c, _u, m):
    try: Q.put_nowait((time.time(), m.topic, m.payload.decode()))
    except queue.Full: pass  # drop: visible as flat INSERTED while sensors keep firing

def writer():
    dsn = os.environ.get("PG_DSN", "host=timescaledb.factory-data.svc user=factory password=factory dbname=telemetry")
    while True:
        try:
            conn = psycopg2.connect(dsn); conn.autocommit = False
            cur = conn.cursor()
            while True:
                batch = [Q.get()]
                t0 = time.time()
                while len(batch) < 500 and time.time() - t0 < 1.0:
                    try: batch.append(Q.get_nowait())
                    except queue.Empty: break
                cur.executemany("INSERT INTO readings(ts, topic, payload) VALUES (to_timestamp(%s), %s, %s)", batch)
                conn.commit()
                INSERTED.inc(len(batch))
                QDEPTH.set(Q.qsize())
        except Exception as e:
            print("db writer error, reconnecting:", e, flush=True)
            time.sleep(2)

if __name__ == "__main__":
    start_http_server(8080)
    threading.Thread(target=writer, daemon=True).start()
    c = mqtt.Client(client_id="telemetry-ingest")
    c.on_message = on_msg
    while True:
        try:
            c.connect(os.environ.get("MQTT_HOST", "mqtt-broker.factory-core.svc"), 1883)
            c.subscribe("sensors/#", qos=1)
            c.loop_forever()
        except Exception as e:
            print("mqtt retry:", e, flush=True); time.sleep(2)
