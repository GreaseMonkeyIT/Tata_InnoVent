"""vision-qc: 'defect detection' on frames/#. LEAK_ENABLED=true -> retains decoded
frames (~6 MB/s) until the 512Mi limit -> OOMKilled -> restart loop. The leak is a
real unbounded cache, not an allocation stunt.
"""
import collections, os, time, threading

import numpy as np
import paho.mqtt.client as mqtt
from prometheus_client import Gauge, start_http_server

LEAK = os.environ.get("LEAK_ENABLED", "false").lower() == "true"
CACHE_BOUND = 64
cache = [] if LEAK else collections.deque(maxlen=CACHE_BOUND)
CACHED = Gauge("visionqc_cached_frames", "frames held in memory")

def on_msg(_c, _u, m):
    frame = np.frombuffer(os.urandom(640 * 480), dtype=np.uint8).reshape(480, 640).copy()  # "decode"
    edges = float(np.abs(np.diff(frame.astype(np.int16), axis=0)).mean())  # "inference"
    if edges > 200: print("defect!", edges, flush=True)
    cache.append(frame)
    CACHED.set(len(cache))

def synthetic_feed():
    # Self-feed at 20 fps if the frames topic is quiet (keeps the pathology autonomous).
    while True:
        on_msg(None, None, None)
        time.sleep(0.05)

if __name__ == "__main__":
    start_http_server(8080)
    print("vision-qc up; LEAK_ENABLED =", LEAK, flush=True)
    threading.Thread(target=synthetic_feed, daemon=True).start()
    c = mqtt.Client(client_id="vision-qc")
    c.on_message = on_msg
    while True:
        try:
            c.connect(os.environ.get("MQTT_HOST", "mqtt-broker.factory-core.svc"), 1883)
            c.subscribe("frames/#", qos=0)
            c.loop_forever()
        except Exception as e:
            print("mqtt retry:", e, flush=True); time.sleep(5)
