"""L4 API gateway — a frontend-agnostic REST seam over the L2 aggregator + L3 engine.

It proxies and *normalizes* the causal graph, per-pod signals, and anomaly events into clean,
stable JSON with permissive CORS and an auto-generated OpenAPI spec at /docs — so any frontend
(React, Vue, a plain HTML page, a CLI) can consume the system without knowing the internal
service names or payload shapes. No causal logic lives here; the reasoning stays in L3. The one
transform it applies is collapsing live pod names (`cooling-monitor-6644486769-6wlst`) to stable
workload names (`cooling-monitor`) so a UI can key off something that survives restarts.

Env: ENGINE_URL, AGGREGATOR_URL, COOLING_URL, ENGINE_SIGNAL.
"""
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ENGINE = os.environ.get("ENGINE_URL", "http://correlation-engine.aiops.svc:9100").rstrip("/")
AGG = os.environ.get("AGGREGATOR_URL", "http://aggregator.aiops.svc:9000").rstrip("/")
PROM = os.environ.get("PROM_URL", "http://prom-kube-prometheus-stack-prometheus.observability.svc.cluster.local.:9090").rstrip("/")  # Caretta topology source (eBPF L4 service map)
COOLING = os.environ.get("COOLING_URL", "http://cooling-monitor.factory-data.svc:8080").rstrip("/")
PLANT = os.environ.get("PLANT_URL", "http://plant-sim.plant.svc:9200").rstrip("/")  # plane-2 physics sim (pivot, LOG-029)
SIGNAL = os.environ.get("ENGINE_SIGNAL", "psi_io")             # primary/default resource class
SIGNALS = [s.strip() for s in os.environ.get("ENGINE_SIGNALS", "psi_io,psi_cpu,psi_mem").split(",") if s.strip()]
SIGNAL_RESOURCE = {"psi_io": "disk I/O", "psi_cpu": "CPU", "psi_mem": "memory",
                   "bus_voltage": "rail voltage", "coolant_temp": "coolant temperature"}  # ground the narrator's resource word
# The one LLM. Unset OLLAMA_HOST -> /api/narrative serves the deterministic template only, so the
# verdict never depends on the model being reachable (the demo must survive a model outage).
OLLAMA = os.environ.get("OLLAMA_HOST", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b-it-qat")  # default matches deploy/api.yaml (QAT 6.1GB)
# Right-sizing thresholds (PS-Q4): p95 usage vs requests/limits -> KAI scheduler verbs.
RECLAIM_FRAC = float(os.environ.get("RECLAIM_FRAC", "0.5"))   # p95 < this * request -> over-provisioned -> reclaim
RESIZE_FRAC = float(os.environ.get("RESIZE_FRAC", "0.85"))    # p95 > this * limit  -> at-risk -> resize up
HEADROOM = 1.3                                                # reclaim target = p95 * headroom

app = FastAPI(
    title="SiliconKnights Edge Causal AIOps API",
    version="1.0",
    description="Frontend-agnostic REST over the causal correlation engine (L3) and the "
                "telemetry aggregator (L2). Read endpoints under /api; OpenAPI at /openapi.json.",
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


KUBE = "https://kubernetes.default.svc"
_SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def _k8s(method, path, body=None):
    """Minimal in-cluster Kubernetes API call via the pod ServiceAccount (no client lib). Used only
    by the scenario console (create the S2 Job; patch the S5 leak flag) under a bounded Role."""
    with open(_SA + "/token") as f:
        token = f.read().strip()
    ctx = ssl.create_default_context(cafile=_SA + "/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    ct = "application/strategic-merge-patch+json" if method == "PATCH" else "application/json"
    req = urllib.request.Request(KUBE + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": ct, "Accept": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.load(r)


def _trigger_s2():
    """Clone the suspended log-archiver CronJob into a fixed-name Job (== scenarios/S2/trigger.sh).
    The name MUST stay `log-archiver-s2` so workload() resolves it to `log-archiver` (LOG-075)."""
    ns, name = "factory-data", "log-archiver-s2"
    job_url = f"/apis/batch/v1/namespaces/{ns}/jobs/{name}"
    try:
        _k8s("DELETE", job_url + "?propagationPolicy=Background")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    for _ in range(20):  # fixed name -> wait for the old Job to clear before recreating (avoid 409)
        try:
            _k8s("GET", job_url)
            time.sleep(0.5)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            raise
    cj = _k8s("GET", f"/apis/batch/v1/namespaces/{ns}/cronjobs/log-archiver")
    job = {"apiVersion": "batch/v1", "kind": "Job",
           "metadata": {"name": name, "namespace": ns},
           "spec": cj["spec"]["jobTemplate"]["spec"]}
    _k8s("POST", f"/apis/batch/v1/namespaces/{ns}/jobs", job)
    return {"scenario": "S2", "status": "fired", "job": name}


def _leak(value):
    """Patch vision-qc's LEAK_ENABLED (== scenarios/S5/{trigger,reset}.sh); template change -> rollout."""
    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": "vision-qc", "env": [{"name": "LEAK_ENABLED", "value": value}]}]}}}}
    _k8s("PATCH", "/apis/apps/v1/namespaces/factory-edge/deployments/vision-qc", patch)


def _parse_caretta(result, ns_prefix="factory"):
    """Collapse Caretta's `caretta_links_observed` series (Caretta emits one per role/kind) into a
    single directed workload edge per (client -> server), scoped to the factory namespaces (drops
    monitoring/infra flows). client_name/server_name are already workload names -- no pod-hash
    stripping needed. Keeps the largest observed byte count per pair."""
    best = {}
    for s in result:
        m = s.get("metric", {})
        cn, sn = m.get("client_name"), m.get("server_name")
        cns, sns = m.get("client_namespace", ""), m.get("server_namespace", "")
        if not cn or not sn or cn == sn:
            continue
        if not (cns.startswith(ns_prefix) and sns.startswith(ns_prefix)):
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
                       "think": False, "keep_alive": "10m",  # stay warm through an incident (P5)
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
    return (f"{cause} is the likely root cause of {signal} contention: its activity correlates with "
            f"{victim} over {ev}{eta_txt}{reg_txt}.")


def _incipient_text(incip) -> str:
    """Deterministic OOM early-warning line (pods already workload-normalized by graph())."""
    f = min(incip, key=lambda x: x.get("eta_s") if x.get("eta_s") is not None else 1e9)
    return (f"Early warning: {f['pod']} is trending toward its memory limit "
            f"({_fmt_mem(f.get('value') or 0)} of {_fmt_mem(f.get('limit') or 0)}) — "
            f"projected OOM in ~{int(f.get('eta_s') or 0)}s.")


_PLANT_ENTITIES: set = set()
_PLANT_ENTITIES_TS: float = 0.0


def _plant_entities() -> set:
    """Plant entities are NOT k8s pods — the hash-stripping heuristic mangles the one
    multi-segment plant name (qa-scanner-1 -> "qa"), the same LOG-033 trap the engine already
    guards in its own workload(). The set comes from the sim's /state (devices + rails + loop),
    TTL-cached so /api/graph never blocks on the sim; a fetch failure keeps the last known set."""
    global _PLANT_ENTITIES, _PLANT_ENTITIES_TS
    if time.time() - _PLANT_ENTITIES_TS > 30:
        _PLANT_ENTITIES_TS = time.time()          # even on failure: don't hammer a down sim
        try:
            s = _get(PLANT + "/state")
            _PLANT_ENTITIES = (set(s.get("devices", {})) | set(s.get("rails", {}))
                               | ({s["loop"]["name"]} if s.get("loop", {}).get("name") else set()))
        except Exception:
            pass
    return _PLANT_ENTITIES


def workload(pod: str) -> str:
    """cooling-monitor-6644486769-6wlst -> cooling-monitor (drop replicaset + pod hash).
    Known plant entities keep their names verbatim — they carry no k8s hashes to strip."""
    if pod in _plant_entities():
        return pod
    parts = pod.split("-")
    return "-".join(parts[:-2]) if len(parts) > 2 else pod


# PS-series = the plant-physics demo scenarios (pivot, LOG-028/029): faults perturb the MODEL,
# symptoms emerge. S-series = the kernel-plane motifs, retired to the regression bench (their
# factory targets are torn down; they live on as box-verified fixtures + the frozen Codex demo).
SCENARIOS = [
    {"id": "PS0", "name": "Steady plant", "mechanism": "no faults; baselines mature, engine stays silent", "triggerable": False},
    {"id": "PS1", "name": "Rail-sag cascade", "mechanism": "press-1 bearing friction -> amps up -> rail A sags -> mates degrade", "triggerable": True},
    {"id": "PS2", "name": "Duty-cycle aggressor stuck on", "mechanism": "compressor-1 pinned at full draw on rail B (no matured baseline)", "triggerable": True},
    {"id": "PS5", "name": "Coolant pump degradation", "mechanism": "flow drops -> temps ramp toward the 78C trip (forecast beat)", "triggerable": True},
    {"id": "S0", "name": "Steady-state control (bench)", "mechanism": "kernel plane; retired to the regression bench", "triggerable": False},
    {"id": "S1", "name": "PVC I/O contention cascade (bench)", "mechanism": "kernel plane; retired to the regression bench", "triggerable": False},
    {"id": "S2", "name": "Large-file I/O starvation (bench)", "mechanism": "kernel plane; retired to the regression bench", "triggerable": False},
    {"id": "S5", "name": "Memory leak + OOM (bench)", "mechanism": "kernel plane; retired to the regression bench", "triggerable": False},
]


@app.get("/api/health", tags=["meta"])
def health():
    """Reachability of the upstream L2/L3 services."""
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "services": {}}
    for name, url in (("aggregator", AGG + "/healthz"), ("engine", ENGINE + "/healthz")):
        try:
            urllib.request.urlopen(url, timeout=3)
            out["services"][name] = "up"
        except Exception:
            out["services"][name] = "down"
    out["ok"] = all(v == "up" for v in out["services"].values())
    return out


@app.get("/api/graph", tags=["causal"])
def graph():
    """The current causal verdict from L3 — root cause, edges (with evidence), blast radius,
    findings — with pod names normalized to stable workload names for the UI."""
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
        "meta": g.get("meta", {}),
    }


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
    prompt = (
        "You are an SRE assistant. Given this causal verdict JSON from a Kubernetes resource-"
        f"contention engine, write ONE or TWO plain sentences for an on-call operator. The contended "
        f"resource is {resource}; call it {resource} contention and do NOT name any other resource "
        "type (not memory, not CPU). The root-cause pod is the SOURCE; the blast-radius pods are the "
        "affected VICTIMS. Cite only the evidence types and ETAs present in the JSON; do not invent "
        "metrics, numbers, or causes. If there is no root cause, say the system is steady.\n\n"
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
    """Auto-discovered L4 service map from Caretta (eBPF) — who-talks-to-whom across the factory,
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


@app.get("/api/scenarios", tags=["scenarios"])
def scenarios():
    """Catalogue of fault scenarios and whether each can be fired through this API."""
    return SCENARIOS


@app.post("/api/scenarios/{sid}/trigger", tags=["scenarios"])
def trigger(sid: str):
    """Fire a scenario from the console. S1 arms cooling-monitor's fio over HTTP; S2 clones the
    archiver CronJob into a Job; S5 flips vision-qc's leak flag (both via a bounded ServiceAccount)."""
    sid = sid.upper()
    try:
        if sid in ("PS1", "PS2", "PS5"):
            _post(PLANT + "/fault/" + sid)
            return {"scenario": sid, "status": "fired", "plane": "plant"}
        if sid == "S1":
            _post(COOLING + "/flush")
            return {"scenario": "S1", "status": "armed"}
        if sid == "S2":
            return _trigger_s2()
        if sid == "S5":
            _leak("true")
            return {"scenario": "S5", "status": "fired"}
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, f"k8s: {e.read().decode()[:200]}")
    except Exception as e:
        raise HTTPException(503, f"{sid} trigger failed: {e}")
    raise HTTPException(501, f"{sid} is not triggerable via the API")


@app.post("/api/scenarios/{sid}/reset", tags=["scenarios"])
def reset_scenario(sid: str):
    """Reset a scenario: S2 deletes the Job, S5 clears the leak flag; S1 self-clears via the gate."""
    sid = sid.upper()
    try:
        if sid in ("PS1", "PS2", "PS5"):
            # the sim's /reset clears ALL active plant faults (one live fault at a time, demo-wise)
            _post(PLANT + "/reset")
            return {"scenario": sid, "status": "reset", "plane": "plant"}
        if sid == "S2":
            try:
                _k8s("DELETE", "/apis/batch/v1/namespaces/factory-data/jobs/log-archiver-s2?propagationPolicy=Background")
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
            return {"scenario": "S2", "status": "reset"}
        if sid == "S5":
            _leak("false")
            return {"scenario": "S5", "status": "reset"}
        if sid == "S1":
            return {"scenario": "S1", "status": "self-clears via recency gate"}
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, f"k8s: {e.read().decode()[:200]}")
    raise HTTPException(501, f"{sid} reset not wired")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}
