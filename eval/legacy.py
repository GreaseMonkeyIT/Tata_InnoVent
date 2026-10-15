"""One scored trial of TODAY'S engine (v1, the box engine) on the box's plant sim (`plant/sim/main.py`).

It runs the engine the way the box does, through `correlation/service.py`: the learned baselines,
the deviation gate, the edge memory, the merge and the forecaster (ps2_lab.py showed the method).
The sequence per trial:

  1. learn   the plant runs normally, a pass every POLL interval, the baselines learn and mature
  2. lock    every baseline is locked (the box: the operator locks after the soak, LOG-089)
  3. calm    a random calm stretch: the onset is not at a fixed time
  4. fault   the fault at a random magnitude (none in a calm run, PS0)
  5. watch   the passes and the trip cards are recorded; OpenPLC's thermal latch is emulated

Each trial must run in its own process: `service` keeps its memory in module state.

Scope (ideas.md 16.7): the plant families only (bus voltage on the rails, coolant temperature on
the loop), so PS1, PS2, PS5, PS7 and calm runs. PS3 (network), PS4A and PS4B (SCADA integrity) and
PS6 (edge plane) run in other layers on the box; they need a box campaign.
"""
import collections
import datetime
import hashlib
import importlib.util
import json
import os
import random
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The box settings for the plant families (deploy/engine.yaml, 2026-10-15), less the families this
# offline run has no data for (psi_*, field_latency).
BOX_ENV = {
    "ENGINE_SIGNALS": "bus_voltage,coolant_temp",
    "PLANT_FAMILIES": "bus_voltage:rail,coolant_temp:loop",
    "PLANT_SOURCES": "bus_voltage:current_draw,coolant_temp:heat_load|cooling_shortfall",
    "PLANT_INVERT": "bus_voltage:400",
    "PLANT_DOMAINS": ("rail:incomer-1=incomer-1,psu-a,psu-b,psu-c;"
                      "rail:psu-a=press-1,press-2,cnc-1,qa-scanner-1,psu-a;"
                      "rail:psu-b=conveyor-1,compressor-1,furnace-1,chiller-1,psu-b;"
                      "loop:cool-1=press-1,press-2,cnc-1,furnace-1,compressor-1,chiller-1,cool-1"),
    "COMMON_MODE": "1", "COMMON_MODE_MIN_MEMBERS": "3", "COMMON_MODE_WINDOW_S": "15",
    "FORECAST_PAIRS": "coolant_temp:temp_limit:trip",
    "EDGE_ALPHA": "0.4", "EDGE_DECAY": "0.1", "EDGE_SHOW": "0.6", "EDGE_HIDE": "0.25",
    "DEV_K": "3.5", "RESET_WINDOW": "24", "GATE_Q": "35", "BASELINE_MIN_COVERAGE": "0.9",
    "MAD_FLOOR_COOLANT_TEMP": "0.3", "FORECAST_HORIZON_S": "900", "FORECAST_MIN_FRAC": "0.5",
    "POLL_S": "5", "ENGINE_INTERVAL": "10",
}

# Scenario -> (true root, fault knob, magnitude range). The scripted magnitude sits inside each range.
# The ranges are project choices: about +-20 to 35 % around the scripted fault.
SCENARIOS = {
    "PS1": ("press-1", "friction", (1.5, 2.3)),        # scripted 1.9
    "PS2": ("compressor-1", "pt_bar", (4.5, 6.5)),     # scripted 5.5 (a reading under the 6.9 bar load point)
    "PS5": ("chiller-1", "pump_health", (0.35, 0.60)), # scripted 0.45
    "PS7": ("incomer-1", "supply", (0.80, 0.90)),      # scripted 0.85
    "PS0": (None, None, None),                         # calm
}
WATCH_S = {"PS1": 900.0, "PS2": 1500.0, "PS5": 900.0, "PS7": 900.0}
TICK, GRID, RING = 1.0, 5.0, 180
T0 = 1_760_000_000.0


def settings_hash():
    """A hash of the engine settings and the engine source, so a score names exactly what it scored."""
    h = hashlib.sha256(json.dumps(BOX_ENV, sort_keys=True).encode())
    for d in ("correlation/engine", "correlation"):
        base = os.path.join(ROOT, d)
        for f in sorted(os.listdir(base)):
            if f.endswith(".py"):
                with open(os.path.join(base, f), "rb") as fh:
                    h.update(f.encode() + fh.read())
    with open(os.path.join(ROOT, "plant", "sim", "main.py"), "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()[:16]


def plan(scenario, seed, learn_s=1800.0):
    """The random draws of one trial, from its seed alone."""
    rng = random.Random(f"trial:{scenario}:{seed}")
    truth, knob, rng_m = SCENARIOS[scenario]
    p = {"scenario": scenario, "seed": seed, "truth": truth, "learn_s": learn_s,
         "settle_s": rng.uniform(300.0, 1200.0)}
    if knob:
        p.update({"knob": knob, "magnitude": rng.uniform(*rng_m), "watch_s": WATCH_S[scenario]})
    else:
        p.update({"knob": None, "magnitude": None, "watch_s": 3600.0})
    return p


def run_trial(p):
    """Run one planned trial in THIS process (call it in a fresh process). Returns the raw record."""
    db = os.path.join(tempfile.mkdtemp(prefix="visr-eval-"), "m.db")
    os.environ.update(BOX_ENV)
    os.environ["MEMORY_DB"] = db
    sys.path.insert(0, os.path.join(ROOT, "correlation"))
    spec = importlib.util.spec_from_file_location("plant_sim", os.path.join(ROOT, "plant", "sim", "main.py"))
    sim = importlib.util.module_from_spec(spec)
    sys.modules["plant_sim"] = sim
    spec.loader.exec_module(sim)
    import service as S                                          # noqa: E402  (after the env)
    from engine.merge import merge_graphs                        # noqa: E402
    from engine.pipeline import run_pass                         # noqa: E402

    cooled = ["press-1", "press-2", "cnc-1", "furnace-1"]
    random.seed(p["seed"])
    _reset(sim)

    def iso(t):
        return datetime.datetime.utcfromtimestamp(t).isoformat(timespec="milliseconds") + "Z"

    def sample(hist, k):
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
            learnable = {q: v for q, v in vectors.items() if cov.get(q, 0.0) >= S.MIN_COVERAGE}
            rendered[sig] = mem.observe(out, vectors, witness=witness, baseline_vectors=learnable)
        g = merge_graphs(rendered, primary=S.PRIMARY) if rendered else {"edges": [], "root_cause_ranking": []}
        g["incipient"] = S.forecast_cards(vec_by_sig)
        return g

    hist = collections.defaultdict(lambda: collections.deque(maxlen=RING))
    onset = p["learn_s"] + p["settle_s"]
    end = onset + p["watch_s"]
    pass_every = int(float(BOX_ENV["ENGINE_INTERVAL"]) / GRID)
    t, k, locked, applied = 61.0, 0, False, False
    passes, trips = [], {}
    while k * GRID < end:
        now = k * GRID
        if not locked and now >= p["learn_s"]:
            for m in S._memory.values():
                m.locked = True
            locked = True
        if p["knob"] and not applied and now >= onset:
            _apply(sim, p["knob"], p["magnitude"])
            applied = True
        for _ in range(int(GRID / TICK)):
            sim.step_world(t, TICK)
            for n in cooled:                             # OpenPLC: a cooled machine at its trip latches
                d = sim.BASE_BY_NAME[n]
                if d.temp >= d.trip_c and not d.tripped:
                    d.tripped = True
                    if applied:
                        trips.setdefault(n, now)         # run time, as the pass times
            t += TICK
        sample(hist, k)
        if k % pass_every == 0 and now >= p["learn_s"] * 0.5:
            g = one_pass(hist)
            if locked:
                passes.append({"t": now, "ranking": [r["pod"] for r in g.get("root_cause_ranking", [])],
                               "cards": [(c["pod"], c["eta_s"]) for c in (g.get("incipient") or [])]})
        k += 1
    rec = dict(p)
    rec.update({"onset_t": onset if p["knob"] else None, "passes": passes, "trips": trips,
                "lock_t": p["learn_s"]})
    return rec


def _reset(sim):
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


def _apply(sim, knob, m):
    if knob == "friction":
        sim.BY_NAME["press-1"].friction = m
    elif knob == "pt_bar":
        sim.AIR.pt_fault = m
    elif knob == "pump_health":
        sim.LOOP.pump_health = m
    elif knob == "supply":
        sim.SUPPLY.target = m
    else:
        raise ValueError(knob)


def run_in_child(p):
    """Entry for a fresh worker process: run, then return a JSON-safe record."""
    return json.loads(json.dumps(run_trial(p)))
