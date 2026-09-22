"""2H fleet routes and the 3D act loop through FastAPI, with fake Kubernetes, SCADA, sim, and vPLC."""
import importlib
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

import fleet

TASKS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vplc", "tasks")
TOKEN = "op-token"


class FakeK8s:
    def __init__(self):
        self.objs = {k: {} for k in ("deployments", "services", "configmaps", "secrets", "pods")}

    def available(self):
        return True

    @staticmethod
    def _match(obj, selector):
        labels = obj.get("metadata", {}).get("labels") or {}
        return all(labels.get(k) == v for k, v in (kv.split("=") for kv in (selector or "").split(",") if kv))

    def list(self, kind, selector=None):
        return [o for o in self.objs[kind].values() if self._match(o, selector)]

    def get(self, kind, name):
        return self.objs[kind].get(name)

    def create(self, kind, obj):
        name = obj["metadata"]["name"]
        if name in self.objs[kind]:
            raise fleet.K8sError(409, "exists")
        self.objs[kind][name] = obj
        return obj

    def patch(self, kind, name, patch):
        self.objs[kind][name].update(patch)

    def delete(self, kind, name):
        return self.objs[kind].pop(name, None) is not None

    def apply_configmap(self, name, data, labels):
        if name in self.objs["configmaps"]:
            self.objs["configmaps"][name]["data"] = data
        else:
            self.create("configmaps", {"metadata": {"name": name, "labels": labels}, "data": data})


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("VISR_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("FLEET_ENROLL_KEY", "enroll-key")
    monkeypatch.setenv("SCADA_WRITE_TOKEN", "write-token")
    monkeypatch.setenv("TASKS_DIR", TASKS)
    monkeypatch.setenv("FLEET_BACKGROUND", "0")
    monkeypatch.setenv("RELIEF_CHECK_S", "0")
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    k8s = FakeK8s()
    monkeypatch.setattr(main, "K8S", k8s)
    calls = []
    world = {
        "plant": {"rails": {"psu-a": {"volts": 345.0}, "psu-b": {"volts": 372.0}, "psu-c": {"volts": 399.0}},
                  "devices": {"press-1": {"rail": "psu-a", "amps": 80.0}, "pack-conveyor-1": {"rail": "psu-c"}}},
        "scada": [],
        "graph": {"root": [], "edges": []},
    }

    def fake_get(url, timeout=8):
        if url.endswith("/state") and "fleet.svc" in url:
            return {"state": "RUN", "task": {"name": "packaging-cell"}, "scan": {"last_ms": 0.1}}
        if url.endswith("/state"):
            return world["plant"]
        if url.endswith("/fleet"):
            return world["scada"]
        raise OSError(f"unexpected GET {url}")

    def fake_call(method, url, body=None, headers=None, timeout=5):
        calls.append((method, url, body, headers))
        if "/write" in url:
            return 200, {"written": True, "ack_ms": 0.3}
        if url.endswith("/task") and method == "PUT":
            return 200, {"loaded": True, "state": "RUN", "task": {"name": body["manifest"]["task"], "sha256": "x"}}
        if url.endswith("/cells") and method == "POST":
            return 201, {"cell": body["cell"]}
        return 200, {"state": "RUN"}

    monkeypatch.setattr(main, "_get", fake_get)
    monkeypatch.setattr(main, "_call", fake_call)
    monkeypatch.setattr(main, "graph", lambda: world["graph"])
    client = TestClient(main.app)
    return main, client, k8s, calls, world


H = {"X-Auth-Token": TOKEN, "X-Remote-User": "operator"}


def test_create_requires_the_operator_and_is_audited(app):
    main, client, k8s, calls, _ = app
    r = client.post("/api/fleet/plcs", json={"name": "plc-pack", "profile": "generic-iec", "task": "packaging-cell"})
    assert r.status_code == 401
    assert main.AUDIT.entries()[-1]["status"] == "denied"
    assert k8s.objs["deployments"] == {}


def test_create_builds_the_cell_and_the_four_objects(app):
    main, client, k8s, calls, _ = app
    r = client.post("/api/fleet/plcs", headers=H,
                    json={"name": "plc-pack", "profile": "generic-iec", "task": "packaging-cell", "rail": "psu-c"})
    assert r.status_code == 202, r.text
    assert r.json()["machines"] == ["pack-conveyor-2", "pack-wrapper-1", "pack-labeler-1"]   # -1 is taken
    sim = next(c for c in calls if c[1].endswith("/cells"))
    assert sim[2]["plc_host"] == "plc-pack.fleet.svc.cluster.local" and sim[2]["rail"] == "psu-c"
    assert set(k8s.objs["deployments"]) == {"plc-pack"} and "plc-pack-token" in k8s.objs["secrets"]
    manifest = json.loads(k8s.objs["configmaps"]["plc-pack-task"]["data"]["task.json"])
    assert manifest["cell"]["machines"][0]["name"] == "pack-conveyor-2"
    row = main.AUDIT.entries()[-1]
    assert row["verb"] == "fleet-create" and row["actor"] == "operator" and row["status"] == "requested"
    assert client.post("/api/fleet/plcs", headers=H, json={"name": "plc-pack", "profile": "generic-iec",
                                                           "task": "packaging-cell"}).status_code == 409


def test_create_validates_inputs(app):
    _, client, _, _, _ = app
    bad = [{"name": "PLC_X", "profile": "generic-iec", "task": "packaging-cell"},
           {"name": "plc-x", "profile": "nope", "task": "packaging-cell"},
           {"name": "plc-x", "profile": "generic-iec", "task": "nope"},
           {"name": "plc-x", "profile": "generic-iec", "task": "stamping-line"},
           {"name": "plc-x", "profile": "generic-iec", "task": "packaging-cell", "rail": "psu-z"}]
    for body in bad:
        assert client.post("/api/fleet/plcs", headers=H, json=body).status_code == 400, body


def test_fleet_view_and_load_task_rules(app):
    main, client, k8s, calls, world = app
    client.post("/api/fleet/plcs", headers=H, json={"name": "plc-pack", "profile": "generic-iec",
                                                    "task": "packaging-cell", "rail": "psu-c"})
    view = client.get("/api/fleet").json()
    assert view["source"] == "k8s" and view["plcs"][0]["name"] == "plc-pack"
    assert view["plcs"][0]["phases"][0]["ts"] is not None
    r = client.put("/api/fleet/plcs/plc-pack/task", headers=H, json={"task": "packaging-cell-rush"})
    assert r.status_code == 200, r.text
    put = next(c for c in calls if c[0] == "PUT")
    assert put[2]["manifest"]["cell"]["machines"][0]["name"] == "pack-conveyor-2"     # names kept
    assert put[3]["X-Device-Token"] == fleet.device_token("enroll-key", "plc-pack")
    assert client.put("/api/fleet/plcs/plc-pack/task", headers=H, json={"task": "stamping-line"}).status_code == 409


def test_delete_refuses_the_base_plc_and_removes_a_ui_plc(app):
    main, client, k8s, calls, _ = app
    k8s.create("deployments", {"metadata": {"name": "plc-stamping", "labels": {"app": "vplc", "visr/managed": "static"}}})
    assert client.delete("/api/fleet/plcs/plc-stamping", headers=H).status_code == 403
    client.post("/api/fleet/plcs", headers=H, json={"name": "plc-pack", "profile": "generic-iec",
                                                    "task": "packaging-cell", "rail": "psu-c"})
    assert client.delete("/api/fleet/plcs/plc-pack", headers=H).status_code == 200
    assert "plc-pack" not in k8s.objs["deployments"] and "plc-pack-task" not in k8s.objs["configmaps"]
    urls = [c[1] for c in calls if c[0] == "DELETE"]
    assert any(u.endswith("/fleet/plc-pack") for u in urls) and any(u.endswith("/cells/pack") for u in urls)


def test_execute_cites_or_dies_and_restore_writes_100(app):
    main, client, k8s, calls, world = app
    world["graph"] = {"root": [{"pod": "press-1"}], "edges": [
        {"src": "press-1", "dst": "psu-a", "r": 0.8, "evidence": ["write", "rail"], "confidence": 0.9,
         "signal": "bus_voltage"}]}
    world["scada"] = [{"name": "plc-stamping", "profile": "siemens-s7-1200", "connected": True,
                       "cell": {"machines": ["press-1", "press-2"]},
                       "tags": [{"tag": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "asset": "press-1",
                                 "signal": "DERATE_PCT", "value": 100, "quality": "GOOD"}]}]
    props = client.get("/api/actions").json()["proposals"]
    assert len(props) == 1 and props[0]["to"] == 55
    assert client.post("/api/actions/execute", headers=H, json={"id": "stale"}).status_code == 409
    r = client.post("/api/actions/execute", headers=H, json={"id": props[0]["id"]})
    assert r.status_code == 200, r.text
    write = next(c for c in calls if "/write" in c[1])
    assert write[2] == {"tag": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "value": 55}
    assert write[3]["X-Scada-Token"] == "write-token"
    verbs = [e["verb"] for e in main.AUDIT.entries()]
    assert "execute" in verbs
    world["scada"][0]["tags"][0]["value"] = 55
    assert client.get("/api/actions").json()["active"][0]["value"] == 55
    assert client.post("/api/actions/restore", headers=H, json={"asset": "press-1"}).status_code == 200
    assert [c for c in calls if "/write" in c[1]][-1][2]["value"] == 100
    assert main.AUDIT.verify()[0] is True
