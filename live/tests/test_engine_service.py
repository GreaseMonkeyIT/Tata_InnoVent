"""Checks of live/engine_service.py against a stand-in plant service: the engine takes every sample in
order, opens an incident on the fault and names its root, and serves its state, incidents and metrics.
A plant whose documents differ from the models is refused."""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)

from live import engine as E                                         # noqa: E402
from live import engine_service as S                                 # noqa: E402
from live.tests.test_engine import DOC, _runs                         # noqa: E402

N = E.N


def _plant(run, wiring):
    """A stand-in for the plant service: /info and /samples of one saved run."""
    tags = sorted(run)
    cols = {t: [None if isinstance(v, float) and np.isnan(v) else v for v in vals]
            for t, vals in zip(tags, zip(*[[s[t] for t in tags] for s in E.samples_of(run)]))}
    rows = [[i, float(i), [cols[t][i] for t in tags]] for i in range(len(cols[tags[0]]))]
    info = {"seed": 1, "tags": tags, "wiring": wiring, "lead_s": float(run.lead), "sample_s": 1.0}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/info":
                body = info
            else:
                q = parse_qs(u.query)
                start, limit = int(q["from"][0]), int(q["limit"][0])
                got = rows[start:start + limit]
                body = {"tags": tags, "first": 0, "from": start, "next": start + len(got), "rows": got}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, len(rows)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        body = r.read().decode()
        return json.loads(body) if r.headers.get("Content-Type") == "application/json" else body


def test_engine_service_names_the_root():
    run = _runs(5)
    fit, cal = [(i, run(1500)) for i in range(3)], [(9, run(1500)), (10, run(1500))]
    nd = N.Node("a", DOC).train(fit, cal)
    r = run(2400, bump=0.6, at=1500)
    plant, n = _plant(r, {"a": DOC})
    svc = S.EngineService(E.Engine({"a": nd}, {"a": DOC}), f"http://127.0.0.1:{plant.server_address[1]}")
    svc.connect()
    threading.Thread(target=svc.run, daemon=True).start()
    srv = S.make_server(svc, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        deadline = time.time() + 60.0
        while _get(base, "/state")["next_sample"] < n:
            assert time.time() < deadline
            time.sleep(0.05)
        state = _get(base, "/state")
        assert state["t"] == n - r.lead - 1 and state["error"] is None and set(state["nodes"]) == {"a"}
        expect = nd.alarms(r)
        incs = _get(base, "/incidents")
        rises = set(np.flatnonzero(expect & ~np.r_[False, expect[:-1]]).tolist())
        assert incs and incs[0]["t0"] == min(rises)                     # opened at the batch's first alarm
        assert all(i["t0"] in rises and i["named"] == "a" for i in incs)
        assert incs[0]["t0_plant"] == float(incs[0]["t0"] + r.lead)
        tb = int(np.flatnonzero(expect[1500 - r.lead:])[0]) + 1500 - r.lead     # the first alarm on the bump
        assert any(i["t0"] <= tb and (i["closed"] is None or i["closed"] >= tb) for i in incs)
        metrics = _get(base, "/metrics")
        assert 'visr_alarm{node="a",station="a"} ' in metrics and "visr_incidents_open " in metrics
    finally:
        svc.stop.set()
        srv.shutdown()
        plant.shutdown()


def test_documents_must_match():
    run = _runs(6)
    r = run(200)
    other = dict(DOC, feeds=["site.v"])
    plant, _ = _plant(r, {"a": other})
    nd = N.Node("a", DOC)
    try:
        svc = S.EngineService.__new__(S.EngineService)
        svc.engine, svc.plant = type("E", (), {"w": {"a": DOC}})(), f"http://127.0.0.1:{plant.server_address[1]}"
        with pytest.raises(ValueError):
            svc.connect()
        assert S.documents_match({"a": DOC}, {"a": dict(DOC, feedback_inputs=[])})
        assert nd.doc == DOC
    finally:
        plant.shutdown()
