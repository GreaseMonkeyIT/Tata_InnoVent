#!/usr/bin/env python3
"""The live engine service (2026-10-22, the direct live path, step 2): the streaming engine
(live/engine.py) on the readings of the plant service (live/plant_service.py).

The service reads the plant service's /info and /samples only, never its fault log. It checks that
the plant's documents are the documents the models were trained on, then steps the engine through
every sample in order: the lead first (the filters warm up), then the scored samples. If it starts
after the plant service has dropped the start of the run, the first LEAD_S of the samples it gets are
its lead (as a batch run's lead), and /state says so.

    python -m live.engine_service --pack eval/results/<campaign>/live-pack.pkl --plant http://127.0.0.1:9110

HTTP (JSON unless noted):
    GET /state       the last scored sample and its plant time, every node's alarm and coverage, the
                     machines in alarm, the open incidents, the lag behind the plant clock (lag_s)
    GET /incidents   every incident: when it opened, its root (the counterfactual, and the graph rule
                     as the cross-check), its candidates and victims, when it closed
    GET /metrics     Prometheus text: visr_alarm{node, station}, visr_machine_alarm{station},
                     visr_incidents_open, visr_engine_sample
    GET /healthz     "ok"

The counterfactual runs inside the engine's step (tens of seconds on a long run). The engine then
falls behind the plant and catches up at about 1,000 samples per second. The HTTP answers come from a
copy of the state, so they never wait for it.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from live import engine as E                              # noqa: E402

N = E.N
PORT = 9111
ROWS = 3600              # rows per request to the plant service
INCIDENT_KEYS = ("id", "t0", "t0_plant", "named", "named_graph", "named_cf", "cf_scores", "hit", "victims",
                 "named_plant", "closed", "closed_plant", "merged_into", "second_of")


def documents_match(plant_w, model_w):
    """The plant's documents and the documents of the models: the same nodes with the same feeds,
    commands, measures and asset."""
    keys = ("feeds", "commands", "measures", "asset")
    return set(plant_w) == set(model_w) and all(
        all(plant_w[n].get(k) == model_w[n].get(k) for k in keys) for n in plant_w)


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class EngineService:
    """The feeder thread (the plant service's samples into the engine) and the state that HTTP reads."""

    def __init__(self, engine, plant_url):
        self.engine, self.plant = engine, plant_url.rstrip("/")
        self.stop = threading.Event()
        self.next = 0                    # the plant sample to read next
        self.plant_t = None              # the plant time of the last sample taken
        self.late_start = False
        self.error = None
        self._snapshot()

    def connect(self):
        info = _get(self.plant + "/info")
        if not documents_match(info["wiring"], self.engine.w):
            raise ValueError("the plant's documents differ from the documents of the models")
        if abs(info["sample_s"] - N.SAMPLE_S) > 1e-9:
            raise ValueError(f"the plant samples every {info['sample_s']} s, the models every {N.SAMPLE_S} s")
        self.tags = info["tags"]
        self.info = info
        got = _get(f"{self.plant}/samples?from=0&limit=1")
        lead = int(round(info["lead_s"] / info["sample_s"]))
        if got["first"] > 0:            # the plant service has dropped the start of the run
            self.late_start, self.next = True, got["first"]
            lead = int(N.LEAD_S / N.SAMPLE_S)
        self.engine.start(lead)
        self._snapshot()
        return info

    def run(self):
        last_clock = 0.0
        try:
            while not self.stop.is_set():
                if time.time() - last_clock > 5.0:           # the plant's clock, for the lag in /state
                    try:
                        self.plant_clock = _get(f"{self.plant}/clock").get("t")
                    except OSError:
                        pass
                    last_clock = time.time()
                got = _get(f"{self.plant}/samples?from={self.next}&limit={ROWS}")
                if got["from"] > self.next:
                    raise RuntimeError(f"the plant service no longer has sample {self.next} (it starts at "
                                       f"{got['first']}): the engine fell more than its buffer behind")
                for i, t, vals in got["rows"]:
                    self._take(t, dict(zip(self.tags, vals)))
                    self.next = i + 1
                self._snapshot()
                if not got["rows"]:
                    time.sleep(0.2)
        except Exception as e:          # the service stays up and reports the error in /state
            self.error = f"{type(e).__name__}: {e}"
            self._snapshot()

    def _take(self, plant_t, sample):
        changed = self.engine.step(sample)
        self.plant_t = plant_t
        for inc in changed:
            inc.setdefault("t0_plant", plant_t)
            if inc["named"] is not None:
                inc.setdefault("named_plant", plant_t)
            if inc["closed"] is not None:
                inc.setdefault("closed_plant", plant_t)

    def _snapshot(self):
        eng = self.engine
        incs = [{k: i.get(k) for k in INCIDENT_KEYS} for i in eng.incidents]
        clock = getattr(self, "plant_clock", None)
        state = {"t": eng.t, "plant_t": self.plant_t, "next_sample": self.next, "warming": eng.t < 0,
                 "lag_s": None if clock is None or self.plant_t is None else max(0.0, clock - self.plant_t),
                 "late_start": self.late_start, "error": self.error, "pack": getattr(self, "pack_name", None),
                 "nodes": {n: {"alarm": bool(s.alarm), "cover": bool(s.cover)} for n, s in eng.sn.items()},
                 "machines_in_alarm": sorted(m for m, on in eng.on.items() if on),
                 "open": [i for i in incs if i["closed"] is None]}
        self.snap = {"state": state, "incidents": incs}

    def metrics(self):
        s = self.snap["state"]
        out = ["# HELP visr_alarm A node of the engine in alarm (1) or not (0).", "# TYPE visr_alarm gauge"]
        for n, v in sorted(s.get("nodes", {}).items()):
            out.append(f'visr_alarm{{node="{_esc(n)}",station="{_esc(self.engine.asset[n])}"}} {int(v["alarm"])}')
        out += ["# HELP visr_machine_alarm A machine in alarm (one of its nodes).", "# TYPE visr_machine_alarm gauge"]
        for m in sorted(self.engine.on):
            out.append(f'visr_machine_alarm{{station="{_esc(m)}"}} {int(m in s.get("machines_in_alarm", []))}')
        out += ["# HELP visr_incidents_open Incidents open now.", "# TYPE visr_incidents_open gauge",
                f"visr_incidents_open {len(s.get('open', []))}",
                "# HELP visr_engine_sample The last scored sample of the engine.", "# TYPE visr_engine_sample gauge",
                f"visr_engine_sample {s.get('t', -1)}"]
        return "\n".join(out) + "\n"


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _clean(o):
    """JSON without NaN or infinity (the console reads it too)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def handler(svc):
    class H(BaseHTTPRequestHandler):
        timeout = 10

        def _send(self, code, body, ctype="text/plain"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            return self._send(code, json.dumps(_clean(obj)), "application/json")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/healthz":
                return self._send(200, "ok\n")
            if path == "/state":
                return self._json(200, svc.snap["state"])
            if path == "/incidents":
                return self._json(200, svc.snap["incidents"])
            if path == "/metrics":
                return self._send(200, svc.metrics(), "text/plain; version=0.0.4")
            self._json(404, {"error": "not found"})

        def log_message(self, *_):
            pass

    return H


def make_server(svc, host="127.0.0.1", port=PORT):
    srv = ThreadingHTTPServer((host, port), handler(svc))
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="The live engine service (live/README.md).")
    ap.add_argument("--pack", required=True, help="live-pack.pkl of a campaign (python -m live.engine pack)")
    ap.add_argument("--plant", default="http://127.0.0.1:9110", help="the plant service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args(argv)
    p = E.load_pack(a.pack)
    svc = EngineService(E.Engine(p["nodes"], p["wiring"], p["sig2"]), a.plant)
    svc.pack_name = p.get("campaign")
    for _ in range(60):                 # the plant service may still be starting
        try:
            info = svc.connect()
            break
        except OSError:
            time.sleep(1.0)
    else:
        ap.error(f"no plant service at {a.plant}")
    threading.Thread(target=svc.run, daemon=True).start()
    srv = make_server(svc, a.host, a.port)
    print(f"live engine: {len(p['nodes'])} nodes from {svc.pack_name}, plant seed {info['seed']}"
          f"{' (late start)' if svc.late_start else ''}, on http://{a.host}:{srv.server_address[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
