#!/usr/bin/env python3
"""PS2 two-hop lab: the real plant model through the REAL engine pipeline, stepped in time.

`replay_offline.py` runs ONE pass of ONE family and answers "who is the root". PS2 does not fail
on the root. It fails on TIMING: the rail hop (compressor-1 -> chiller-1) and the loop hop
(chiller-1 -> a cooled machine) have to be up in the SAME poll, and on the box they were not
(LOG-070, LOG-071). This file runs both families with the real GraphMemory, the real learned
baselines, the real merge, and the real forecaster, and it runs a pass every 20 s so the window
where both hops coexist becomes a measurable number.

It also emulates the OpenPLC thermal latch, which is the whole point. `plc/program.st` latches a
cooled machine at 78.0 C, its contactor opens, and rail B unloads. That is what kills the sag and
with it the rail hop. An engine-only replay leaves the PLC out and never sees the failure.

READ THIS BEFORE TRUSTING A NUMBER. The lab is NOT the box:
  - It runs two signals. The box runs six, with `psi_io` primary, and the merge across six can
    move the root. Plane 1 cannot be simulated honestly here, so it is left out.
  - PS2 PASSES in this lab even at the old 0.45, while it fails on the box. So the lab measures
    RELATIVE improvement between settings. It never predicts a box pass.
  - Each trial needs its own process. `service._memory` is a live SQLite GraphMemory, and a
    second trial in the same process inherits the first trial's baselines and edge memory.

Run one setting (prints the poll-by-poll trace):
    python correlation/tests/ps2_lab.py 0.60

Sweep (one process per value, as above):
    for r in 0.45 0.50 0.60 0.70; do python correlation/tests/ps2_lab.py $r | tail -3; done
"""
import collections, datetime, importlib, importlib.util, os, random, sys, tempfile

R = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))   # the repo root
DB = os.path.join(tempfile.mkdtemp(prefix="ps2lab"), "m.db")
os.environ.update({
    "MEMORY_DB": DB,
    "ENGINE_SIGNALS": "bus_voltage,coolant_temp",
    "PLANT_FAMILIES": "bus_voltage:rail,coolant_temp:loop",
    "PLANT_SOURCES": "bus_voltage:current_draw,coolant_temp:heat_load|cooling_shortfall",
    "PLANT_INVERT": "bus_voltage:400",
    "PLANT_DOMAINS": ("rail:incomer-1=incomer-1,psu-a,psu-b,psu-c;"
                      "rail:psu-a=press-1,press-2,cnc-1,qa-scanner-1,psu-a;"
                      "rail:psu-b=conveyor-1,compressor-1,furnace-1,chiller-1,psu-b;"
                      "loop:cool-1=press-1,press-2,cnc-1,furnace-1,chiller-1,cool-1"),
    "FORECAST_PAIRS": "coolant_temp:temp_limit:trip",
    "GATE_Q": "35", "BASELINE_MIN_COVERAGE": "0.9", "MAD_FLOOR_COOLANT_TEMP": "0.3",
    "RESET_WINDOW": "24", "DEV_K": "3.5", "POLL_S": "5", "ENGINE_INTERVAL": "10",
    "COMMON_MODE": "1",
})
sys.path.insert(0, os.path.join(R, "correlation"))
spec = importlib.util.spec_from_file_location("plant_sim", os.path.join(R, "plant", "sim", "main.py"))
sim = importlib.util.module_from_spec(spec); sys.modules["plant_sim"] = sim; spec.loader.exec_module(sim)
import service as S                                              # noqa: E402
from engine.merge import merge_graphs                            # noqa: E402
from engine.pipeline import run_pass                             # noqa: E402
from engine.forecast import incipient_findings                   # noqa: E402

TICK, GRID, RING = 1.0, 5.0, 180
T0 = 1_760_000_000.0
COOLED = ["press-1", "press-2", "cnc-1", "furnace-1"]


def openplc_tick():
    """What the real OpenPLC runtime does over Modbus every tick (plc/program.st lines 25 to 28):
    a cooled machine whose coolant temperature reaches 78.0 C trips, and the trip LATCHES.
    Leaving this out is why an engine-only replay never reproduces the PS2 failure."""
    for n in COOLED:
        d = sim.BASE_BY_NAME[n]
        if d.temp >= sim.TRIP_C:
            d.tripped = True


def iso(t):
    return datetime.datetime.utcfromtimestamp(t).isoformat(timespec="milliseconds") + "Z"


def reset(seed, residual):
    random.seed(seed)
    sim.CHILLER_RESIDUAL_FLOW = residual
    sim.SUPPLY.target, sim.SUPPLY.voltage = 1.0, sim.SUPPLY.v_nom
    for r in sim.RAILS:
        r.voltage = r.v_nom
    for d in sim.DEVICES:
        d.friction, d.temp, d.current, d.throughput, d.tripped = 1.0, 35.0, 0.0, 100.0, False
        if d.overload is not None:
            d.overload.reset()
    sim.BY_NAME["compressor-1"].duty_fn = sim.compressor_duty
    sim.LOOP.pump_health, sim.LOOP.flow = 1.0, sim.LOOP.flow_nominal
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()


def sample(hist, k):
    """One 5 s aggregator sample of every series the two families need."""
    ts = iso(T0 + k * GRID)
    def put(pod, signal, value):
        hist[("plant", pod, signal)].append({"ts": ts, "value": float(value)})
    put(sim.SUPPLY.name, "bus_voltage", sim.SUPPLY.voltage)
    put(sim.SUPPLY.name, "current_draw", sum(d.current for d in sim.DEVICES))
    for r in sim.RAILS:
        put(r.name, "bus_voltage", r.voltage)
    for d in sim.DEVICES:
        put(d.name, "bus_voltage", d.rail.voltage)
        put(d.name, "current_draw", d.current)
        if d.loop is not None:
            put(d.name, "coolant_temp", d.temp)
            put(d.name, "heat_load", d.heat_k * d.current)
    put("chiller-1", "cooling_shortfall", sim.cooling_shortfall())
    put("cool-1", "temp_limit", sim.TRIP_C)


def one_pass(hist):
    """Exactly what service.loop() does for one iteration, minus the HTTP."""
    window = {"%s/%s/%s" % k: list(v) for k, v in hist.items()}
    vec_by_sig, breach, coverage = S.build_inputs(window, [])
    rendered = {}
    for sig in S.SIGNALS:
        vectors = vec_by_sig.get(sig) or {}
        if not vectors:
            continue
        mem = S._memory[sig]
        write_vectors = S.source_vectors(sig, vec_by_sig)
        pair_over = {**(write_vectors or {}), **vectors} if sig in S.PLANT_FAMILIES else vectors
        witness = S._witness_for(sig, pair_over)
        baselines = {pod: mem.baseline_threshold(S.workload(pod)) for pod in vectors}
        out = run_pass(vectors, witness, slo_breach=breach or None, window=S.ANALYSIS_WINDOW,
                       write_vectors=write_vectors, baselines=baselines, recent=S.RESET_WINDOW,
                       gate_q=S.GATE_Q, bare_src_must_deviate=sig in S.PLANT_FAMILIES,
                       common_mode=S.common_mode_arg(sig))
        cov = coverage.get(sig) or {}
        learnable = {p: v for p, v in vectors.items() if cov.get(p, 0.0) >= S.MIN_COVERAGE}
        rendered[sig] = mem.observe(out, vectors, witness=witness, baseline_vectors=learnable)
    g = merge_graphs(rendered, primary=S.PRIMARY) if rendered else {"edges": [], "root_cause_ranking": []}
    incip = []
    for fsig, flim, fcls in S.FORECAST_PAIRS:
        sig_vec, lim_vec = vec_by_sig.get(fsig) or {}, vec_by_sig.get(flim) or {}
        limits = {p: float(v[-1]) for p, v in lim_vec.items() if len(v) and v[-1] > 0}
        if len(limits) == 1 and sig_vec:
            only = next(iter(limits.values()))
            limits = {p: limits.get(p, only) for p in sig_vec}
        incip.extend(incipient_findings(sig_vec, limits, horizon_s=S.FORECAST_HORIZON_S,
                                        min_frac=S.FORECAST_MIN_FRAC, signal=fsig, cls=fcls,
                                        floors=S.forecast_floors(fsig, sig_vec)))
    g["incipient"] = incip
    return g


def trial(residual, warm_s=1500, watch_s=560, pass_every_s=20, seed=99, verbose=True):
    for m in S._memory.values():                      # a clean memory per trial
        m.edges.clear() if hasattr(m, "edges") else None
    reset(seed, residual)
    hist = collections.defaultdict(lambda: collections.deque(maxlen=RING))
    t, k = 61.0, 0
    fired_at = None
    rows = []
    total = int((warm_s + watch_s) / GRID)
    for k in range(total):
        if fired_at is None and k * GRID >= warm_s:
            sim.FAULTS["PS2"]["apply"](); sim.ACTIVE.add("PS2"); fired_at = k * GRID
        for _ in range(int(GRID / TICK)):
            sim.SUPPLY.step(sum(d.current for d in sim.DEVICES), TICK)
            for r in sim.RAILS:
                r.step(sum(d.current for d in sim.DEVICES if d.rail is r))
            sim.LOOP.step()
            for d in sim.DEVICES:
                d.step(t, TICK)
            openplc_tick()
            t += TICK
        sample(hist, k)
        if k * GRID < warm_s or (k * GRID - warm_s) % pass_every_s:
            continue
        g = one_pass(hist)
        el = k * GRID - fired_at
        edges = g.get("edges", [])
        roots = g.get("root_cause_ranking", [])
        hop1 = any(e["src"] == "compressor-1" and e["dst"] == "chiller-1" for e in edges)
        # deploy/proof-run.sh ps2_chain(): root compressor-1 AND an edge from chiller-1 with
        # "loop" evidence, in the SAME poll. Score exactly what the box scores.
        loop_hop = [e for e in edges if e.get("src") == "chiller-1"
                    and "loop" in (e.get("evidence") or [])]
        rows.append({
            "t": el, "root": roots[0]["pod"] if roots else None,
            "hop1": hop1, "hop2": bool(loop_hop),
            "pass": bool(roots) and roots[0]["pod"] == "compressor-1" and bool(loop_hop),
            "cards": len(g.get("incipient") or []),
            "flow": sim.LOOP.flow, "chiller_tripped": sim.BY_NAME["chiller-1"].tripped,
            "trips": [d.name for d in sim.DEVICES if d.tripped and d.name in COOLED],
            "maxtemp": max(d.temp for d in sim.DEVICES if d.loop is not None),
        })
        if verbose:
            r = rows[-1]
            print("  t+%-4.0f %-4s root=%-13s hop1=%-5s loop=%-5s cards=%d flow=%5.1f maxT=%5.1f trips=%s"
                  % (r["t"], "PASS" if r["pass"] else "", r["root"], r["hop1"], r["hop2"],
                     r["cards"], r["flow"], r["maxtemp"], ",".join(r["trips"]) or "-"))
    return rows, [r["t"] for r in rows if r["pass"]]


def longest_run(times, step=20.0):
    """The longest contiguous stretch of passing polls, in seconds, and where it starts."""
    if not times:
        return 0.0, None
    best = cur = 1
    start = best_start = times[0]
    for a, b in zip(times, times[1:]):
        if b - a <= step + 1e-6:
            cur += 1
        else:
            cur, start = 1, b
        if cur > best:
            best, best_start = cur, start
    return (best - 1) * step, best_start


if __name__ == "__main__":
    res = float(sys.argv[1]) if len(sys.argv) > 1 else sim.CHILLER_RESIDUAL_FLOW
    print("CHILLER_RESIDUAL_FLOW = %.2f   (sim default %.2f)" % (res, sim.CHILLER_RESIDUAL_FLOW))
    rows, ok = trial(res)
    dur, start = longest_run(ok)
    trips = sorted({n for r in rows for n in r["trips"]})
    print()
    print("  proof-run PS2 predicate first met at: %s" % (("t+%.0f" % ok[0]) if ok else "NEVER"))
    print("  longest unbroken window: %.0f s from t+%s" % (dur, start))
    print("  cooled machines that latched: %s" % (", ".join(trips) or "none"))
