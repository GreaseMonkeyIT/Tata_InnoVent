#!/usr/bin/env python3
"""The live plant service: the plant runs on the wall clock and serves its readings (2026-10-21, the
direct live path, step 1).

The plant is the run of eval/nodes.py: `new_plant(seed)`, the warm-up, the lead, the step DT and one
sample every SAMPLE_S. The service reads the tags of the plant documents (wiring.py) through the
virtual instruments, as `record` reads them. So the readings of a seed equal `record(seed, hours,
fault)`, value for value (live/tests/test_plant_service.py, and --check with the full warm-up).

The plant warms up at full speed. From the end of the warm-up, plant time follows the wall clock,
times the speed. The engine reads the readings only. The fault log is the ground truth: it is for
the operator and the scorer, never for the engine.

    python -m live.plant_service --seed 60000 --speed 1 --port 9110
    python -m live.plant_service --check 1 --fault F6:0.5:600

HTTP (JSON unless noted):
    GET  /info                    the run: seed, day of the year, warm-up, lead, sample period,
                                  tags, commands and the documents (wiring)
    GET  /clock                   plant time, speed, anchor, lag, warm-up state, last sample
    GET  /samples?from=i&limit=n  the readings from sample i: rows [i, plant s, [one value per tag]]
    GET  /metrics                 Prometheus text: visr_value{tag, station} for each number of the
                                  last sample (the metric of the console preview), the plant clock
    GET  /faults                  the fault log (the operator and the scorer only)
    GET  /floor?since=t           with --floor: the console's floor recording after plant time t (all
                                  without t): station states, tracking events, values every 2 s
    GET  /healthz                 "ok"
    POST /fault                   {"fid": "F6", "severity": 0.5}, "onset_s" optional (seconds after
                                  the warm-up; default now)
    POST /fault/clear             {"fid": "F6"}: the operator clears a fault
    POST /speed                   {"speed": 20}

The JSON keeps Python's NaN token, so a value goes through unchanged (the clients are Python).
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "plant"))
sys.path.insert(0, ROOT)

from eval import nodes as N                               # noqa: E402  the run: plant, warm-up, lead, samples
from model import fault_ranges as RANGES                  # noqa: E402
from model import faults as FR                            # noqa: E402
from model.instruments import Instruments                 # noqa: E402
from model.wiring import instrument_ranges, wiring        # noqa: E402
from floor.replay import SAMPLE_S as FLOOR_SAMPLE_S, STATE_RULES, FloorRecorder   # noqa: E402

PORT = 9110
DEFAULT_SEED = N.SEED_BASE["validation"]   # a validation day: the engine never trained on it
KEEP_S = 4 * 3600.0     # readings kept for the clients: the lead (2 h), the replay window and a margin
MAX_ROWS = 3600         # rows per /samples answer
MAX_SPEED = 1000.0      # the plant steps at about 700 x real time on a laptop core (2026-10-21)
MAX_BODY = 4096


class LivePlant:
    """One run of the plant, stepped as `N.simulate_multi` steps it and read as `N.record` reads it.
    floor: also keep the floor recording of the console (plant/floor/replay.py FloorRecorder: the
    station states, the tracking table and the values every 2 s from the start of the lead). The
    tracking table only reads the plant, so the readings do not change."""

    def __init__(self, seed, faults=(), keep_s=KEEP_S, floor=False):
        self.seed = seed
        self.p = N.new_plant(seed)
        doc = N.new_plant(seed)                           # the documents, as `record` takes them
        self.wiring = wiring(doc)
        w = self.wiring
        self.tags = sorted({t for n in w.values() for role in ("feeds", "commands", "measures") for t in n[role]})
        self.commands = sorted({t for n in w.values() for t in n["commands"]})
        cmd = set(self.commands)
        self._inst = [i for i, t in enumerate(self.tags) if t not in cmd] if N.INSTRUMENTS else []
        inst = Instruments(N.FACTORY_SEED, noise=N.INSTRUMENTS, ranges=instrument_ranges(doc))
        self._stream = inst.stream([self.tags[i] for i in self._inst], seed)
        self.dt, self.every = N.DT, int(round(N.SAMPLE_S / N.DT))
        self.warmup_s = N.WARMUP_S
        self.rec_from = N.WARMUP_S - min(N.LEAD_S, N.WARMUP_S)
        self.k = 0                                        # steps taken
        self.n = 0                                        # samples taken
        self.samples = deque(maxlen=max(1, int(keep_s / N.SAMPLE_S)))    # (i, plant s, values)
        self.faults = []                                  # the fault log, in the order given
        self._ends = None
        # The batch run applies the range rule to the plant that it runs, before the first step
        # (`N.simulate` for a fault that is not binary, `N.simulate_multi` always). The rule moves the
        # cooling pump's operating point, which gives a small start transient that dies out in the
        # warm-up (LOG-125). Faults planned at the start do the same here, so the run equals the batch
        # run. A fault added later takes the rule from a separate plant.
        if len(faults) > 1 or (faults and faults[0][0] not in FR.BINARY):
            self._ends = RANGES.ends(self.p)
        for f in faults:
            self.add_fault(*f)
        self.floor = FloorRecorder(self.p, int(round(self.rec_from / self.dt))) if floor else None
        self.keep_rows = int(keep_s / FLOOR_SAMPLE_S)

    def add_fault(self, fid, severity, onset_s=None):
        """Plan fault `fid` at `severity` (0 to 1), `onset_s` after the end of the warm-up. None: now,
        or the end of the warm-up while the plant warms up. The magnitude and the duration follow
        `N.simulate`: one range rule for every fault (fault_ranges.py)."""
        if fid not in FR.FAULTS:
            raise ValueError(f"unknown fault {fid!r}: the faults are {', '.join(FR.FAULTS)}")
        if isinstance(severity, bool) or not isinstance(severity, (int, float)) or not 0.0 <= severity <= 1.0:
            raise ValueError("severity must be a number from 0 to 1")
        if onset_s is None:
            onset_s = max(0.0, self.p.t - self.warmup_s)
        if isinstance(onset_s, bool) or not isinstance(onset_s, (int, float)) or not 0.0 <= onset_s < math.inf:
            raise ValueError("onset_s must be a number of seconds from 0")
        if fid not in FR.BINARY and self._ends is None:
            self._ends = RANGES.ends(N.new_plant(self.seed))         # never on the running plant
        f = N.fault_plan(fid, float(severity), float(onset_s), self._ends)
        self.faults.append(f)
        return f

    def clear_fault(self, fid):
        """The operator clears fault `fid`: an active one goes back to healthy now, a planned one is
        cancelled. Returns the number of log entries that changed."""
        changed = 0
        for f in self.faults:
            if f["fid"] != fid:
                continue
            if f["state"] == "on":
                FR.clear(self.p, fid)
                f["state"], f["off_t"] = "cleared", self.p.t
                changed += 1
            elif f["state"] == "planned":
                f["state"] = "cancelled"
                changed += 1
        return changed

    def step(self):
        """One plant step of DT, as `N.simulate_multi` steps it (`N.fault_step`: the faults that are due,
        the step, and a fault that clears inside the step by a sub-step), then a sample every SAMPLE_S
        from the start of the lead."""
        p = self.p
        N.fault_step(p, self.faults, self.dt)
        if self.floor is not None:
            self.floor.step()
            if len(self.floor.rows) > self.keep_rows + 600:           # trim in blocks, not every row
                cut = len(self.floor.rows) - self.keep_rows
                del self.floor.rows[:cut], self.floor.row_t[:cut]
        if p.t >= self.rec_from and self.k % self.every == 0:
            tags = p.tags()
            vals = [tags.get(t) for t in self.tags]
            if self._inst:
                for i, v in zip(self._inst, self._stream.read([vals[i] for i in self._inst])):
                    vals[i] = v
            self.samples.append((self.n, p.t, vals))
            self.n += 1
        self.k += 1

    def columns(self):
        """The kept readings as {tag: list of values}, the form of `N.record` before `N._array`."""
        rows = list(self.samples)
        return {t: [r[2][i] for r in rows] for i, t in enumerate(self.tags)}

    def fault_log(self):
        return [{k: v for k, v in f.items() if not k.startswith("_")} for f in self.faults]


def check_speed(speed):
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.0 < speed <= MAX_SPEED:
        raise ValueError(f"speed must be a number above 0 and up to {MAX_SPEED:g}")
    return float(speed)


class Service:
    """The plant thread and the clock. The plant warms up at full speed. From the end of the warm-up,
    the clock asks for plant time anchor_t + (wall - anchor) x speed, and the plant steps while it is
    behind. If the plant cannot keep up, `lag_s` grows."""

    def __init__(self, plant, speed=1.0):
        self.plant = plant
        self.speed = check_speed(speed)
        self.lock = threading.Lock()
        self.anchor = None                                # (wall s, plant s), set at the end of the warm-up
        self.stop = threading.Event()

    def target(self, now=None):
        """The plant time that the clock asks for, None during the warm-up."""
        if self.anchor is None:
            return None
        wall, t = self.anchor
        return t + ((time.time() if now is None else now) - wall) * self.speed

    def run(self):
        pl = self.plant
        while not self.stop.is_set():
            with self.lock:
                if self.anchor is None and pl.p.t >= pl.warmup_s:
                    self.anchor = (time.time(), pl.p.t)
                tgt = self.target()
                due = tgt is None or pl.p.t + pl.dt <= tgt
                if due:
                    pl.step()
                else:
                    wait = min(0.05, (pl.p.t + pl.dt - tgt) / self.speed)
            if not due:
                time.sleep(wait)

    def set_speed(self, speed):
        speed = check_speed(speed)
        with self.lock:
            if self.anchor is not None:
                self.anchor = (time.time(), self.plant.p.t)   # the new speed starts from now
            self.speed = speed

    def clock(self):
        with self.lock:
            pl, now = self.plant, time.time()
            tgt = self.target(now)
            return {"t": pl.p.t, "t_day_h": pl.p.t_day_h(), "speed": self.speed, "wall": now,
                    "anchor": None if self.anchor is None else self.anchor[0],
                    "anchor_t": None if self.anchor is None else self.anchor[1],
                    "lag_s": 0.0 if tgt is None else max(0.0, tgt - pl.p.t),
                    "warming": pl.p.t < pl.warmup_s, "warmup_s": pl.warmup_s, "sample": pl.n - 1}

    def info(self):
        pl = self.plant
        return {"seed": pl.seed, "split": N.split_of(pl.seed), "day_of_year": N.day_of_year(pl.seed),
                "t_day0_h": pl.p.t_day0_h, "factory": N.FACTORY_SEED, "instruments": N.INSTRUMENTS,
                "warmup_s": pl.warmup_s, "lead_s": pl.warmup_s - pl.rec_from, "sample_s": N.SAMPLE_S,
                "dt": pl.dt, "tags": pl.tags, "commands": pl.commands, "wiring": pl.wiring}

    def samples(self, start, limit):
        with self.lock:
            rows = list(self.plant.samples)
            n = self.plant.n
        first = rows[0][0] if rows else n
        start = max(start, first)
        got = rows[start - first:start - first + limit]
        return {"tags": self.plant.tags, "first": first, "from": start, "next": start + len(got),
                "rows": [[i, t, v] for i, t, v in got]}

    def metrics(self):
        with self.lock:
            last = self.plant.samples[-1] if self.plant.samples else None
            t, speed = self.plant.p.t, self.speed
        out = ["# HELP visr_value A reading of the live plant, the last sample.", "# TYPE visr_value gauge"]
        for tag, v in zip(self.plant.tags, last[2] if last else ()):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out.append(f'visr_value{{tag="{_esc(tag)}",station="{_esc(tag.split(".")[0])}"}} {float(v)!r}')
        out += ["# HELP visr_plant_seconds The plant time.", "# TYPE visr_plant_seconds gauge",
                f"visr_plant_seconds {t!r}",
                "# HELP visr_plant_speed Plant seconds per wall-clock second.", "# TYPE visr_plant_speed gauge",
                f"visr_plant_speed {speed!r}"]
        return "\n".join(out) + "\n"

    def floor(self, since=None):
        """The floor recording after plant time `since`, in the form of plant/floor/replay.py, for the
        console's player to append (None without --floor)."""
        pl = self.plant
        if pl.floor is None:
            return None
        with self.lock:
            seg = pl.floor.segment(since)
            seg["values"]["rows"] = list(seg["values"]["rows"])
            seg["states"], seg["events"] = list(seg["states"]), list(seg["events"])
            seg["items"] = dict(seg["items"])
        seg["meta"] = {"seed": pl.seed, "t_day0_h": pl.p.t_day0_h, "live": True, "dt": pl.dt,
                       "t_start": round(pl.rec_from, 1), "warmup_s": pl.warmup_s, "t_end": seg.pop("t_end"),
                       "sample_s": FLOOR_SAMPLE_S, "state_rules": STATE_RULES}
        return seg

    def add_fault(self, fid, severity, onset_s=None):
        with self.lock:
            f = self.plant.add_fault(fid.upper() if isinstance(fid, str) else fid, severity, onset_s)
            return {k: v for k, v in f.items() if not k.startswith("_")}

    def clear_fault(self, fid):
        with self.lock:
            return self.plant.clear_fault(fid.upper() if isinstance(fid, str) else fid)

    def fault_log(self):
        with self.lock:
            return self.plant.fault_log()


def _esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def handler(svc):
    class H(BaseHTTPRequestHandler):
        timeout = 10          # s. A client that connects and sends nothing frees its thread after this.

        def _send(self, code, body, ctype="text/plain"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            return self._send(code, json.dumps(obj), "application/json")

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("bad Content-Length")
            if not 0 < n <= MAX_BODY:
                raise ValueError(f"the body must be JSON of 1 to {MAX_BODY} bytes")
            try:
                body = json.loads(self.rfile.read(n))
            except ValueError:
                raise ValueError("the body is not valid JSON")
            if not isinstance(body, dict):
                raise ValueError("the body must be a JSON object")
            return body

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self._send(200, "ok\n")
            if u.path == "/info":
                return self._json(200, svc.info())
            if u.path == "/clock":
                return self._json(200, svc.clock())
            if u.path == "/samples":
                q = parse_qs(u.query)
                try:
                    start = int(q.get("from", ["0"])[0])
                    limit = int(q.get("limit", [str(MAX_ROWS)])[0])
                except ValueError:
                    return self._json(400, {"error": "from and limit must be integers"})
                return self._json(200, svc.samples(start, max(1, min(limit, MAX_ROWS))))
            if u.path == "/metrics":
                return self._send(200, svc.metrics(), "text/plain; version=0.0.4")
            if u.path == "/faults":
                return self._json(200, svc.fault_log())
            if u.path == "/floor":
                try:
                    since = parse_qs(u.query).get("since", [None])[0]
                    seg = svc.floor(None if since is None else float(since))
                except ValueError:
                    return self._json(400, {"error": "since must be a number of plant seconds"})
                if seg is None:
                    return self._json(404, {"error": "the service runs without --floor"})
                return self._send(200, json.dumps(seg, separators=(",", ":")), "application/json")
            self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/fault", "/fault/clear", "/speed"):
                return self._json(404, {"error": "not found"})
            try:
                body = self._body()
                if path == "/fault":
                    return self._json(200, svc.add_fault(body.get("fid"), body.get("severity"), body.get("onset_s")))
                if path == "/fault/clear":
                    return self._json(200, {"changed": svc.clear_fault(body.get("fid"))})
                svc.set_speed(body.get("speed"))
                return self._json(200, svc.clock())
            except ValueError as e:
                return self._json(400, {"error": str(e)})

        def log_message(self, *_):
            pass

    return H


def make_server(svc, host="127.0.0.1", port=PORT):
    """The HTTP server, one thread per request (as plant/sim/main.py)."""
    srv = ThreadingHTTPServer((host, port), handler(svc))
    srv.daemon_threads = True
    return srv


# ----------------------------------------------------------------------- the equivalence check --

def compare(seed, hours, faults=()):
    """Step the live plant offline for `hours` after the warm-up and compare its readings with
    `N.record` of the same run, simulated again (no cache). faults: (fid, severity, onset_s) tuples.
    Returns {"bad": tags that differ, "samples", "lead", "tags", "plant": the LivePlant}."""
    import numpy as np
    faults = [(fid, sev, 0.0 if on is None else on) for fid, sev, on in faults]
    fault = None if not faults else tuple(faults[0]) if len(faults) == 1 else tuple(tuple(f) for f in faults)
    pl = LivePlant(seed, faults, keep_s=N.LEAD_S + hours * 3600.0 + 3600.0)
    for _ in range(int((hours * 3600.0 + N.WARMUP_S) / N.DT)):     # the step count of `N.simulate`
        pl.step()
    cache = N.CACHE
    N.CACHE = False
    try:
        w, rows, info = N.record(seed, hours, fault)
    finally:
        N.CACHE = cache
    cols = pl.columns()
    bad = sorted(set(rows) ^ set(cols))
    for t in sorted(set(rows) & set(cols)):
        a, b = N._array(cols[t]), rows[t]
        if a.dtype != b.dtype or not np.array_equal(a, b, equal_nan=b.dtype.kind == "f"):
            bad.append(t)
    lead = sum(1 for _, t, _ in pl.samples if t < pl.warmup_s)
    if w != pl.wiring or lead != info["lead"]:
        bad.append("(documents or lead)")
    mags = [f["magnitude"] for f in info.get("faults", [info])] if faults else []
    if mags != [f["magnitude"] for f in pl.faults]:
        bad.append("(fault magnitudes)")
    return {"bad": bad, "samples": pl.n, "lead": lead, "tags": len(rows), "plant": pl}


def parse_fault(s):
    """FID:SEVERITY[:ONSET_S] -> (fid, severity, onset_s or None)."""
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise argparse.ArgumentTypeError(f"{s!r}: use FID:SEVERITY or FID:SEVERITY:ONSET_S")
    try:
        return parts[0].upper(), float(parts[1]), float(parts[2]) if len(parts) == 3 else None
    except ValueError:
        raise argparse.ArgumentTypeError(f"{s!r}: severity and onset must be numbers")


def main(argv=None):
    ap = argparse.ArgumentParser(description="The live plant service (live/README.md).")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"the run (default {DEFAULT_SEED})")
    ap.add_argument("--speed", type=float, default=1.0, help="plant seconds per wall-clock second")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--keep-h", type=float, default=KEEP_S / 3600.0, help="hours of readings kept")
    ap.add_argument("--fault", type=parse_fault, action="append", default=[], metavar="FID:SEVERITY[:ONSET_S]",
                    help="a planned fault, ONSET_S after the warm-up (repeat for a pair)")
    ap.add_argument("--check", type=float, metavar="HOURS",
                    help="no server: compare HOURS of the live plant with eval/nodes.py record() and exit")
    ap.add_argument("--floor", action="store_true", help="also keep the console's floor recording (GET /floor)")
    a = ap.parse_args(argv)
    if N.split_of(a.seed) == "test":
        ap.error(f"seed {a.seed} is a test-split day: the test split runs only at the freeze (ideas.md 16.2)")
    if a.check is not None:
        t0 = time.time()
        r = compare(a.seed, a.check, a.fault)
        print(f"seed {a.seed}, {a.check:g} h, faults {a.fault or 'none'}: {r['samples']} samples "
              f"({r['lead']} lead), {r['tags']} tags, differing: {', '.join(r['bad']) or 'none'} "
              f"({time.time() - t0:.0f} s)")
        return 1 if r["bad"] else 0
    try:
        svc = Service(LivePlant(a.seed, a.fault, keep_s=a.keep_h * 3600.0, floor=a.floor), a.speed)
    except ValueError as e:
        ap.error(str(e))
    threading.Thread(target=svc.run, daemon=True).start()
    srv = make_server(svc, a.host, a.port)
    print(f"live plant: seed {a.seed} (day {N.day_of_year(a.seed)}), {len(svc.plant.tags)} tags, warm-up "
          f"{N.WARMUP_S / 3600.0:g} h at full speed, then {a.speed:g} x on http://{a.host}:{srv.server_address[1]}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        svc.stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
