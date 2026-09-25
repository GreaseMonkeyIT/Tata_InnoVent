"""L4 API gateway — a frontend-agnostic REST seam over the L2 aggregator + L3 engine.

It proxies and *normalizes* the causal graph, per-pod signals, and anomaly events into clean,
stable JSON with permissive CORS and an auto-generated OpenAPI spec at /docs — so any frontend
(React, Vue, a plain HTML page, a CLI) can consume the system without knowing the internal
service names or payload shapes. No causal logic lives here; the reasoning stays in L3. The one
transform it applies is collapsing live pod names (`tag-server-6644486769-6wlst`) to stable
workload names (`tag-server`) so a UI can key off something that survives restarts.
The integrity checks (integrity.py, SCENARIOS.md 5.2) compare what SCADA reports with the plant
physics. They are invariant checks, not causal inference.

Env: ENGINE_URL, AGGREGATOR_URL, PROM_URL, PLANT_URL, SCADA_URL, EWS_URL, ENGINE_SIGNAL,
ENGINE_SIGNALS, TOPOLOGY_NAMESPACES, OLLAMA_HOST, OLLAMA_MODEL, RECLAIM_FRAC, RESIZE_FRAC,
VISR_OPERATOR_TOKEN, AUDIT_PATH, FLEET_NS, FLEET_ENROLL_KEY, SCADA_WRITE_TOKEN, VPLC_IMAGE, TASKS_DIR,
DERATE_TARGET_PCT, RELIEF_CHECK_S, FLEET_BACKGROUND, INTEGRITY_BACKGROUND, RECONCILE_S.
"""
import concurrent.futures
import contextlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

import fleet
import integrity
import metrics
import security

ENGINE = os.environ.get("ENGINE_URL", "http://correlation-engine.aiops.svc:9100").rstrip("/")
AGG = os.environ.get("AGGREGATOR_URL", "http://aggregator.aiops.svc:9000").rstrip("/")
PROM = os.environ.get("PROM_URL", "http://prom-kube-prometheus-stack-prometheus.observability.svc.cluster.local.:9090").rstrip("/")  # Caretta topology source (eBPF L4 service map)
PLANT = os.environ.get("PLANT_URL", "http://plant-sim.plant.svc:9200").rstrip("/")  # plane-2 physics sim (pivot, LOG-029)
SCADA = os.environ.get("SCADA_URL", "http://tag-server.plant.svc:9300").rstrip("/")  # 2F.2 tag server (PLC-read tag DB + historian)
EWS = os.environ.get("EWS_URL", "http://rogue-ews.plant.svc.cluster.local:8090").rstrip("/")  # PS4A fault injector
# Caretta topology scope: both ends of an edge must sit in one of these namespaces (drops monitoring/infra flows).
TOPOLOGY_NS = {s.strip() for s in os.environ.get("TOPOLOGY_NAMESPACES", "plant,aiops").split(",") if s.strip()}
SIGNAL = os.environ.get("ENGINE_SIGNAL", "psi_io")             # primary/default resource class
SIGNALS = [s.strip() for s in os.environ.get("ENGINE_SIGNALS", "psi_io,psi_cpu,psi_mem").split(",") if s.strip()]
SIGNAL_RESOURCE = {"psi_io": "disk I/O", "psi_cpu": "CPU", "psi_mem": "memory",
                   "bus_voltage": "rail voltage", "coolant_temp": "coolant temperature",
                   "field_latency": "field network latency"}  # ground the narrator's resource word
PLANT_SIGNALS = ("bus_voltage", "coolant_temp", "field_latency")   # narrated as the plant floor, not pods
# The one LLM. Unset OLLAMA_HOST -> /api/narrative serves the deterministic template only, so the
# verdict never depends on the model being reachable (the demo must survive a model outage).
OLLAMA = os.environ.get("OLLAMA_HOST", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b-it-qat")  # default matches deploy/api.yaml (QAT 6.1GB)
# Right-sizing thresholds (PS-Q4): p95 usage vs requests/limits -> KAI scheduler verbs.
RECLAIM_FRAC = float(os.environ.get("RECLAIM_FRAC", "0.5"))   # p95 < this * request -> over-provisioned -> reclaim
RESIZE_FRAC = float(os.environ.get("RESIZE_FRAC", "0.85"))    # p95 > this * limit  -> at-risk -> resize up
HEADROOM = 1.3                                                # reclaim target = p95 * headroom
# 2E secure pass: state-changing endpoints require the operator token when it is set (unset =
# pre-2E open behavior, reported honestly by /api/health). nginx injects X-Auth-Token for the
# basic-auth `operator` user and X-Remote-User for attribution; a direct caller supplies the
# token itself. Every action AND denied attempt lands in the hash-chained audit ledger.
OPERATOR_TOKEN = os.environ.get("VISR_OPERATOR_TOKEN", "")
AUDIT = security.AuditLedger(os.environ.get("AUDIT_PATH", "/data/audit.jsonl"))
# 2H fleet + 3D act loop (FLEET.md section 10). The keys come from Secret visr-fleet. Without them the
# fleet mutations and the setpoint writes fail closed, and /api/fleet says so.
FLEET_NS = os.environ.get("FLEET_NS", "fleet")
FLEET_ENROLL_KEY = os.environ.get("FLEET_ENROLL_KEY", "")
SCADA_WRITE_TOKEN = os.environ.get("SCADA_WRITE_TOKEN", "")
VPLC_IMAGE = os.environ.get("VPLC_IMAGE", "localhost:5000/skn/vplc:v0.1")
TASKS_DIR = os.environ.get("TASKS_DIR", "/tasks")
DERATE_TARGET_PCT = int(os.environ.get("DERATE_TARGET_PCT", "55"))
RELIEF_CHECK_S = float(os.environ.get("RELIEF_CHECK_S", "60"))
K8S = fleet.K8s(FLEET_NS)
# Integrity checks (SCENARIOS.md 5.2): one background pass every RECONCILE_S seconds.
RECONCILE_S = float(os.environ.get("RECONCILE_S", "5"))
LEDGER_SCAN = 2000                  # ledger rows that the checks read (the newest rows)


@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Start the integrity checks at startup, so a headless soak gets them with no console open."""
    _ensure_integrity()
    yield


app = FastAPI(
    title="VISR API",
    version="1.0",
    description="Frontend-agnostic REST over the causal correlation engine (L3) and the "
                "telemetry aggregator (L2). Read endpoints under /api; OpenAPI at /openapi.json.",
    lifespan=_lifespan,
)
# Permissive CORS so a separately-served frontend (any origin/port) can call this directly.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _post(url, timeout=8):
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def _call(method, url, body=None, headers=None, timeout=5):
    """JSON request -> (status, parsed body). HTTP errors return their status instead of raising."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    try:
        return status, json.loads(raw) if raw.strip() else {}
    except ValueError:
        return status, {"detail": raw[:300]}


def _probe(url, timeout=3) -> bool:
    """True when the URL answers 2xx within the timeout."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def _fanout(fns) -> list:
    """Run zero-argument calls in parallel threads. A call that raises gives None."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(fns))) as pool:
        futs = [pool.submit(f) for f in fns]
    out = []
    for f in futs:
        try:
            out.append(f.result())
        except Exception:
            out.append(None)
    return out


def _clip(s, n=64) -> str:
    """Client input that becomes a ledger target has a bounded length."""
    return str(s)[:n]


def _parse_caretta(result, namespaces=TOPOLOGY_NS):
    """Collapse Caretta's `caretta_links_observed` series (Caretta emits one per role/kind) into a
    single directed workload edge per (client -> server), scoped to TOPOLOGY_NAMESPACES (drops
    monitoring/infra flows). client_name/server_name are already workload names -- no pod-hash
    stripping needed. Keeps the largest observed byte count per pair."""
    best = {}
    for s in result:
        m = s.get("metric", {})
        cn, sn = m.get("client_name"), m.get("server_name")
        cns, sns = m.get("client_namespace", ""), m.get("server_namespace", "")
        if not cn or not sn or cn == sn:
            continue
        if not (cns in namespaces and sns in namespaces):
            continue
        try:
            b = float(s.get("value", [0, 0])[1])
        except Exception:
            b = 0.0
        k = (cn, sn)
        if k not in best or b > best[k]["bytes"]:
            best[k] = {"src": cn, "dst": sn, "src_ns": cns, "dst_ns": sns,
                       "port": m.get("server_port"), "bytes": round(b)}
    return sorted(best.values(), key=lambda e: -e["bytes"])


def _caretta_topology():
    try:
        r = _get(PROM + "/api/v1/query?query=caretta_links_observed")
        result = (r.get("data") or {}).get("result") or []
    except Exception:
        return {"edges": [], "source": "unavailable"}
    return {"edges": _parse_caretta(result), "source": "caretta"}


def _prom_map(q):
    """Instant PromQL -> {(namespace, workload): summed float}. Empty on any failure (graceful)."""
    out = {}
    try:
        r = _get(PROM + "/api/v1/query?query=" + urllib.parse.quote(q))
    except Exception:
        return out
    for s in (r.get("data") or {}).get("result") or []:
        m = s.get("metric", {})
        ns, pod = m.get("namespace"), m.get("pod")
        if not ns or not pod:
            continue
        try:
            v = float(s.get("value", [0, 0])[1])
        except Exception:
            continue
        k = (ns, workload(pod))
        out[k] = out.get(k, 0.0) + v
    return out


def _prom_pod_map(q):
    """Instant PromQL -> {(namespace, pod): summed float}, keyed by the *full* pod name (unlike
    _prom_map which collapses to workload). Used by /api/pod-resources so each replica is its own
    row. Empty on any failure (graceful)."""
    out = {}
    try:
        r = _get(PROM + "/api/v1/query?query=" + urllib.parse.quote(q))
    except Exception:
        return out
    for s in (r.get("data") or {}).get("result") or []:
        m = s.get("metric", {})
        ns, pod = m.get("namespace"), m.get("pod")
        if not ns or not pod:
            continue
        try:
            v = float(s.get("value", [0, 0])[1])
        except Exception:
            continue
        k = (ns, pod)
        out[k] = out.get(k, 0.0) + v
    return out


def _fmt_cpu(c):
    return f"{round(c * 1000)}m" if c < 1 else f"{round(c, 2)} CPU"


def _fmt_mem(b):
    u = ["B", "KB", "MB", "GB"]; i = 0; v = float(b)
    while v >= 1024 and i < len(u) - 1:
        v /= 1024; i += 1
    return f"{round(v)} {u[i]}"


def _rightsize(wl, req, lim, p95, resource):
    """One workload+resource -> a KAI-verb right-sizing card, or None if already right-sized."""
    fmt = _fmt_cpu if resource == "cpu" else _fmt_mem
    min_save = 0.05 if resource == "cpu" else 64 * 1024 * 1024
    if req and p95 is not None and p95 < RECLAIM_FRAC * req:
        target = max(p95 * HEADROOM, req * 0.1)
        save = req - target
        if save > min_save:
            return {"verb": "reclaim", "workload": wl, "resource": resource,
                    "detail": f"requests {fmt(req)}, p95 {fmt(p95)} -> reclaim {fmt(save)}",
                    "request": req, "limit": lim, "p95": p95, "target": round(target, 3)}
    if lim and p95 is not None and p95 > RESIZE_FRAC * lim:
        return {"verb": "resize", "workload": wl, "resource": resource,
                "detail": f"p95 {fmt(p95)} near limit {fmt(lim)} -> resize up (throttle/OOM risk)",
                "request": req, "limit": lim, "p95": p95, "target": round(lim * 1.5, 3)}
    return None


def _gini(xs):
    """Gini coefficient over per-pod stall (0 = perfectly fair; ->1 = a few suffer disproportionately)."""
    xs = sorted(v for v in xs if v >= 0)
    n = len(xs)
    s = sum(xs)
    if n == 0 or s == 0:
        return 0.0
    cum = sum((i + 1) * v for i, v in enumerate(xs))
    return round((2 * cum) / (n * s) - (n + 1) / n, 3)


def _fairness(stall_by_key):
    by_ns = {}
    for (ns, _wl), v in stall_by_key.items():
        by_ns.setdefault(ns, []).append(v)
    return [{"namespace": ns, "gini": _gini(vs), "workloads": len(vs)} for ns, vs in sorted(by_ns.items())]


def _ollama(prompt, timeout=30):
    """One non-streamed completion from Ollama; None on any failure so the caller falls back.
    `think: false` disables gemma's reasoning phase (we want one fast, deterministic sentence)."""
    if not OLLAMA:
        return None
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                       "think": False, "keep_alive": "10m",  # stay warm through an incident
                       "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.load(r).get("response") or "").strip() or None
    except Exception:
        return None


def _template_narrative(g) -> str:
    """Deterministic verdict sentence built from the graph — the always-available fallback."""
    root = g.get("root") or []
    edges = g.get("edges") or []
    meta = g.get("meta") or {}
    signal = meta.get("signal", SIGNAL)
    if not root or not edges:
        return f"Steady state: no causal contention detected across {meta.get('pods', 0)} workloads."
    cause = root[0]["pod"]
    out = [e for e in edges if e["src"] == cause] or edges          # strongest edge leaving the root
    e = max(out, key=lambda x: abs(x.get("r") or 0.0))
    victim = e["dst"]
    ev = ", ".join(e.get("evidence") or []) or "correlation"
    eta = {b["pod"]: b.get("eta_s") for b in (g.get("blast_radius") or [])}.get(victim)
    eta_txt = f", with impact on {victim} expected in ~{int(eta)}s" if eta else ""
    reg = meta.get("case_register")
    reg_txt = f" (recognised as a {reg} of a known case)" if reg in ("recurrence", "variant") else ""
    return (f"{cause} is the likely root cause of {SIGNAL_RESOURCE.get(signal, signal)} contention: "
            f"its activity correlates with {victim} over {ev}{eta_txt}{reg_txt}.")


def _incipient_text(incip) -> str:
    """Deterministic early-warning line, phrased per forecast family (2C'): the plant coolant thermal
    TRIP (coolant_temp -> temp_limit, °C) or the memory OOM leak (mem -> mem_limit, bytes). The
    finding's class/signal pick the wording — a coolant trip must NOT read as a memory OOM in bytes.
    Pods already workload-normalized by graph()."""
    f = min(incip, key=lambda x: x.get("eta_s") if x.get("eta_s") is not None else 1e9)
    pod, eta = f["pod"], int(f.get("eta_s") or 0)
    val, lim = f.get("value") or 0, f.get("limit") or 0
    if f.get("class") == "trip" or f.get("signal") == "coolant_temp":
        return (f"Early warning: {pod} coolant temperature is climbing toward the {lim:.0f} °C trip "
                f"({val:.0f} °C now) — projected trip in ~{eta}s.")
    return (f"Early warning: {pod} is trending toward its memory limit "
            f"({_fmt_mem(val)} of {_fmt_mem(lim)}) — projected OOM in ~{eta}s.")


_PLANT_ENTITIES: set = set()
_PLANT_ENTITIES_TS: float = 0.0


def _plant_entities() -> set:
    """Plant entities are NOT k8s pods — the hash-stripping heuristic mangles the one
    multi-segment plant name (qa-scanner-1 -> "qa"), the same LOG-033 trap the engine already
    guards in its own workload(). The set comes from the sim's /state: devices, rails, the loop,
    and the network segments with their members. It is TTL-cached so /api/graph never blocks on
    the sim. A fetch failure keeps the last known set."""
    global _PLANT_ENTITIES, _PLANT_ENTITIES_TS
    if time.time() - _PLANT_ENTITIES_TS > 30:
        _PLANT_ENTITIES_TS = time.time()          # even on failure: don't hammer a down sim
        try:
            s = _get(PLANT + "/state")
            segs = s.get("segments") or {}
            _PLANT_ENTITIES = (set(s.get("devices", {})) | set(s.get("rails", {}))
                               | ({s["loop"]["name"]} if s.get("loop", {}).get("name") else set())
                               | set(segs)
                               | {m for seg in segs.values() for m in ((seg or {}).get("members") or {})})
        except Exception:
            pass
    return _PLANT_ENTITIES


def workload(pod: str) -> str:
    """tag-server-6644486769-6wlst -> tag-server (drop replicaset + pod hash).
    Known plant entities keep their names verbatim — they carry no k8s hashes to strip."""
    if pod in _plant_entities():
        return pod
    parts = pod.split("-")
    return "-".join(parts[:-2]) if len(parts) > 2 else pod


# PS-series = the plant fault scenarios (SCENARIOS.md 1). Faults perturb the MODEL, and the
# symptoms emerge. `owner` is the service that injects and clears the fault (the dispatch table
# _OWNERS below). `expect_s` is the time to the expected verdict, for the console message. The
# engine holds a root until a signal stays out of band for most of 2 min (GATE_Q=35), so a plant
# root takes about 80 s. The values follow the box proof run of 2026-09-19 (LOG-070).
SCENARIOS = [
    {"id": "PS0", "name": "Steady plant", "mechanism": "no faults. Baselines mature and the engine stays silent.",
     "anchor": None, "plane": "all", "owner": None, "triggerable": False,
     "expect": "no root, no findings, no integrity finding", "expect_s": 0},
    {"id": "PS1", "name": "Rail-sag cascade",
     "mechanism": "press-1 bearing friction -> amps up -> rail A sags -> mates degrade",
     "anchor": "Milford Haven refinery, 1994: 275 alarms in the last 11 minutes",
     "plane": "plant", "owner": "plant", "triggerable": True,
     "expect": "root press-1 along rail psu-a", "expect_s": 90},
    {"id": "PS2", "name": "Power sag trips the chiller",
     "mechanism": "compressor-1 stuck on -> rail B sags -> chiller-1 overload relay trips -> loop cool-1 flow falls",
     "anchor": "Azure Australia East, 2023: a power sag tripped the chillers",
     "plane": "plant", "owner": "plant", "triggerable": True,
     "expect": "root compressor-1 via rail psu-b, chiller-1 and loop cool-1, with trip forecasts",
     "expect_s": 150},
    {"id": "PS3", "name": "Control network storm",
     "mechanism": "hmi-gw floods segment field-1 (20 -> 1500 frames/s) -> the stamping cell link lags and drops",
     "anchor": "Browns Ferry Unit 3, 2006: network traffic stopped both recirculation pump drives",
     "plane": "network", "owner": "plant", "triggerable": True,
     "expect": "root hmi-gw along segment field-1", "expect_s": 90},
    {"id": "PS4A", "name": "Setpoint write with no record",
     "mechanism": "a rogue workstation writes press-1 DERATE_PCT = 30 over S7comm, with no SCADA path and no ledger row",
     "anchor": "Stuxnet 2010, FrostyGoop 2024, Ukraine grid 2015",
     "plane": "integrity", "owner": "ews", "triggerable": True,
     "expect": "integrity finding unsigned_write on FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "expect_s": 45},
    {"id": "PS4B", "name": "Current report contradicts the feeder",
     "mechanism": "press-1 friction 1.4 -> real amps up, while its PLC AMPS channel replays the last 30 s",
     "anchor": "Stuxnet replayed normal values. Buncefield 2005: a stuck gauge.",
     "plane": "integrity", "owner": "plant", "triggerable": True,
     "expect": "integrity finding current_balance on rail psu-a, channel FLEET.PLC_STAMPING.PRESS_1.AMPS",
     "expect_s": 40},
    {"id": "PS5", "name": "Coolant pump degradation",
     "mechanism": "flow drops -> temps ramp toward the 78C trip (forecast beat)",
     "anchor": "LG Polymers, Visakhapatnam, 2020: the tank heated with no sensor at the top",
     "plane": "plant", "owner": "plant", "triggerable": True,
     "expect": "trip forecast cards before the 78 C trip", "expect_s": 60},
    {"id": "PS6", "name": "The monitor runs out of memory",
     "mechanism": "the tag server leaks 0.5 MiB/s toward its 128 MiB limit until the kernel kills it",
     "anchor": "Toyota, 2023: a full disk stopped 12 plants. Northeast blackout, 2003: "
               "the alarm system stopped with no warning.",
     "plane": "edge", "owner": "scada", "triggerable": True,
     "expect": "forecast card class leak on tag-server. After the kill, the console shows that the SCADA view is blind.",
     "expect_s": 120},
]
_SCN = {s["id"]: s for s in SCENARIOS}
PS6_LEAK_MIB_PER_S = 0.5
# The setpoint that rogue-ews writes (EWS_DB 1, byte 292 = %MW10) and its task default.
EWS_SETPOINT = {"plc": "plc-stamping", "asset": "press-1", "signal": "DERATE_PCT", "default": 100}


@app.get("/api/health", tags=["meta"])
def health():
    """Reachability of the upstream services. `ok` means the aggregator and the engine answer. The
    tag server (scada) and plant-sim (plant) are reported too, and they do not change `ok`."""
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "services": {}}
    probes = (("aggregator", AGG), ("engine", ENGINE), ("scada", SCADA), ("plant", PLANT))
    up = _fanout([lambda u=url: _probe(u + "/healthz") for _, url in probes])
    for (name, _), ok in zip(probes, up):
        out["services"][name] = "up" if ok else "down"
    out["ok"] = out["services"]["aggregator"] == "up" and out["services"]["engine"] == "up"
    # 2E honesty: say whether the action gate is live, so an open deployment can't pass as secured
    out["auth"] = "enforced" if OPERATOR_TOKEN else "disabled"
    return out


@app.get("/api/graph", tags=["causal"])
def graph():
    """The current causal verdict from L3: root cause, edges (with evidence), blast radius, and
    findings, with pod names normalized to stable workload names for the UI. `integrity` holds the
    open findings of the API's own integrity checks. They do not replace a root."""
    try:
        g = _get(ENGINE + "/graph")
    except Exception as e:
        raise HTTPException(503, f"engine unreachable: {e}")
    w = workload
    return {
        "root": [{"pod": w(r["pod"]), "score": r.get("score"), "onset_s": r.get("onset_s")}
                 for r in g.get("root_cause_ranking", [])],
        "edges": [{"src": w(e["src"]), "dst": w(e["dst"]), "r": e["r"], "lag_s": e["lag_s"],
                   "evidence": e["evidence"], "signal": e.get("signal"),
                   "confidence": e.get("confidence"), "state": e.get("state"),
                   "render_weight": e.get("render_weight"), "source": e.get("source")}
                  for e in g.get("edges", [])],
        "blast_radius": [{"pod": w(b["pod"]), "impact": b["impact"], "eta_s": b["eta_s"]}
                         for b in g.get("blast_radius", [])],
        "findings": [{"pod": w(f["pod"]), "class": f.get("class"), "onset_s": f.get("onset_s"),
                      "severity": f.get("severity")} for f in g.get("findings", [])],
        "incipient": [{"pod": w(f["pod"]), "class": f.get("class"), "signal": f.get("signal"),
                       "eta_s": f.get("eta_s"), "value": f.get("value"), "limit": f.get("limit"),
                       "headroom_frac": f.get("headroom_frac")} for f in g.get("incipient", [])],
        "integrity": _integrity_findings(),
        "meta": g.get("meta", {}),
    }


def _controller_note(root_pod) -> str:
    """3E narrator line (FLEET.md 10): name the PLC that controls the root machine, and any operator
    derate in force on it. Deterministic and model-free, so the model cannot invent an action."""
    if not root_pod:
        return ""
    try:
        scada = _scada_fleet()
    except Exception:
        return ""
    entry = fleet.controller_of(root_pod, scada)
    if entry is None:
        return ""
    label = (fleet.PROFILE_BY_ID.get(entry.get("profile")) or {}).get("label", entry.get("profile"))
    note = f" {root_pod} is controlled by {entry['name']} ({label})."
    active = next((a for a in fleet.active_derates(scada) if a["asset"] == root_pod), None)
    if active:
        note += f" An operator derate holds {root_pod} at {active['value']:.0f} % through {active['plc']}."
    return note


# Cache the LLM verdict keyed by the graph's shape, so the dashboard's 5s poll doesn't re-run the
# model every tick — we only regenerate when the verdict actually changes.
_NARR_CACHE: dict = {}


def _verdict_signature(g) -> str:
    root = g.get("root") or []
    edges = g.get("edges") or []
    return json.dumps(
        {"root": root[0]["pod"] if root else None,
         "edges": sorted((e["src"], e["dst"], e.get("state")) for e in edges),
         "case": (g.get("meta") or {}).get("case_register")},
        sort_keys=True,
    )


@app.get("/api/narrative", tags=["causal"])
def narrative():
    """One-sentence operator verdict. A local LLM (Ollama) renders the causal graph into prose
    that cites the evidence the engine already found; it falls back to a deterministic template
    when the model is unset/unreachable/slow — so the verdict never depends on the model."""
    g = graph()  # normalized verdict; raises 503 if the engine is unreachable
    if not g.get("root"):
        # No causal root. A memory leak is self-caused (no edge), so surface the OOM forecast here:
        # deterministic and model-free (the "before the kernel did" beat must never depend on Ollama).
        incip = g.get("incipient") or []
        if incip:
            return {"text": _incipient_text(incip), "source": "forecast", "model": None,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        # Otherwise steady. Do NOT feed the steady-state backbone edges to the LLM: with no root it
        # narrates the normal coupling as "contention" (the steady graph still carries faint backbone
        # edges). The deterministic steady line is the right answer and costs no model call.
        return {"text": _template_narrative(g), "source": "steady", "model": None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    sig = _verdict_signature(g)
    # Weave the OOM forecast into the incident verdict too (it otherwise shows only on the no-root
    # path). Deterministic, model-free; computed fresh each call so the ETA stays current under cache.
    fc = (" " + _incipient_text(g["incipient"])) if g.get("incipient") else ""
    fc = _controller_note(root_pod=(g.get("root") or [{}])[0].get("pod")) + fc
    if sig in _NARR_CACHE:
        c = _NARR_CACHE[sig]
        return {**c, "text": c["text"] + fc}
    template = _template_narrative(g)
    # Ground the resource word in the signal of the ROOT's own edge (multi-signal: the graph may
    # carry edges on more than one resource class), falling back to the active-signal meta label.
    root_pod = (g.get("root") or [{}])[0].get("pod")
    active_sig = next((e.get("signal") for e in (g.get("edges") or [])
                       if e.get("src") == root_pod and e.get("signal")), None) \
        or (g.get("meta") or {}).get("signal", SIGNAL)
    resource = SIGNAL_RESOURCE.get(active_sig, "resource")
    # Plane-aware framing: the plant floor (rail voltage / coolant temp / field latency) speaks in machines/assets;
    # the edge node (psi_*) in pods. Keeps the narrator on the NEW physics-plant vocabulary instead
    # of defaulting to "Kubernetes pods / memory / OOM".
    plant = active_sig in PLANT_SIGNALS
    domain = "an industrial plant floor" if plant else "a Kubernetes edge node"
    entity = "machine" if plant else "pod"
    prompt = (
        f"You are a controls and SRE assistant for {domain}. Given this causal verdict JSON from an "
        f"edge causal-AIOps engine, write ONE or TWO plain sentences for an on-call operator. The "
        f"contended resource is {resource}; call it {resource} contention and do NOT name any other "
        f"resource type (not memory, not CPU, not I/O). The root-cause {entity} is the SOURCE; the "
        f"blast-radius {entity}s are the affected VICTIMS. Cite only the evidence types and ETAs "
        f"present in the JSON; do not invent metrics, numbers, or causes. If there is no root cause, "
        f"say the system is steady.\n\n"
        "VERDICT:\n" + json.dumps({k: g.get(k) for k in ("root", "edges", "blast_radius", "meta")})
    )
    text = _ollama(prompt)
    out = {
        "text": text or template,
        "source": "llm" if text else "fallback",
        "model": OLLAMA_MODEL if text else None,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if text:  # cache only successful LLM renders (the base text); while it falls back, keep retrying
        _NARR_CACHE.clear()
        _NARR_CACHE[sig] = out
    return {**out, "text": out["text"] + fc}


@app.get("/api/pods", tags=["telemetry"])
def pods():
    """Live per-workload snapshot: most-recent level of the engine signal + whether the engine
    currently considers it anomalous. Sorted hottest-first, for a heatmap or a status list."""
    try:
        window = _get(AGG + "/window")
    except Exception as e:
        raise HTTPException(503, f"aggregator unreachable: {e}")
    try:
        flagged = {workload(f["pod"]) for f in _get(ENGINE + "/graph").get("findings", [])}
    except Exception:
        flagged = set()
    out = {}
    for key, samples in window.items():
        parts = key.split("/")
        if len(parts) < 3 or parts[-1] not in SIGNALS or not samples:
            continue
        w = workload(parts[1])
        val = float(samples[-1]["value"])
        cur = out.setdefault(w, {"workload": w, "namespace": parts[0], "signal": parts[-1], "value": 0.0, "anomalous": False})
        if val >= cur["value"]:                       # report each workload's hottest signal class
            cur["value"], cur["signal"] = round(val, 4), parts[-1]
        cur["anomalous"] = w in flagged
    return sorted(out.values(), key=lambda p: -p["value"])


@app.get("/api/signal/{pod}", tags=["telemetry"])
def signal(pod: str, signal: str = SIGNAL):
    """Raw (ts, value) time series for one workload's signal — for charting a single pod."""
    try:
        window = _get(AGG + "/window")
    except Exception as e:
        raise HTTPException(503, f"aggregator unreachable: {e}")
    for key, samples in window.items():
        parts = key.split("/")
        if len(parts) >= 3 and parts[-1] == signal and samples and workload(parts[1]) == pod:
            return {"pod": pod, "signal": signal,
                    "points": [{"ts": s["ts"], "value": s["value"]} for s in samples]}
    raise HTTPException(404, f"no '{signal}' series for workload '{pod}'")


@app.get("/api/events", tags=["telemetry"])
def events():
    """Recent anomaly_candidate events from L2 (the coarse threshold alert stream)."""
    try:
        return _get(AGG + "/events")
    except Exception as e:
        raise HTTPException(503, f"aggregator unreachable: {e}")


@app.get("/api/topology", tags=["topology"])
def topology():
    """Auto-discovered L4 service map from Caretta (eBPF) — who-talks-to-whom across the plant and engine pods,
    with zero application instrumentation. Directed edges with the server port + observed bytes.
    `source: unavailable` until Caretta is up and scraped."""
    return _caretta_topology()


@app.get("/api/recommendations", tags=["recommendations"])
def recommendations():
    """PS-Q4 — which workloads to optimize. Deterministic right-sizing (p95 usage vs requests/limits)
    in KAI scheduler verbs (reclaim / resize), plus a per-namespace fairness index (Gini over PSI
    stall). Pure analysis of metrics already scraped; `source: unavailable` if Prometheus is down."""
    q = {
        "cpu_req": 'sum by(namespace,pod)(kube_pod_container_resource_requests{namespace=~"aiops|observability|plant",resource="cpu"})',
        "cpu_lim": 'sum by(namespace,pod)(kube_pod_container_resource_limits{namespace=~"aiops|observability|plant",resource="cpu"})',
        "cpu_p95": 'quantile_over_time(0.95, sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{namespace=~"aiops|observability|plant",container!=""}[5m]))[1h:5m])',
        "mem_req": 'sum by(namespace,pod)(kube_pod_container_resource_requests{namespace=~"aiops|observability|plant",resource="memory"})',
        "mem_lim": 'sum by(namespace,pod)(kube_pod_container_resource_limits{namespace=~"aiops|observability|plant",resource="memory"})',
        "mem_p95": 'quantile_over_time(0.95, sum by(namespace,pod)(container_memory_working_set_bytes{namespace=~"aiops|observability|plant",container!=""})[1h:5m])',
    }
    maps = {k: _prom_map(v) for k, v in q.items()}
    if not any(maps.values()):
        return {"right_sizing": [], "fairness": [], "source": "unavailable"}
    keys = set().union(*[set(m) for m in maps.values()])
    cards = []
    for (ns, wl) in sorted(keys):
        for res, rq, lm, p9 in (("cpu", "cpu_req", "cpu_lim", "cpu_p95"),
                                ("memory", "mem_req", "mem_lim", "mem_p95")):
            c = _rightsize(wl, maps[rq].get((ns, wl)), maps[lm].get((ns, wl)), maps[p9].get((ns, wl)), res)
            if c:
                c["namespace"] = ns
                cards.append(c)
    cards.sort(key=lambda c: (c["verb"] != "resize", -(c.get("p95") or 0)))  # at-risk (resize) first
    # total PSI stall per pod = io + cpu + mem (3 explicit queries summed; the proven aggregator form
    # -- a single {__name__=~...} regex query came back empty on the live Prometheus).
    stall = {}
    for psi in ("io", "cpu", "memory"):
        q_psi = f'sum by(namespace,pod)(rate(container_pressure_{psi}_stalled_seconds_total{{namespace=~"aiops|observability|plant"}}[5m]))'
        for k, v in _prom_map(q_psi).items():
            stall[k] = stall.get(k, 0.0) + v
    return {"right_sizing": cards, "fairness": _fairness(stall), "source": "prometheus"}


@app.get("/api/pod-resources", tags=["telemetry"])
def pod_resources(namespace: str = "aiops|observability|plant"):
    """Per-pod **allocated vs live** snapshot: CPU/memory requests + limits next to current usage
    (CPU cores from a 1m rate; memory working-set bytes), straight from Prometheus. Raw numbers —
    the frontend formats and charts them on a moving window. `source: unavailable` if Prometheus
    is down. Reuses the same metrics as /api/recommendations; nothing is written."""
    sel = f'namespace=~"{namespace}"'
    q = {
        "cpu_req": f'sum by(namespace,pod)(kube_pod_container_resource_requests{{{sel},resource="cpu"}})',
        "cpu_lim": f'sum by(namespace,pod)(kube_pod_container_resource_limits{{{sel},resource="cpu"}})',
        "cpu_use": f'sum by(namespace,pod)(rate(container_cpu_usage_seconds_total{{{sel},container!=""}}[1m]))',
        "mem_req": f'sum by(namespace,pod)(kube_pod_container_resource_requests{{{sel},resource="memory"}})',
        "mem_lim": f'sum by(namespace,pod)(kube_pod_container_resource_limits{{{sel},resource="memory"}})',
        "mem_use": f'sum by(namespace,pod)(container_memory_working_set_bytes{{{sel},container!=""}})',
    }
    maps = {k: _prom_pod_map(v) for k, v in q.items()}
    if not any(maps.values()):
        return {"pods": [], "source": "unavailable"}
    keys = set().union(*[set(m) for m in maps.values()])
    pods = []
    for (ns, pod) in sorted(keys):
        pods.append({
            "namespace": ns, "pod": pod, "workload": workload(pod),
            "cpu": {"request": maps["cpu_req"].get((ns, pod)), "limit": maps["cpu_lim"].get((ns, pod)),
                    "usage": round(maps["cpu_use"].get((ns, pod), 0.0), 4)},
            "mem": {"request": maps["mem_req"].get((ns, pod)), "limit": maps["mem_lim"].get((ns, pod)),
                    "usage": round(maps["mem_use"].get((ns, pod), 0.0))},
        })
    return {"pods": pods, "source": "prometheus"}


@app.get("/api/plant", tags=["telemetry"])
def plant_state():
    """Live plant-floor snapshot (plane 2): rails, coolant loop, machines with A/°C/throughput,
    active PS-series faults — proxied from the physics sim's /state. Honestly labeled: the
    substrate is SIMULATED plant physics; the inference downstream is real. `source:
    unavailable` if the sim is down."""
    try:
        s = _get(PLANT + "/state")
    except Exception:
        return {"source": "unavailable"}
    s["source"] = "sim"
    return s


@app.get("/api/tags", tags=["telemetry"])
def scada_tags():
    """The SCADA tag browser (2F.2): every plant tag with ISA-style name, PLC address, unit,
    live value, GOOD/STALE/BAD quality, plus PLC/historian health and the historian ingest rate.
    Values here traveled physics -> OpenPLC registers -> Modbus -> tag server — the industrial
    data path, not a shortcut through the sim."""
    try:
        return _get(SCADA + "/tags", timeout=4)
    except Exception:
        return {"source": "unavailable", "tags": []}


def _owner_active() -> dict:
    """{owner: set of active scenario ids, or None when the owner does not answer}. Short timeouts,
    run in parallel, so one dead owner cannot stall the catalogue."""
    def owned(owner):
        return {s["id"] for s in SCENARIOS if s["owner"] == owner}

    def plant():
        return {str(f).upper() for f in _get(PLANT + "/state", timeout=2).get("active_faults") or []}

    def ews():
        return owned("ews") if _get(EWS + "/state", timeout=2).get("active") else set()

    def scada():
        return owned("scada") if _get(SCADA + "/chaos", timeout=2).get("active") else set()

    return dict(zip(("plant", "ews", "scada"), _fanout([plant, ews, scada])))


@app.get("/api/scenarios", tags=["scenarios"])
def scenarios():
    """Catalogue of fault scenarios (SCENARIOS.md 5.1). `active` comes from the owner of each fault:
    plant-sim /state, rogue-ews /state, or tag-server /chaos. It is null when that owner does not
    answer. PS0 is active when every owner answers and none has a fault."""
    act = _owner_active()
    out = []
    for s in SCENARIOS:
        item = dict(s)
        if s["owner"] is None:
            item["active"] = None if None in act.values() else not any(act.values())
        else:
            ids = act.get(s["owner"])
            item["active"] = None if ids is None else s["id"] in ids
        out.append(item)
    return out


def _actor(request: Request) -> str:
    """Attribution: nginx sets X-Remote-User to the basic-auth username; else anonymous."""
    return request.headers.get("x-remote-user", "") or "anonymous"


def _require_operator(request: Request, verb: str, target: str) -> str:
    """The 2E action gate: state changes need the operator token (when configured). A denied
    attempt is itself an audit event — the ledger records who knocked, not just who entered."""
    token = request.headers.get("x-auth-token", "")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    actor = _actor(request)
    if not security.token_ok(token, OPERATOR_TOKEN):
        AUDIT.append(actor, verb, _clip(target), "denied")
        raise HTTPException(401, "operator token required for state-changing actions")
    return actor


def _evidence_snapshot() -> dict:
    """Best-effort verdict context at action time (what the operator saw when they acted)."""
    try:
        g = _get(ENGINE + "/graph", timeout=3)
        root = (g.get("root_cause_ranking") or [{}])[0]
        edge = next((e for e in g.get("edges", []) if e.get("src") == root.get("pod")), {})
        return {"root": root.get("pod"), "score": root.get("score"),
                "evidence": edge.get("evidence", [])}
    except Exception:
        return {}


@app.get("/api/audit", tags=["security"])
def audit(limit: int = 100):
    """The tamper-evident action ledger: who fired/reset what, when, citing which verdict.
    chain_ok re-derives every hash on read — a false value means the file was edited."""
    ok, n = AUDIT.verify()
    return {"entries": AUDIT.entries(limit), "chain_ok": ok, "count": n,
            "auth": "enforced" if OPERATOR_TOKEN else "disabled"}


# ---------------------------------------------------- fault owner dispatch --
class _OwnerRefused(Exception):
    """A fault owner answered with an HTTP error. `code` goes into the ledger row, `answer` is the
    status this API returns. `off` means the API has no SCADA_WRITE_TOKEN for that owner."""

    def __init__(self, code, detail, answer=502, off=False):
        super().__init__(detail)
        self.code, self.detail, self.answer, self.off = code, detail, answer, off


def _plant_post(path):
    """POST to plant-sim (PS1, PS2, PS3, PS4B, PS5). The URL shapes stay /fault/<ID> and /reset."""
    try:
        _post(PLANT + path)
    except urllib.error.HTTPError as e:
        raise _OwnerRefused(e.code, f"plant-sim: {e.read().decode()[:200]}", answer=e.code)


def _token_post(url, who, body=None):
    """POST to a token-gated fault owner (rogue-ews, the tag-server chaos endpoints). A network
    error raises as it is."""
    if not SCADA_WRITE_TOKEN:
        raise _OwnerRefused(503, f"SCADA_WRITE_TOKEN is not set (Secret visr-fleet): {who} faults are off",
                            answer=503, off=True)
    status, out = _call("POST", url, body, {"X-Scada-Token": SCADA_WRITE_TOKEN}, timeout=5)
    if status != 200:
        raise _OwnerRefused(status, f"{who} answered {status}: {out.get('error') or out.get('detail') or out}")
    return out


def _restore_setpoint(actor):
    """PS4A reset: write the task default back through the SCADA write path, with a signed restore
    row. The write is skipped when SCADA already reads the default."""
    sp = EWS_SETPOINT
    tag = fleet.tag_name(sp["plc"], sp["asset"], sp["signal"])
    try:
        entry = next((e for e in _get(SCADA + "/fleet", timeout=3) if e.get("name") == sp["plc"]), None)
        current = next((t.get("value") for t in (entry or {}).get("tags") or [] if t.get("tag") == tag), None)
    except Exception:
        current = None                  # unknown: write anyway, and a dead tag server fails the write
    if current == sp["default"]:
        return
    _scada_write(sp["plc"], tag, sp["default"])
    AUDIT.append(actor, "restore", sp["asset"], "restored", {"plc": sp["plc"], "tag": tag, "from": current,
                                                              "to": sp["default"], "reason": "scenario reset"})


def _ews_reset(actor):
    if not SCADA_WRITE_TOKEN:
        raise _OwnerRefused(503, "SCADA_WRITE_TOKEN is not set (Secret visr-fleet): rogue-ews faults are off",
                            answer=503, off=True)
    _restore_setpoint(actor)
    _token_post(EWS + "/reset", "rogue-ews")


# owner -> trigger(sid, actor) and reset(sid, actor). A new fault needs a SCENARIOS row, not new code.
_OWNERS = {
    "plant": {"trigger": lambda sid, actor: _plant_post("/fault/" + sid),
              "reset": lambda sid, actor: _plant_post("/reset")},        # clears every plant-sim fault
    "ews": {"trigger": lambda sid, actor: _token_post(EWS + "/fault/" + sid, "rogue-ews"),
            "reset": lambda sid, actor: _ews_reset(actor)},
    "scada": {"trigger": lambda sid, actor: _token_post(SCADA + "/chaos/leak", "tag-server",
                                                        {"mib_per_s": PS6_LEAK_MIB_PER_S}),
              "reset": lambda sid, actor: _token_post(SCADA + "/chaos/reset", "tag-server")},
}


def _dispatch(actor, verb, target, fn):
    """Run one owner call. A failure writes an error row: `error <status>` for an HTTP answer, or
    `error` and 503 for a network error."""
    try:
        return fn()
    except _OwnerRefused as e:
        AUDIT.append(actor, verb, target, f"error {e.code}")
        raise HTTPException(e.answer, e.detail)
    except HTTPException as e:
        AUDIT.append(actor, verb, target, f"error {e.status_code}")
        raise
    except Exception as e:
        AUDIT.append(actor, verb, target, "error")
        raise HTTPException(503, f"{target} {verb} failed: {e}")


def _scenario(sid, verb):
    spec = _SCN.get(sid)
    if spec is None or not spec["triggerable"]:
        raise HTTPException(501, f"{sid} is not triggerable via the API" if verb == "trigger"
                            else f"{sid} reset not wired")
    return spec


@app.post("/api/scenarios/reset-all", tags=["scenarios"])
def reset_all(request: Request):
    """Reset every fault owner: plant-sim, the tag-server leak, then the rogue setpoint and rogue-ews.
    Operator-gated. One `reset` row with target ALL. Any owner failure answers 503."""
    actor = _require_operator(request, "reset", "ALL")
    owners, errors = {}, []
    for owner in ("plant", "scada", "ews"):
        try:
            _OWNERS[owner]["reset"](None, actor)
            owners[owner] = "reset"
        except _OwnerRefused as e:
            owners[owner] = "off" if e.off else f"error {e.code}"
            if not e.off:
                errors.append(f"{owner}: {e.detail}")
        except HTTPException as e:
            owners[owner] = f"error {e.status_code}"
            errors.append(f"{owner}: {e.detail}")
        except Exception as e:
            owners[owner] = "error"
            errors.append(f"{owner}: {e}")
    AUDIT.append(actor, "reset", "ALL", "error" if errors else "reset", {"owners": owners})
    if errors:
        raise HTTPException(503, "reset incomplete: " + " | ".join(errors)[:400])
    return {"scenario": "ALL", "status": "reset", "owners": owners}


@app.post("/api/scenarios/{sid}/trigger", tags=["scenarios"])
def trigger(sid: str, request: Request):
    """Fire a PS-series fault from the console through its owner (the dispatch table _OWNERS).
    2E: requires the operator token when configured; the action is audit-logged either way."""
    sid = _clip(sid.upper())
    actor = _require_operator(request, "trigger", sid)
    spec = _scenario(sid, "trigger")
    _dispatch(actor, "trigger", sid, lambda: _OWNERS[spec["owner"]]["trigger"](sid, actor))
    AUDIT.append(actor, "trigger", sid, "fired", _evidence_snapshot())
    return {"scenario": sid, "status": "fired", "plane": spec["plane"], "owner": spec["owner"],
            "expect_s": spec["expect_s"]}


@app.post("/api/scenarios/{sid}/reset", tags=["scenarios"])
def reset_scenario(sid: str, request: Request):
    """Reset a PS-series fault through its owner. The plant-sim /reset clears every active plant
    fault. 2E: operator-gated + audit-logged, same as trigger."""
    sid = _clip(sid.upper())
    actor = _require_operator(request, "reset", sid)
    spec = _scenario(sid, "reset")
    _dispatch(actor, "reset", sid, lambda: _OWNERS[spec["owner"]]["reset"](sid, actor))
    AUDIT.append(actor, "reset", sid, "reset")
    return {"scenario": sid, "status": "reset", "plane": spec["plane"], "owner": spec["owner"]}


# ================================================================ 2H PLC fleet ==
_SCADA_FLEET = {"ts": 0.0, "data": []}
_IN_WINDOW: dict[str, float] = {}        # PLC name -> first time the aggregator window carried it
_BG = {"started": False}


def _scada_fleet(max_age=2.0) -> list:
    """The tag server fleet registry, cached briefly (three dashboard polls read it)."""
    if time.time() - _SCADA_FLEET["ts"] > max_age:
        try:
            data = _get(SCADA + "/fleet", timeout=3)
            _SCADA_FLEET["data"] = data if isinstance(data, list) else []
        except Exception:
            _SCADA_FLEET["data"] = []
        _SCADA_FLEET["ts"] = time.time()
    return _SCADA_FLEET["data"]


def _vplc_url(name, path):
    return f"http://{name}.{FLEET_NS}.svc.cluster.local:{fleet.CONTROL_PORT}{path}"


def _library():
    return fleet.load_library(TASKS_DIR)


def _require_fleet():
    if not K8S.available():
        raise HTTPException(503, "no Kubernetes service account: the fleet runs only in the cluster")
    if not FLEET_ENROLL_KEY:
        raise HTTPException(503, "FLEET_ENROLL_KEY is not set (Secret visr-fleet): fleet changes are off")


def _background_loop():
    """Every 10 s: note when each PLC first reaches the aggregator window, and re-register any UI cell
    that plant-sim lost (cells live in the sim's memory, so a sim restart drops them)."""
    while True:
        try:
            plcs = {e["name"]: (e.get("cell") or {}).get("machines") or [] for e in _scada_fleet(max_age=5)}
            if plcs:
                keys = list(_get(AGG + "/window", timeout=8).keys())
                now = time.time()
                for name in fleet.window_hits(keys, plcs):
                    _IN_WINDOW.setdefault(name, now)
            if K8S.available():
                sim_cells = (_get(PLANT + "/cells", timeout=3).get("cells") or {})
                for dep in K8S.list("deployments", "app=vplc,visr/managed=ui"):
                    name = dep["metadata"]["name"]
                    cm = K8S.get("configmaps", f"{name}-task")
                    manifest = json.loads(((cm or {}).get("data") or {}).get("task.json") or "{}")
                    cell = (manifest.get("cell") or {}).get("name")
                    if cell and cell not in sim_cells:
                        _call("POST", PLANT + "/cells", fleet.sim_cell_body(name, manifest))
        except Exception as e:
            print(f"api: fleet background pass failed ({e})", flush=True)
        time.sleep(10)


def _ensure_background():
    if not _BG["started"] and os.environ.get("FLEET_BACKGROUND", "1") != "0":
        _BG["started"] = True
        threading.Thread(target=_background_loop, name="fleet-bg", daemon=True).start()


@app.get("/api/fleet/profiles", tags=["fleet"])
def fleet_profiles():
    """The vPLC protocol profiles. Each is a virtual PLC with a protocol profile, not vendor firmware."""
    return fleet.PROFILES


@app.get("/api/fleet/tasks", tags=["fleet"])
def fleet_tasks():
    """The task library: Structured Text sources and their cell layouts."""
    return [fleet.task_summary(n, e) for n, e in sorted(_library().items())]


@app.get("/api/fleet", tags=["fleet"])
def fleet_list():
    """Every virtual PLC with its six onboarding phases. Each timestamp is a real observation."""
    _ensure_background()
    if not K8S.available():
        return {"source": "unavailable", "enroll": "enabled" if FLEET_ENROLL_KEY else "disabled", "plcs": []}
    try:
        deps = K8S.list("deployments", "app=vplc")
        pods = K8S.list("pods", "app=vplc")
    except fleet.K8sError as e:
        return {"source": "unavailable", "error": str(e), "enroll": "enabled" if FLEET_ENROLL_KEY else "disabled",
                "plcs": []}
    scada = {e["name"]: e for e in _scada_fleet()}
    now = time.time()
    out = []
    for dep in sorted(deps, key=lambda d: d["metadata"]["name"]):
        name = dep["metadata"]["name"]
        mine = [p for p in pods if (p.get("metadata", {}).get("labels") or {}).get("visr/plc") == name]
        try:
            vstate = _get(_vplc_url(name, "/state"), timeout=1.5)
        except Exception:
            vstate = None
        out.append(fleet.plc_view(dep, mine, scada.get(name), vstate, _IN_WINDOW.get(name), now))
    return {"source": "k8s", "enroll": "enabled" if FLEET_ENROLL_KEY else "disabled", "plcs": out}


@app.post("/api/fleet/plcs", tags=["fleet"], status_code=202)
async def fleet_create(request: Request):
    """Add a virtual PLC: a cell in plant-sim, then the Secret, ConfigMap, Deployment, and Service."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "the body must be JSON")
    name = str((body or {}).get("name") or "")
    actor = _require_operator(request, "fleet-create", name or "?")
    _require_fleet()
    profile, task, rail = body.get("profile"), body.get("task"), body.get("rail")
    lib = _library()
    if not fleet.NAME_RE.match(name):
        raise HTTPException(400, "name must look like plc-<slug> (lower case, digits, dashes, at most 21 characters)")
    if profile not in fleet.PROFILE_BY_ID:
        raise HTTPException(400, f"unknown profile {profile}")
    if task not in lib:
        raise HTTPException(400, f"unknown task {task}")
    manifest = lib[task]["manifest"]
    if (manifest.get("cell") or {}).get("machines_fixed"):
        raise HTTPException(400, f"task {task} drives fixed base machines, load it on the base PLC instead")
    if K8S.get("deployments", name) is not None:
        AUDIT.append(actor, "fleet-create", name, "refused: exists")
        raise HTTPException(409, f"{name} already exists")
    try:
        plant = _get(PLANT + "/state", timeout=3)
    except Exception as e:
        raise HTTPException(503, f"plant-sim unreachable: {e}")
    rail = rail or (manifest.get("cell") or {}).get("rail_default")
    if rail not in (plant.get("rails") or {}):
        raise HTTPException(400, f"unknown rail {rail}")
    taken = set((plant.get("devices") or {}).keys())
    for cm in K8S.list("configmaps", "app=vplc"):
        try:
            taken.update(fleet.cell_machines(json.loads((cm.get("data") or {}).get("task.json") or "{}")))
        except ValueError:
            pass
    resolved = fleet.resolve_manifest(manifest, name, rail, taken)
    requested_at = time.time()
    status, sim = _call("POST", PLANT + "/cells", fleet.sim_cell_body(name, resolved))
    if status not in (200, 201):
        AUDIT.append(actor, "fleet-create", name, f"error sim {status}")
        raise HTTPException(502, f"plant-sim refused the cell: {sim.get('detail') or sim.get('error') or sim}")
    objs = fleet.objects_for(name, profile, task, lib[task]["st"], resolved,
                             fleet.device_token(FLEET_ENROLL_KEY, name), VPLC_IMAGE, requested_at)
    try:
        for kind in ("secrets", "configmaps", "deployments", "services"):
            K8S.create(kind, objs[kind])
    except fleet.K8sError as e:
        for kind in ("services", "deployments", "configmaps", "secrets"):
            try:
                K8S.delete(kind, objs[kind]["metadata"]["name"] if kind != "secrets" else f"{name}-token")
            except fleet.K8sError:
                pass
        _call("DELETE", f"{PLANT}/cells/{resolved['cell']['name']}")
        AUDIT.append(actor, "fleet-create", name, f"error k8s {e.code}")
        raise HTTPException(502, f"kubernetes refused the PLC: {e}")
    AUDIT.append(actor, "fleet-create", name, "requested",
                 {"profile": profile, "task": task, "rail": rail, "machines": fleet.cell_machines(resolved)})
    _ensure_background()
    return {"name": name, "status": "requested", "machines": fleet.cell_machines(resolved), "rail": rail}


@app.put("/api/fleet/plcs/{name}/task", tags=["fleet"])
async def fleet_load_task(name: str, request: Request):
    """Load a task into a running PLC. The new task must drive the same cell layout."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "the body must be JSON")
    task = (body or {}).get("task")
    actor = _require_operator(request, "load-task", f"{name}:{task}")
    _require_fleet()
    lib = _library()
    if task not in lib:
        raise HTTPException(400, f"unknown task {task}")
    dep = K8S.get("deployments", name)
    if dep is None:
        raise HTTPException(404, f"no PLC {name}")
    cm = K8S.get("configmaps", f"{name}-task")
    current = json.loads(((cm or {}).get("data") or {}).get("task.json") or "null") or None
    if current is None:
        current_task = next((e["value"] for c in dep["spec"]["template"]["spec"]["containers"]
                             for e in c.get("env", []) if e.get("name") == "TASK_NAME"), None)
        current = (lib.get(current_task) or {}).get("manifest") or {}
    new = lib[task]["manifest"]
    if fleet.layout(new) != fleet.layout(current):
        AUDIT.append(actor, "load-task", name, "refused: layout", {"task": task})
        raise HTTPException(409, f"task {task} drives a different cell layout than the task running on {name}")
    rail = (current.get("cell") or {}).get("rail") or (new.get("cell") or {}).get("rail_default")
    resolved = fleet.resolve_manifest(new, name, rail, set(), keep_names=fleet.cell_machines(current)
                                      if not (new.get("cell") or {}).get("machines_fixed") else None)
    if (current.get("cell") or {}).get("name") and not (new.get("cell") or {}).get("machines_fixed"):
        resolved["cell"]["name"] = current["cell"]["name"]
    labels = {"app": "vplc", "visr/plc": name}
    K8S.apply_configmap(f"{name}-task", {"task.st": lib[task]["st"], "task.json": json.dumps(resolved, indent=2)},
                        labels)
    status, out = _call("PUT", _vplc_url(name, "/task"), {"st": lib[task]["st"], "manifest": resolved},
                        {"X-Device-Token": fleet.device_token(FLEET_ENROLL_KEY, name)})
    AUDIT.append(actor, "load-task", name, "loaded" if status == 200 else f"error {status}",
                 {"task": task, "sha256": (out.get("task") or {}).get("sha256")})
    if status != 200:
        raise HTTPException(502 if status >= 500 or status == 0 else status, out.get("error") or out)
    return {"name": name, "task": out.get("task"), "state": out.get("state")}


def _fleet_control(name, request, verb):
    name = _clip(name)
    actor = _require_operator(request, verb, name)
    _require_fleet()
    status, out = _call("POST", _vplc_url(name, "/" + verb), headers={
        "X-Device-Token": fleet.device_token(FLEET_ENROLL_KEY, name)})
    AUDIT.append(actor, verb, name, out.get("state", f"error {status}") if status == 200 else f"error {status}")
    if status != 200:
        raise HTTPException(502, out.get("error") or f"{name} did not answer")
    return {"name": name, "state": out.get("state")}


@app.post("/api/fleet/plcs/{name}/run", tags=["fleet"])
def fleet_run(name: str, request: Request):
    return _fleet_control(name, request, "run")


@app.post("/api/fleet/plcs/{name}/stop", tags=["fleet"])
def fleet_stop(name: str, request: Request):
    return _fleet_control(name, request, "stop")


@app.delete("/api/fleet/plcs/{name}", tags=["fleet"])
def fleet_delete(name: str, request: Request):
    """Remove a UI-created PLC: the Deployment first (so it cannot re-enroll), then SCADA and the cell."""
    actor = _require_operator(request, "fleet-delete", name)
    _require_fleet()
    dep = K8S.get("deployments", name)
    if dep is None:
        raise HTTPException(404, f"no PLC {name}")
    if (dep["metadata"].get("labels") or {}).get("visr/managed") == "static":
        AUDIT.append(actor, "fleet-delete", name, "refused: base PLC")
        raise HTTPException(403, f"{name} is a base PLC and cannot be removed from the dashboard")
    cm = K8S.get("configmaps", f"{name}-task")
    cell = (json.loads(((cm or {}).get("data") or {}).get("task.json") or "{}").get("cell") or {}).get("name")
    for kind, obj in (("deployments", name), ("services", name), ("configmaps", f"{name}-task"),
                      ("secrets", f"{name}-token")):
        K8S.delete(kind, obj)
    _call("DELETE", f"{SCADA}/fleet/{name}", headers={"X-Scada-Token": SCADA_WRITE_TOKEN})
    if cell:
        _call("DELETE", f"{PLANT}/cells/{cell}")
    _IN_WINDOW.pop(name, None)
    AUDIT.append(actor, "fleet-delete", name, "removed", {"cell": cell})
    return {"name": name, "status": "removed"}


# ================================================================ 3D act loop ===
def _current_proposals():
    try:
        g = graph()
    except HTTPException:
        return [], {}, {}
    try:
        plant = _get(PLANT + "/state", timeout=3)
    except Exception:
        plant = {}
    scada = _scada_fleet(max_age=1.0)
    held = {b["asset"] for b in integrity.blocked(_integrity_findings(), scada)}
    return fleet.proposals(g, scada, plant, DERATE_TARGET_PCT, held), g, plant


@app.get("/api/actions", tags=["actions"])
def actions():
    """Execute proposals derived from the current verdict, and the derates in force now. `blocked`
    lists the assets that get no proposal because their controller channel has an open integrity
    finding. Each active derate says whether a signed ledger row wrote its value (`signed`)."""
    props, _, _ = _current_proposals()
    scada = _scada_fleet(max_age=1.0)
    findings = _integrity_findings()
    active = fleet.active_derates(scada)
    rows = AUDIT.entries(LEDGER_SCAN) if active else []
    for a in active:
        a["signed"] = integrity.signed(a, rows, findings)
    return {"proposals": props, "active": active, "blocked": integrity.blocked(findings, scada),
            "write": "enabled" if SCADA_WRITE_TOKEN else "disabled", "target_pct": DERATE_TARGET_PCT}


def _scada_write(plc, tag, value):
    if not SCADA_WRITE_TOKEN:
        raise HTTPException(503, "SCADA_WRITE_TOKEN is not set (Secret visr-fleet): setpoint writes are off")
    _record_intent(plc, tag, value)         # the integrity check reads the API's own write as signed
    status, out = _call("POST", f"{SCADA}/fleet/{plc}/write", {"tag": tag, "value": value},
                        {"X-Scada-Token": SCADA_WRITE_TOKEN})
    if status != 200:
        raise HTTPException(502, f"SCADA write failed ({status}): {out.get('error') or out.get('detail') or out}")
    _SCADA_FLEET["ts"] = 0.0
    return out


def _relief_check(asset, before, cites):
    time.sleep(RELIEF_CHECK_S)
    try:
        after = fleet.plant_snapshot(_get(PLANT + "/state", timeout=3), asset)
    except Exception:
        after = {}
    AUDIT.append("visr", "relief", asset, "measured", {
        "asset": asset, "rail": before.get("rail"), "volts_before": before.get("volts"),
        "volts_after": after.get("volts"), "amps_before": before.get("amps"), "amps_after": after.get("amps"),
        "after_s": RELIEF_CHECK_S, "cited": cites})


@app.post("/api/actions/execute", tags=["actions"])
async def actions_execute(request: Request):
    """Run one proposal. The id must still match the CURRENT verdict, or the answer is 409 (cite or die)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "the body must be JSON")
    pid = _clip((body or {}).get("id") or "")
    actor = _require_operator(request, "execute", pid or "?")
    props, _, plant = _current_proposals()
    prop = next((p for p in props if p["id"] == pid), None)
    if prop is None:
        AUDIT.append(actor, "execute", pid or "?", "refused: the verdict changed")
        raise HTTPException(409, "the verdict changed, review the recommendation again")
    before = fleet.plant_snapshot(plant, prop["asset"])
    out = _scada_write(prop["plc"], prop["tag"], prop["to"])
    AUDIT.append(actor, "execute", prop["asset"], "executed", {
        **prop["cites"], "verb": prop["verb"], "plc": prop["plc"], "tag": prop["tag"],
        "from": prop["from"], "to": prop["to"], "ack_ms": out.get("ack_ms")})
    threading.Thread(target=_relief_check, args=(prop["asset"], before, prop["cites"]), daemon=True).start()
    return {"status": "executed", "proposal": prop, "relief_check_s": RELIEF_CHECK_S}


@app.post("/api/actions/restore", tags=["actions"])
async def actions_restore(request: Request):
    """Write DERATE_PCT back to 100 for one asset."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "the body must be JSON")
    asset = _clip((body or {}).get("asset") or "")
    actor = _require_operator(request, "restore", asset or "?")
    active = next((a for a in fleet.active_derates(_scada_fleet(max_age=0)) if a["asset"] == asset), None)
    if active is None:
        raise HTTPException(404, f"no active derate on {asset}")
    _scada_write(active["plc"], active["tag"], 100)
    AUDIT.append(actor, "restore", asset, "restored", {"plc": active["plc"], "tag": active["tag"],
                                                        "from": active["value"], "to": 100})
    return {"status": "restored", "asset": asset}


# ========================================================= integrity (5.2) ===
_INTEG = integrity.new_state()
_INTEG_LOCK = threading.Lock()
_INTENTS: list = []                     # write intents, recorded just before each SCADA write
_INTENT_LOCK = threading.Lock()
_INTEG_BG = {"started": False}


def _record_intent(plc, tag, value):
    now = time.time()
    with _INTENT_LOCK:
        _INTENTS[:] = integrity.prune_intents(_INTENTS, now)
        _INTENTS.append(integrity.intent(plc, tag, value, now))


def _integrity_findings() -> list:
    with _INTEG_LOCK:
        return integrity.open_findings(_INTEG)


def _plc_clients(plc) -> list:
    """Best effort: workloads that Caretta saw talk to the PLC on port 102. Empty on any failure."""
    try:
        r = _get(PROM + "/api/v1/query?query=caretta_links_observed", timeout=2)
        return integrity.caretta_clients((r.get("data") or {}).get("result") or [], plc)
    except Exception:
        return []


def _integrity_pass(now=None) -> list:
    """One pass of both checks. It reads tag-server /fleet and /tags and plant-sim /state directly,
    with short timeouts. An input that does not answer marks the pass blind, and the state stays."""
    now = time.time() if now is None else now
    blind = []

    def read(name, url, kind):
        try:
            out = _get(url, timeout=3)
            if isinstance(out, kind):
                return out
        except Exception:
            pass
        blind.append(name)
        return None

    scada, tags, plant = _fanout([lambda: read("scada /fleet", SCADA + "/fleet", list),
                                  lambda: read("scada /tags", SCADA + "/tags", dict),
                                  lambda: read("plant /state", PLANT + "/state", dict)])
    with _INTENT_LOCK:
        intents = list(_INTENTS)
    events = []
    with _INTEG_LOCK:
        if scada is not None:
            events += integrity.reconcile(_INTEG, scada, intents, lambda: AUDIT.entries(LEDGER_SCAN), now)
            if tags is not None and plant is not None:
                events += integrity.balance(_INTEG, plant, scada, tags.get("tags") or [], now)
        integrity.mark(_INTEG, now, blind)
    for ev in events:
        if ev["verb"] == "unsigned" and ev["status"] == "detected":
            clients = _plc_clients(ev["evidence"]["plc"])      # outside the lock: Prometheus can be slow
            with _INTEG_LOCK:
                integrity.set_clients(_INTEG, ev, clients)
        AUDIT.append("visr", ev["verb"], ev["target"], ev["status"], ev["evidence"])
    return events


def _integrity_loop():
    while True:
        try:
            _integrity_pass()
        except Exception as e:
            print(f"api: integrity pass failed ({e})", flush=True)
        time.sleep(RECONCILE_S)


def _ensure_integrity():
    if not _INTEG_BG["started"] and os.environ.get("INTEGRITY_BACKGROUND", "1") != "0":
        _INTEG_BG["started"] = True
        threading.Thread(target=_integrity_loop, name="integrity-bg", daemon=True).start()


@app.get("/api/integrity", tags=["integrity"])
def integrity_view():
    """Integrity checks (SCENARIOS.md 5.2): unsigned setpoint changes and the current balance per rail.
    `source` is live, blind (the last pass could not read an input), or off (no pass has run)."""
    with _INTEG_LOCK:
        return integrity.view(_INTEG)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """LOG-088: the verdict as Prometheus series (api/metrics.py), read-only. The ServiceMonitor
    api-verdict (deploy/api.yaml) scrapes it every 5 s. An engine that does not answer gives
    visr_engine_up 0 and no verdict series, never an error."""
    try:
        g = graph()
    except HTTPException:
        g = None
    derates = fleet.active_derates(_scada_fleet())
    return PlainTextResponse(metrics.exposition(g, _integrity_findings(), derates),
                             media_type="text/plain; version=0.0.4")
