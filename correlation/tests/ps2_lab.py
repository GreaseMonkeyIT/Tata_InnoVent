#!/usr/bin/env python3
"""PS2 two-hop lab: the real plant model through the REAL engine pipeline, stepped in time.

`replay_offline.py` runs ONE pass of ONE family and answers "who is the root". PS2 does not fail
on the root. It fails on TIMING: the rail hop (compressor-1 -> chiller-1) and the loop hop
(chiller-1 -> a cooled machine) have to be up in the SAME poll, and on the box they were not
(LOG-070, LOG-071). This file runs both families with the real GraphMemory, the real learned
baselines, the real merge, and the real forecaster, and it runs a pass every 20 s so the window
where both hops coexist becomes a measurable number.

It also emulates the OpenPLC thermal latch, which is the whole point. `plc/program.st` latches a
cooled machine at its own trip (LOG-100: 80 C press and cnc, 55 C furnace coil water), its
contactor opens, and its heat leaves the loop. An engine-only replay leaves the PLC out.

LOG-100 changed PS2: the compressor's pressure transducer fails low, the compressor stays loaded,
its heat (water-cooled, 80 % of its input) exceeds chiller-1's capacity, and the supply water warms
every cooled machine. The chiller does not trip. The cascade is slow: furnace-1 trips about 16 min
after the fault. The lab now watches 1200 s by default and scores both hops from compressor-1.

READ THIS BEFORE TRUSTING A NUMBER. The lab is NOT the box:
  - It runs two signals. The box runs six, with `psi_io` primary, and the merge across six can
    move the root. Plane 1 cannot be simulated honestly here, so it is left out.
  - PS2 PASSES in this lab even at the old 0.45, while it fails on the box. So the lab measures
    RELATIVE improvement between settings. It never predicts a box pass.
  - Each trial needs its own process. `service._memory` is a live SQLite GraphMemory, and a
    second trial in the same process inherits the first trial's baselines and edge memory.

Run one setting (prints the poll-by-poll trace):
    python correlation/tests/ps2_lab.py [watch_s]
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
                      "loop:cool-1=press-1,press-2,cnc-1,furnace-1,compressor-1,chiller-1,cool-1"),
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
    """What the real OpenPLC runtime does over Modbus every tick (plc/program.st): a cooled machine
    at its own trip (LOG-100: TRIP_LIMITS) trips, and the trip LATCHES."""
    for n in COOLED:
        d = sim.BASE_BY_NAME[n]
        if d.temp >= d.trip_c:
            d.tripped = True


def iso(t):
    return datetime.datetime.utcfromtimestamp(t).isoformat(timespec="milliseconds") + "Z"


def reset(seed, residual=None):
    random.seed(seed)
    sim.SUPPLY.target, sim.SUPPLY.voltage = 1.0, sim.SUPPLY.v_nom
    for r in sim.RAILS:
        r.voltage = r.v_nom
    for d in sim.DEVICES:
        d.friction, d.current, d.throughput, d.tripped = 1.0, 0.0, 100.0, False
        d.temp = sim.LOOP_T_SETPOINT + d.heat_k * d.i_base if d.loop is not None else sim.LOOP_T_SETPOINT
        if d.overload is not None:
            d.overload.reset()
    unit = sim.BY_NAME["chiller-1"].unit
    unit.running, unit.last_start, unit.q_removed = True, -1e9, 0.0
    unit.uv.reset()
    sim.AIR.pressure, sim.AIR.loaded, sim.AIR.pt_fault = 7.2, False, None
    sim.LOOP.pump_health, sim.LOOP.flow, sim.LOOP.t_supply = 1.0, sim.LOOP.flow_nominal, sim.LOOP_T_SETPOINT
    for fid in list(sim.ACTIVE):
        sim.FAULTS[fid]["clear"]()
    sim.ACTIVE.clear()


def step_plant(t):
    """One plant tick with the OpenPLC latch. Returns the next t."""
    sim.step_world(t, TICK)
    openplc_tick()
    return t + TICK


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
            put(d.name, "temp_limit", d.trip_c)
        if d.heat_loop is not None:
            put(d.name, "heat_load", 1000.0 * d.heat_kw())
    put("chiller-1", "cooling_shortfall", sim.cooling_shortfall())


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
    g["incipient"] = S.forecast_cards(vec_by_sig)
    return g


def trial(residual=None, warm_s=1500, watch_s=1200, pass_every_s=20, seed=99, verbose=True):
    for m in S._memory.values():                      # a clean memory per trial
        m.edges.clear() if hasattr(m, "edges") else None
    reset(seed)
    hist = collections.defaultdict(lambda: collections.deque(maxlen=RING))
    t, k = 61.0, 0
    fired_at = None
    rows = []
    total = int((warm_s + watch_s) / GRID)
    for k in range(total):
        if fired_at is None and k * GRID >= warm_s:
            sim.FAULTS["PS2"]["apply"](); sim.ACTIVE.add("PS2"); fired_at = k * GRID
        for _ in range(int(GRID / TICK)):
            t = step_plant(t)
        sample(hist, k)
        if k * GRID < warm_s or (k * GRID - warm_s) % pass_every_s:
            continue
        g = one_pass(hist)
        el = k * GRID - fired_at
        edges = g.get("edges", [])
        roots = g.get("root_cause_ranking", [])
        # LOG-100: the rail hop is compressor-1 -> a rail B member, the loop hop compressor-1 (its heat)
        # or chiller-1 (its lost capacity) -> a cooled machine with "loop" evidence, in the same poll.
        hop1 = any(e["src"] == "compressor-1" and "rail" in (e.get("evidence") or []) for e in edges)
        loop_hop = [e for e in edges if e.get("src") in ("compressor-1", "chiller-1")
                    and "loop" in (e.get("evidence") or [])]
        rows.append({
            "t": el, "root": roots[0]["pod"] if roots else None,
            "hop1": hop1, "hop2": bool(loop_hop),
            "pass": bool(roots) and roots[0]["pod"] == "compressor-1" and bool(loop_hop),
            "cards": len(g.get("incipient") or []),
            "flow": sim.LOOP.flow, "supply": sim.LOOP.t_supply,
            "chiller_tripped": sim.BY_NAME["chiller-1"].tripped,
            "trips": [d.name for d in sim.DEVICES if d.tripped and d.name in COOLED],
            "maxtemp": max(d.temp for d in sim.DEVICES if d.loop is not None),
        })
        if verbose:
            r = rows[-1]
            print("  t+%-4.0f %-4s root=%-13s hop1=%-5s loop=%-5s cards=%d supply=%5.2f maxT=%5.1f trips=%s"
                  % (r["t"], "PASS" if r["pass"] else "", r["root"], r["hop1"], r["hop2"],
                     r["cards"], r["supply"], r["maxtemp"], ",".join(r["trips"]) or "-"))
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
    watch = float(sys.argv[1]) if len(sys.argv) > 1 else 1200.0
    print("PS2 (LOG-100): pressure transducer fails low, watch %.0f s" % watch)
    rows, ok = trial(watch_s=watch)
    dur, start = longest_run(ok)
    trips = sorted({n for r in rows for n in r["trips"]})
    print()
    print("  proof-run PS2 predicate first met at: %s" % (("t+%.0f" % ok[0]) if ok else "NEVER"))
    print("  longest unbroken window: %.0f s from t+%s" % (dur, start))
    print("  cooled machines that latched: %s" % (", ".join(trips) or "none"))
