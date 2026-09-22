"""2H virtual PLC fleet and the 3D act loop, API side (FLEET.md section 10).

This module holds the logic without HTTP routes, so it unit-tests without a cluster:
  - K8s: a small Kubernetes REST client that uses the pod service-account token (no client library)
  - the task library read from the vplc-tasks ConfigMap mount
  - resolve_manifest: give each cell machine a free name <prefix>-<n> and set the chosen rail
  - objects_for: the Secret, ConfigMap, Deployment, and Service of one PLC
  - fleet_view: merge Kubernetes, SCADA, vPLC, and window facts into the /api/fleet shape
  - proposals: derive the Execute proposals from the current verdict (cite or die)

main.py owns the routes, the operator gate, and the audit ledger.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

NAME_RE = re.compile(r"^plc-[a-z0-9]([-a-z0-9]{0,16}[a-z0-9])?$")
PHASES = ("requested", "scheduled", "running", "enrolled", "polling", "in_window")

PROFILES = [
    {"id": "siemens-s7-1200", "label": "Siemens S7-1200 (virtual)", "protocol": "s7comm", "port": 102},
    {"id": "generic-iec", "label": "IEC 61131-3 soft PLC", "protocol": "modbus", "port": 502},
]
PROFILE_BY_ID = {p["id"]: p for p in PROFILES}
FIELD_PORT, CONTROL_PORT = 5020, 8080


def device_token(key: str, name: str) -> str:
    """The per-PLC enrollment and control token: hex HMAC-SHA256(key, name)."""
    return hmac.new(key.encode(), name.encode(), hashlib.sha256).hexdigest()


def tag_name(plc: str, asset: str, signal: str) -> str:
    """FLEET.<PLC>.<ASSET>.<SIGNAL>, upper case, '-' becomes '_' (FLEET.md 8)."""
    up = lambda s: s.upper().replace("-", "_")
    return f"FLEET.{up(plc)}.{up(asset)}.{signal}"


def rfc3339(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ------------------------------------------------------------------- Kubernetes --
class K8sError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"kubernetes {code}: {message}")
        self.code = code


class K8s:
    """Namespaced CRUD over the Kubernetes REST API with the pod service-account token."""

    SA = "/var/run/secrets/kubernetes.io/serviceaccount"
    PATHS = {"deployments": "/apis/apps/v1", "services": "/api/v1", "configmaps": "/api/v1",
             "secrets": "/api/v1", "pods": "/api/v1"}

    def __init__(self, namespace: str, sa_dir: str | None = None):
        self.ns = namespace
        sa = sa_dir or self.SA
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        self.base = f"https://{host}:{port}" if host else None
        self.token_path = os.path.join(sa, "token")
        self.ca_path = os.path.join(sa, "ca.crt")

    def available(self) -> bool:
        return bool(self.base) and os.path.exists(self.token_path)

    def _request(self, method: str, url: str, body: dict | None = None,
                 ctype: str = "application/json", timeout: float = 5.0):
        if not self.available():
            raise K8sError(503, "no in-cluster service account")
        with open(self.token_path, encoding="utf-8") as f:
            token = f.read().strip()
        ctx = ssl.create_default_context(cafile=self.ca_path) if os.path.exists(self.ca_path) else None
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": ctype, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("message", detail)
            except ValueError:
                pass
            raise K8sError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise K8sError(503, str(e.reason)) from None

    def _url(self, kind: str, name: str | None = None, query: dict | None = None) -> str:
        url = f"{self.PATHS[kind]}/namespaces/{self.ns}/{kind}" + (f"/{name}" if name else "")
        return url + ("?" + urllib.parse.urlencode(query) if query else "")

    def list(self, kind: str, selector: str | None = None) -> list:
        return self._request("GET", self._url(kind, query={"labelSelector": selector} if selector else None)
                             ).get("items", [])

    def get(self, kind: str, name: str) -> dict | None:
        try:
            return self._request("GET", self._url(kind, name))
        except K8sError as e:
            if e.code == 404:
                return None
            raise

    def create(self, kind: str, obj: dict) -> dict:
        return self._request("POST", self._url(kind), obj)

    def patch(self, kind: str, name: str, patch: dict) -> dict:
        return self._request("PATCH", self._url(kind, name), patch, ctype="application/merge-patch+json")

    def delete(self, kind: str, name: str) -> bool:
        try:
            self._request("DELETE", self._url(kind, name), {"propagationPolicy": "Background"})
            return True
        except K8sError as e:
            if e.code == 404:
                return False
            raise

    def apply_configmap(self, name: str, data: dict, labels: dict) -> None:
        """Create the ConfigMap, or replace its data when it exists."""
        if self.get("configmaps", name) is None:
            self.create("configmaps", {"apiVersion": "v1", "kind": "ConfigMap",
                                       "metadata": {"name": name, "labels": labels}, "data": data})
        else:
            self.patch("configmaps", name, {"data": data})


# --------------------------------------------------------------- task library --
class TaskError(ValueError):
    pass


def load_library(tasks_dir: str) -> dict[str, dict]:
    """{task name: {manifest, st}} from a directory of <task>.json and <task>.st files."""
    lib = {}
    try:
        names = sorted(os.listdir(tasks_dir))
    except OSError:
        return lib
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(tasks_dir, fn), encoding="utf-8") as f:
                manifest = json.load(f)
            st_file = os.path.basename(manifest.get("st") or fn[:-5] + ".st")
            with open(os.path.join(tasks_dir, st_file), encoding="utf-8") as f:
                source = f.read()
        except (OSError, ValueError):
            continue
        lib[manifest.get("task") or fn[:-5]] = {"manifest": manifest, "st": source}
    return lib


def task_summary(name: str, entry: dict) -> dict:
    m = entry["manifest"]
    return {"name": name, "title": m.get("title") or name, "description": m.get("description", ""),
            "profile_hint": m.get("profile_hint"), "cell": m.get("cell") or {}, "st": entry["st"]}


def layout(manifest: dict) -> tuple:
    """The cell layout that two tasks must share for Load task: fixed machines or machine prefixes."""
    cell = manifest.get("cell") or {}
    if cell.get("machines_fixed"):
        return ("fixed",) + tuple(cell["machines_fixed"])
    return ("prefix",) + tuple(m.get("prefix") or m.get("name") for m in cell.get("machines") or [])


def free_name(prefix: str, taken: set) -> str:
    n = 1
    while f"{prefix}-{n}" in taken:
        n += 1
    return f"{prefix}-{n}"


def resolve_manifest(manifest: dict, plc: str, rail: str, taken: set, keep_names: list | None = None) -> dict:
    """Return a copy of the manifest with cell.name = the PLC slug, cell.rail = rail, and every machine
    named. keep_names reuses the names of a running cell (Load task). taken grows with each new name."""
    m = json.loads(json.dumps(manifest))
    cell = m.setdefault("cell", {})
    cell["rail"] = rail
    if cell.get("machines_fixed"):
        return m                                   # a base cell keeps its fixed machines and name
    cell["name"] = plc[4:] if plc.startswith("plc-") else plc
    for i, mach in enumerate(cell.get("machines") or []):
        if keep_names and i < len(keep_names):
            mach["name"] = keep_names[i]
        else:
            mach["name"] = free_name(mach.get("prefix") or "machine", taken)
        taken.add(mach["name"])
    return m


def cell_machines(manifest: dict) -> list[str]:
    cell = manifest.get("cell") or {}
    if cell.get("machines_fixed"):
        return list(cell["machines_fixed"])
    return [m.get("name") or m.get("prefix") for m in cell.get("machines") or []]


def sim_cell_body(plc: str, manifest: dict) -> dict:
    """The plant-sim POST /cells body for a resolved manifest (FLEET.md 7)."""
    cell = manifest["cell"]
    machines = []
    for mach in cell.get("machines") or []:
        row = {"name": mach["name"], "kind": mach.get("kind") or mach["name"].rsplit("-", 1)[0],
               "i_base": float(mach.get("i_base", 5.0)), "cooled": bool(mach.get("cooled", False))}
        for k in ("tau", "heat_k"):
            if k in mach:
                row[k] = float(mach[k])
        if mach.get("v_sensitive"):
            row["v_sensitive"] = True
        machines.append(row)
    return {"cell": cell["name"], "plc_host": f"{plc}.fleet.svc.cluster.local", "field_port": FIELD_PORT,
            "rail": cell["rail"], "fail_open": False, "machines": machines}


# ---------------------------------------------------------------- objects ------
def objects_for(plc: str, profile: str, task: str, st: str, manifest: dict, token: str, image: str,
                requested_at: float) -> dict:
    """The four Kubernetes objects of one UI-created PLC."""
    p = PROFILE_BY_ID[profile]
    labels = {"app": "vplc", "visr/plc": plc, "visr/profile": profile, "visr/managed": "ui"}
    scada = {"name": "s7comm" if p["protocol"] == "s7comm" else "modbus", "port": p["port"]}
    ports = [scada, {"name": "field", "port": FIELD_PORT}, {"name": "control", "port": CONTROL_PORT}]
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": f"{plc}-token", "labels": labels},
              "data": {"token": base64.b64encode(token.encode()).decode()}}
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": f"{plc}-task", "labels": labels},
                 "data": {"task.st": st, "task.json": json.dumps(manifest, indent=2)}}
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": plc, "labels": labels, "annotations": {"visr/requested-at": f"{requested_at:.3f}"}},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"visr/plc": plc}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [{
                        "name": "vplc", "image": image, "imagePullPolicy": "Always",
                        "env": [
                            {"name": "PLC_NAME", "value": plc},
                            {"name": "PROFILE", "value": profile},
                            {"name": "TASK_NAME", "value": task},
                            {"name": "TASK_DIR", "value": "/task"},
                            {"name": "TZ", "value": "Asia/Kolkata"},
                            {"name": "DEVICE_TOKEN",
                             "valueFrom": {"secretKeyRef": {"name": f"{plc}-token", "key": "token"}}},
                        ],
                        "ports": [{"name": x["name"], "containerPort": x["port"]} for x in ports],
                        "volumeMounts": [{"name": "task", "mountPath": "/task", "readOnly": True}],
                        "readinessProbe": {"httpGet": {"path": "/healthz", "port": CONTROL_PORT},
                                           "initialDelaySeconds": 2},
                        "resources": {"requests": {"cpu": "25m", "memory": "48Mi"},
                                      "limits": {"cpu": "250m", "memory": "128Mi"}},
                    }],
                    "volumes": [{"name": "task", "configMap": {"name": f"{plc}-task", "optional": True}}],
                },
            },
        },
    }
    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": plc, "labels": labels},
               "spec": {"selector": {"visr/plc": plc},
                        "ports": [{"name": x["name"], "port": x["port"], "targetPort": x["port"]} for x in ports]}}
    return {"secrets": secret, "configmaps": configmap, "deployments": deployment, "services": service}


# ------------------------------------------------------------------ fleet view --
def _pod_times(pods: list) -> tuple[float | None, float | None, bool]:
    """(scheduled, running, ready) of the newest pod."""
    if not pods:
        return None, None, False
    pod = max(pods, key=lambda p: rfc3339(p.get("metadata", {}).get("creationTimestamp")) or 0)
    scheduled = rfc3339(pod.get("metadata", {}).get("creationTimestamp"))
    running, ready = None, False
    for cs in (pod.get("status") or {}).get("containerStatuses") or []:
        started = ((cs.get("state") or {}).get("running") or {}).get("startedAt")
        running = rfc3339(started) or running
        ready = ready or bool(cs.get("ready"))
    return scheduled, running, ready


def plc_view(dep: dict, pods: list, scada: dict | None, vplc: dict | None, in_window_at: float | None,
             now: float) -> dict:
    """One /api/fleet entry (FLEET.md 10)."""
    meta = dep.get("metadata", {})
    labels = meta.get("labels") or {}
    name = meta.get("name")
    profile = labels.get("visr/profile", "generic-iec")
    p = PROFILE_BY_ID.get(profile, PROFILE_BY_ID["generic-iec"])
    requested = rfc3339(meta.get("creationTimestamp"))
    try:
        requested = float((meta.get("annotations") or {}).get("visr/requested-at")) or requested
    except (TypeError, ValueError):
        pass
    scheduled, running, ready = _pod_times(pods)
    scada = scada or {}
    vplc = vplc or {}
    task = vplc.get("task") or scada.get("task") or {}
    cell = scada.get("cell") or {}
    tags = scada.get("tags") or []
    good = sum(1 for t in tags if t.get("quality") == "GOOD")
    if vplc.get("state") in ("RUN", "STOP", "FAULT"):
        state = vplc["state"]
    elif running is None or (requested and now - requested < 90):
        state = "STARTING"
    else:
        state = "OFFLINE"
    phases = [{"phase": "requested", "ts": requested}, {"phase": "scheduled", "ts": scheduled},
              {"phase": "running", "ts": running}, {"phase": "enrolled", "ts": scada.get("enrolled_at")},
              {"phase": "polling", "ts": scada.get("first_good_at")}, {"phase": "in_window", "ts": in_window_at}]
    return {
        "name": name, "profile": profile, "profile_label": p["label"],
        "protocol": {"kind": p["protocol"], "port": p["port"]},
        "managed": labels.get("visr/managed", "ui"),
        "task": {"name": task.get("name"), "title": task.get("title"), "sha256": task.get("sha256"),
                 "interval_ms": task.get("interval_ms")},
        "cell": {"name": cell.get("name"), "rail": cell.get("rail"), "machines": cell.get("machines") or []},
        "state": state, "fault": vplc.get("fault"), "ready": ready,
        "scan": vplc.get("scan") or {"last_ms": None, "avg_ms": None, "max_ms": None, "overruns": None},
        "scada": {"enrolled": bool(scada), "connected": bool(scada.get("connected")),
                  "rtt_ms": scada.get("poll_rtt_ms"), "tags": len(tags), "good": good},
        "phases": phases,
    }


def window_hits(window_keys, plcs: dict[str, list[str]]) -> set[str]:
    """PLC names that have a key for their pod or one of their machines in the aggregator window.
    Window keys look like namespace/pod/signal."""
    hits = set()
    pods_seen = {k.split("/")[1] for k in window_keys if k.count("/") >= 2}
    workloads = {"-".join(p.split("-")[:-2]) for p in pods_seen if p.count("-") >= 2}   # drop the pod hash
    for name, machines in plcs.items():
        if name in workloads or any(m in pods_seen for m in machines):
            hits.add(name)
    return hits


# ------------------------------------------------------------------- act loop --
def proposal_id(verb: str, asset: str, root: str, edge: tuple) -> str:
    raw = json.dumps([verb, asset, root, list(edge)], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def controller_of(asset: str, scada_fleet: list) -> dict | None:
    for entry in scada_fleet or []:
        if asset in ((entry.get("cell") or {}).get("machines") or []):
            return entry
    return None


def derate_tag(entry: dict, asset: str) -> dict | None:
    want = tag_name(entry["name"], asset, "DERATE_PCT")
    return next((t for t in entry.get("tags") or [] if t.get("tag") == want), None)


def proposals(graph: dict, scada_fleet: list, plant: dict, target_pct: int,
              blocked: set | None = None) -> list[dict]:
    """Execute proposals from the CURRENT verdict. Every condition of FLEET.md 10 must hold.
    An asset in `blocked` has an open integrity finding on its controller channel: no proposal."""
    root = (graph.get("root") or [None])[0]
    if not root:
        return []
    asset = root["pod"]
    if asset in (blocked or ()):
        return []
    edges = [e for e in graph.get("edges") or [] if e.get("src") == asset and e.get("evidence")]
    if not edges:
        return []
    edge = max(edges, key=lambda e: abs(e.get("r") or 0.0))
    entry = controller_of(asset, scada_fleet)
    if entry is None or not entry.get("connected"):
        return []
    tag = derate_tag(entry, asset)
    if tag is None or tag.get("quality") != "GOOD" or tag.get("value") != 100:
        return []
    rail = ((plant.get("devices") or {}).get(asset) or {}).get("rail")
    return [{
        "id": proposal_id("derate", asset, asset, (edge["src"], edge["dst"])),
        "verb": "derate", "asset": asset, "plc": entry["name"], "tag": tag["tag"],
        "from": 100, "to": int(target_pct),
        "cites": {"root": asset, "edge": f"{edge['src']}→{edge['dst']}", "evidence": edge.get("evidence") or [],
                  "confidence": edge.get("confidence"), "signal": edge.get("signal")},
        "expected": f"{asset} draws less current" + (f", rail {rail} recovers" if rail else ""),
    }]


def active_derates(scada_fleet: list) -> list[dict]:
    out = []
    for entry in scada_fleet or []:
        for t in entry.get("tags") or []:
            if t.get("signal") == "DERATE_PCT" and isinstance(t.get("value"), (int, float)) and t["value"] < 100:
                out.append({"asset": t.get("asset"), "plc": entry["name"], "tag": t["tag"], "value": t["value"],
                            "quality": t.get("quality")})
    return out


def plant_snapshot(plant: dict, asset: str) -> dict:
    dev = (plant.get("devices") or {}).get(asset) or {}
    rail = dev.get("rail")
    volts = ((plant.get("rails") or {}).get(rail) or {}).get("volts") if rail else None
    return {"asset": asset, "rail": rail, "volts": volts, "amps": dev.get("amps")}
