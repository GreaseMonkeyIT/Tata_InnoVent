#!/usr/bin/env python3
"""L3 correlation service (P4).

Polls the L2 aggregator's /window (per-pod signal vectors) and /events (anomaly
seeds), builds the engine inputs, runs one deterministic pass, and serves the
latest CausalGraph at /graph. No language model anywhere in this process; the
single LLM lives at L4.

v0 witness construction (until Caretta/OBI land): shared-storage relations come
from the known storage-domain workloads (one physical disk via local-path), and
PSI co-pressure comes from pods whose signal is elevated in the same window.
"""
import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

from engine.forecast import incipient_findings
from engine.gate import Witness
from engine.merge import merge_graphs
from engine.pipeline import run_pass
from engine.state import GraphMemory, MemoryConfig

WINDOW_URL = os.environ.get("WINDOW_URL", "http://aggregator.aiops.svc:9000/window")
EVENTS_URL = os.environ.get("EVENTS_URL", "http://aggregator.aiops.svc:9000/events")
SIGNALS    = [s.strip() for s in os.environ.get("ENGINE_SIGNALS", "psi_io,psi_cpu,psi_mem").split(",") if s.strip()]
PRIMARY    = SIGNALS[0]                                          # keeps the original memory-db path
# Resource-class source/aggressor signal per psi class -> writer/hog leads staller (source attribution).
# psi_mem has none: a memory leak is self-caused (source == victim), so it forms no cross-pod edge --
# instead it gets an OOM FORECAST: project working_set to the pod's memory limit (FORECAST_* below).
SIGNAL_SOURCES = {"psi_io": "io_write", "psi_cpu": "cpu"}

# ---- 2C' plant families (LOG-029/033): config + service wiring ONLY, run_pass untouched. ----
# PLANT_FAMILIES: victim family -> its shared-medium prefix in PLANT_DOMAINS (also the witness
#   relation_kind, so the evidence chip reads "rail"/"loop", never "pvc").
# PLANT_SOURCES:  victim family -> aggressor signal (role asymmetry gives direction: the
#   load-carrier leads — current_draw for rails, heat_load for the loop).
# PLANT_DOMAINS:  declared memberships. Plant entities NEVER inherit the pods' same-node blanket;
#   no shared domain -> no edge (safe by construction — the 2C' regression fixture).
# PLANT_INVERT:   families where LOWER is worse (bus_voltage): ingest as sag = nominal - value so
#   deviation/onset detection keeps its "higher = worse" semantics engine-wide.
def _parse_map(env, default):
    raw = os.environ.get(env, default)
    return {k.strip(): v.strip() for k, v in
            (item.split(":", 1) for item in raw.split(",") if ":" in item)}

PLANT_FAMILIES = _parse_map("PLANT_FAMILIES", "bus_voltage:rail,coolant_temp:loop")
PLANT_SOURCES  = _parse_map("PLANT_SOURCES", "bus_voltage:current_draw,coolant_temp:heat_load")
PLANT_INVERT   = {k: float(v) for k, v in _parse_map("PLANT_INVERT", "bus_voltage:400").items()}
_DOMAINS_RAW = os.environ.get(
    "PLANT_DOMAINS",
    "rail:psu-a=press-1,press-2,cnc-1,qa-scanner-1,psu-a;"
    "rail:psu-b=conveyor-1,compressor-1,furnace-1,chiller-1,psu-b;"
    "loop:cool-1=press-1,press-2,cnc-1,furnace-1,cool-1")
PLANT_DOMAINS: dict[str, dict[str, set]] = {}      # prefix -> {domain: {members}}
for _d in _DOMAINS_RAW.split(";"):
    if "=" not in _d:
        continue
    _name, _members = _d.split("=", 1)
    _prefix = _name.split(":", 1)[0].strip()
    PLANT_DOMAINS.setdefault(_prefix, {})[_name.strip()] = {
        m.strip() for m in _members.split(",") if m.strip()}
PLANT_ENTITIES = {m for doms in PLANT_DOMAINS.values() for mem in doms.values() for m in mem}
SIGNAL_SOURCES.update(PLANT_SOURCES)

FORECAST_SIGNAL = os.environ.get("FORECAST_SIGNAL", "mem")           # working_set bytes: the OOM ramp
FORECAST_LIMIT  = os.environ.get("FORECAST_LIMIT", "mem_limit")      # memory limit (kube-state): the cap
# ramp-to-limit pairs: signal:limit:class. Pair 1 = the OOM card (S5); pair 2 = the PS5 thermal
# trip card (coolant_temp ramps to the loop-wide trip threshold; single-entity limit broadcast).
FORECAST_PAIRS = [p.split(":") for p in os.environ.get(
    "FORECAST_PAIRS", f"{FORECAST_SIGNAL}:{FORECAST_LIMIT}:leak,coolant_temp:temp_limit:trip"
).split(",") if p.count(":") == 2]
FORECAST_HORIZON_S = float(os.environ.get("FORECAST_HORIZON_S", "900"))  # warn only if OOM is within this window
FORECAST_MIN_FRAC  = float(os.environ.get("FORECAST_MIN_FRAC", "0.5"))   # warn only once working_set is past this fraction of the limit (drops transient/low-level climbs); 0.5 = S5-verified earlier card (LOG-093), do not go lower (re-admits the LOG-087 false cards)
INTERVAL   = int(os.environ.get("ENGINE_INTERVAL", "10"))        # seconds between passes
PORT       = int(os.environ.get("ENGINE_PORT", "9100"))
COPR_MIN   = float(os.environ.get("COPRESSURE_MIN", "0.10"))     # signal level that counts as "stalled"
ANALYSIS_WINDOW = int(os.environ.get("ANALYSIS_WINDOW", "36"))   # samples (~3min): the WHOLE pass (detect+correlate+order) looks back over the recent disturbance, not the 15-min ring. Match to event timescale; not a resource limit.
RESET_WINDOW    = int(os.environ.get("RESET_WINDOW", "24"))      # samples (~2min): an onset is a CURRENT incident only if the pod still deviates within this recent tail -> the verdict clears ~RESET_WINDOW after a storm ends, not when it scrolls out of the 15-min ring
GRID_STEP_S = float(os.environ.get("POLL_S", "5"))               # aggregator scrape cadence = the time-alignment grid step (resample all pods onto a shared wall-clock axis)
MEMORY_DB  = os.environ.get("MEMORY_DB", "/var/lib/skn/memory/l3-memory.db")
STORAGE    = [s.strip() for s in os.environ.get(
    "STORAGE_WORKLOADS", "cooling-monitor,dcim-bridge,log-archiver,timescaledb").split(",")]

def _config(signal):
    """One MemoryConfig per signal. Only the disk domain (psi_io) has a STABLE coupling topology
    (the shared-PVC quartet), so only it keeps a structural backbone. CPU/mem contention is
    transient with no fixed topology, so those edges are PURELY LIVE (no prior, no floor) -- they
    appear during real contention and vanish after, instead of accreting a false permanent backbone."""
    structural = signal == "psi_io"
    return MemoryConfig(
        signal=signal,
        alpha=float(os.environ.get("EDGE_ALPHA", "0.4")),
        decay=float(os.environ.get("EDGE_DECAY", "0.1")),
        show=float(os.environ.get("EDGE_SHOW", "0.6")),
        hide=float(os.environ.get("EDGE_HIDE", "0.25")),
        prior=float(os.environ.get("EDGE_PRIOR", "0.2")) if structural else 0.0,
        floor_frac=float(os.environ.get("EDGE_FLOOR_FRAC", "0.4")) if structural else 0.0,
        tau_merge=float(os.environ.get("CASE_TAU_MERGE", "0.85")),
        tau_family=float(os.environ.get("CASE_TAU_FAMILY", "0.60")),
        base_alpha=float(os.environ.get("BASE_ALPHA", "0.05")),
        dev_k=float(os.environ.get("DEV_K", "4.0")),
        mad_floor=float(os.environ.get("MAD_FLOOR", "0.01")),
        base_min_n=int(os.environ.get("BASE_MIN_N", "12")),
    )


def _db_for(signal):
    """Each signal gets its own SQLite memory. The PRIMARY keeps the original path so existing
    psi_io history (edges, cases, baselines) carries over; the others get a `.<signal>` suffix."""
    if signal == PRIMARY:
        return MEMORY_DB
    base, ext = os.path.splitext(MEMORY_DB)
    return f"{base}.{signal}{ext}"


# One independent causal memory per resource class (different witness, different case family).
_memory = {s: GraphMemory(_db_for(s), _config(s)) for s in SIGNALS}
_lock = threading.Lock()
_graph = merge_graphs({s: m.bootstrap_graph() for s, m in _memory.items()}, primary=PRIMARY)


def _fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def workload(pod):
    """cooling-monitor-59584cbf7d-6szhd -> cooling-monitor (drop replicaset + pod hash).
    Plant entities are NOT k8s pods — their names carry no replicaset/pod hash, so the stripping
    heuristic would mangle them (qa-scanner-1 -> "qa", cross-keying baselines). Declared domain
    membership is the discriminator: a known plant entity keeps its name verbatim."""
    if pod in PLANT_ENTITIES:
        return pod
    parts = pod.split("-")
    return "-".join(parts[:-2]) if len(parts) > 2 else pod


def _epoch(ts):
    """Aggregator stamps each sample with its poll time (Go RFC3339). -> epoch seconds, or None."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def build_inputs(window, events):
    """window: {ns/pod/signal: [{ts,value,...}]}  ->  ({signal: {pod: vec}}, breach), TIME-ALIGNED.

    Collects every psi signal in SIGNALS plus each resource's source signal (io_write, cpu),
    resampled onto ONE shared wall-clock grid by each sample's `ts`. The aggregator ring is a
    positional append, but psi is gappy and pods restart, so column i drifts across pods; the grid
    makes column k the same instant for all pods -- the precondition lagged cross-correlation
    assumes. Stale pods (last sample older than the grid) drop out for free (retires LOG-048).
    """
    step, n = GRID_STEP_S, 180
    # victims + their source/aggressor signals + every ramp-to-limit pair's signal & limit
    wanted = set(SIGNALS) | set(SIGNAL_SOURCES.values())
    for sig, lim, _cls in FORECAST_PAIRS:
        wanted |= {sig, lim}
    raw = {sig: {} for sig in wanted}
    latest = 0.0
    for key, samples in window.items():
        parts = key.split("/")
        if len(parts) < 3 or parts[-1] not in wanted or not samples:
            continue
        # inverted families (bus_voltage): ingest sag = nominal - value, so "higher = worse"
        # holds engine-wide (baselines, onsets, and the positive-coupling gate all assume it)
        inv = PLANT_INVERT.get(parts[-1])
        pts = sorted((t, (inv - s["value"]) if inv is not None else s["value"])
                     for s in samples if (t := _epoch(s.get("ts"))) is not None)
        if len(pts) >= 12:
            raw[parts[-1]][parts[1]] = pts
            latest = max(latest, pts[-1][0])

    grid = [latest - step * (n - 1 - k) for k in range(n)]

    def to_vectors(per_pod):                          # resample one signal's pods onto the shared grid
        out = {}
        for pod, pts in per_pod.items():
            if pts[-1][0] < latest - 2 * step:        # stale/dead pod -> drop (no recent data)
                continue
            vec, j = np.full(n, np.nan), 0
            for k, gt in enumerate(grid):             # sample-and-hold onto the shared grid
                while j + 1 < len(pts) and pts[j + 1][0] <= gt + step / 2:
                    j += 1
                if abs(pts[j][0] - gt) <= step:
                    vec[k] = pts[j][1]
            if np.count_nonzero(~np.isnan(vec)) >= 12:  # real coverage; a gap == no activity == 0
                out[pod] = np.nan_to_num(vec, nan=0.0)
        return out

    vec_by_sig = {sig: to_vectors(raw[sig]) for sig in wanted}
    breach = sorted({e["pod"] for e in events if isinstance(e, dict) and e.get("kind") == "anomaly_candidate"})
    return vec_by_sig, breach


def _witness_for(signal, vectors):
    """Per-signal physical witness.
    - psi_io: disk (pvc) coupling among the storage quartet -> admits I/O cascade edges.
    - psi_cpu / psi_mem: same-node coupling (single node = one CPU/mem contention domain) ->
      admits a SOURCE-attributed edge only (the aggressor's usage leads a co-resident's stall, with
      NO network edge -- the S3 'mesh-blind' case). A bare psi pair still forms no edge (same-node
      is excluded from gate.couples), preserving the LOG-061 false-positive fix.
    psi co-pressure stays corroboration only.
    - plant families (2C'): coupling comes ONLY from the declared shared-medium domains
      (rail:/loop: registry) — no same-node blanket (plant entities aren't pods), no co-pressure
      corroboration (the domain is declared, not inferred). Co-deviation without a shared domain
      forms NO edge, by construction. relation_kind carries the honest evidence label."""
    pods = list(vectors)
    prefix = PLANT_FAMILIES.get(signal)
    if prefix is not None:
        shared = set()
        for members in PLANT_DOMAINS.get(prefix, {}).values():
            present = [p for p in pods if p in members]
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    shared.add(frozenset((present[i], present[j])))
        return Witness(ebpf_edges=set(), psi_copressure=set(), shared_relation=shared,
                       same_node=set(), relation_kind=prefix)
    shared, copr, same_node = set(), set(), set()
    for i in range(len(pods)):
        for j in range(i + 1, len(pods)):
            a, b = pods[i], pods[j]
            if signal == "psi_io":
                if workload(a) in STORAGE and workload(b) in STORAGE:
                    shared.add(frozenset((a, b)))               # same physical disk (local-path)
            else:
                same_node.add(frozenset((a, b)))                # single node -> one CPU/mem domain (multi-node: scope by node label)
    hot = [p for p in pods if float(np.max(vectors[p][-6:])) > COPR_MIN]
    for i in range(len(hot)):
        for j in range(i + 1, len(hot)):
            copr.add(frozenset((hot[i], hot[j])))               # single node => same PSI domain (corroboration)
    return Witness(ebpf_edges=set(), psi_copressure=copr, shared_relation=shared, same_node=same_node)


def loop():
    global _graph
    while True:
        try:
            window = _fetch(WINDOW_URL)
            events = _fetch(EVENTS_URL)
            vec_by_sig, breach = build_inputs(window, events)
            rendered = {}                                  # {signal: rendered graph} for the merge
            for sig in SIGNALS:
                vectors = vec_by_sig.get(sig) or {}
                if not vectors:
                    continue
                mem = _memory[sig]
                witness = _witness_for(sig, vectors)
                src_sig = SIGNAL_SOURCES.get(sig)
                write_vectors = (vec_by_sig.get(src_sig) or None) if src_sig else None
                # per-pod incident threshold from the learned steady-state baseline (None while
                # still maturing) -> an onset is an incident only if it deviates from normal
                baselines = {pod: mem.baseline_threshold(workload(pod)) for pod in vectors}
                out = run_pass(vectors, witness, slo_breach=breach or None,
                               window=ANALYSIS_WINDOW, write_vectors=write_vectors,
                               baselines=baselines, recent=RESET_WINDOW)
                rendered[sig] = mem.observe(out, vectors, witness=witness, ts=time.time())
            merged = (merge_graphs(rendered, primary=PRIMARY) if rendered else
                      {"findings": [], "edges": [], "root_cause_ranking": [],
                       "blast_radius": [], "meta": {"signals": SIGNALS}})
            # Ramp-to-limit early warnings (A1 forecaster), one per configured pair: the OOM card
            # (working_set -> memory limit, "leak") and the PS5 thermal card (coolant_temp -> trip
            # threshold, "trip"). Self-caused, so no causal edge -- they ride as `incipient`,
            # fired before the kill/trip ("we told you before the kernel/PLC did").
            incip = []
            for fsig, flim, fcls in FORECAST_PAIRS:
                sig_vec = vec_by_sig.get(fsig) or {}
                lim_vec = vec_by_sig.get(flim) or {}
                limits = {p: float(v[-1]) for p, v in lim_vec.items() if len(v) and v[-1] > 0}
                # a single-entity limit (the loop-wide trip threshold, keyed cool-1) broadcasts
                # to every entity carrying the ramp signal
                if len(limits) == 1 and sig_vec:
                    only = next(iter(limits.values()))
                    limits = {p: limits.get(p, only) for p in sig_vec}
                incip.extend(incipient_findings(sig_vec, limits, horizon_s=FORECAST_HORIZON_S,
                                                min_frac=FORECAST_MIN_FRAC, signal=fsig, cls=fcls))
            incip.sort(key=lambda f: f["eta_s"])
            merged["incipient"] = incip
            merged.setdefault("meta", {})["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with _lock:
                _graph = merged
        except Exception as e:  # never die; report the error on /graph
            with _lock:
                _graph = {"meta": {"status": "error", "error": str(e)}}
        time.sleep(INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, b"ok\n")
        if self.path.rstrip("/") in ("", "/graph"):
            with _lock:
                return self._send(200, json.dumps(_graph).encode(), "application/json")
        self._send(404, b"not found\n")

    def _send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    threading.Thread(target=loop, daemon=True).start()
    print(f"correlation engine up on :{PORT}; window={WINDOW_URL} signals={','.join(SIGNALS)}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
