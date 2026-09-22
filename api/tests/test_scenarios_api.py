"""SCENARIOS.md 5 through FastAPI: the catalogue and its owner dispatch, reset-all, the integrity pass,
blocked proposals, refusal rows, and the health fields. Every upstream is a fake routed by host."""
import importlib
import sys
import time
import urllib.error
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from test_fleet_api import TASKS, FakeK8s

TOKEN = "op-token"
H = {"X-Auth-Token": TOKEN, "X-Remote-User": "operator"}
TAG = "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT"
IDS = ["PS0", "PS1", "PS2", "PS3", "PS4A", "PS4B", "PS5", "PS6"]
FIELDS = {"id", "name", "mechanism", "anchor", "plane", "owner", "triggerable", "expect", "expect_s", "active"}


def _scada(derate=100):
    return [{"name": "plc-stamping", "profile": "siemens-s7-1200", "connected": True, "enrolled_at": 1.0,
             "task": {"sha256": "abc"}, "runtime": {"started_at": 1.0},
             "cell": {"name": "stamping", "machines": ["press-1", "press-2"]},
             "tags": [{"tag": TAG, "asset": "press-1", "signal": "DERATE_PCT", "address": "%MW10", "writable": True,
                       "min": 0, "max": 100, "scale": 1, "value": derate, "quality": "GOOD"}]}]


@pytest.fixture
def api(tmp_path, monkeypatch):
    for k, v in {"AUDIT_PATH": str(tmp_path / "audit.jsonl"), "VISR_OPERATOR_TOKEN": TOKEN,
                 "FLEET_ENROLL_KEY": "enroll-key", "SCADA_WRITE_TOKEN": "write-token", "TASKS_DIR": TASKS,
                 "FLEET_BACKGROUND": "0", "INTEGRITY_BACKGROUND": "0", "RELIEF_CHECK_S": "0",
                 "PLANT_URL": "http://plant.test:9200", "SCADA_URL": "http://scada.test:9300",
                 "EWS_URL": "http://ews.test:8090", "ENGINE_URL": "http://engine.test:9100",
                 "AGGREGATOR_URL": "http://agg.test:9000", "PROM_URL": "http://prom.test:9090"}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    k8s = FakeK8s()
    monkeypatch.setattr(main, "K8S", k8s)
    world = {
        "plant": {"rails": {"psu-a": {"volts": 345.0}, "psu-c": {"volts": 399.0}},
                  "devices": {"press-1": {"rail": "psu-a", "amps": 42.0}}, "active_faults": []},
        "scada": _scada(), "tags": {"source": "scada", "tags": []},
        "ews": {"active": False}, "chaos": {"active": False},
        "engine": {"root_cause_ranking": [], "edges": [], "blast_radius": [], "findings": [], "meta": {}},
        "caretta": [{"metric": {"client_name": "rogue-ews", "server_name": "plc-stamping", "server_port": "102"}},
                    {"metric": {"client_name": "tag-server", "server_name": "plc-stamping", "server_port": "102"}}],
        "down": set(),
    }
    posts, calls = [], []

    def route(url):
        u = urlsplit(url)
        if u.hostname in world["down"]:
            raise urllib.error.URLError("connection refused")
        return u.hostname, u.path

    def fake_get(url, timeout=8):
        host, path = route(url)
        table = {("plant.test", "/state"): world["plant"], ("scada.test", "/fleet"): world["scada"],
                 ("scada.test", "/tags"): world["tags"], ("scada.test", "/chaos"): world["chaos"],
                 ("ews.test", "/state"): world["ews"], ("engine.test", "/graph"): world["engine"],
                 ("prom.test", "/api/v1/query"): {"data": {"result": world["caretta"]}}}
        if (host, path) not in table:
            raise OSError(f"unexpected GET {url}")
        return table[(host, path)]

    def fake_post(url, timeout=8):
        route(url)
        posts.append(url)
        return "ok"

    def fake_call(method, url, body=None, headers=None, timeout=5):
        route(url)
        calls.append((method, url, body, headers))
        if "/write" in url:
            return 200, {"written": True, "ack_ms": 0.3}
        if url.endswith("/cells") and method == "POST":
            return 201, {"cell": body["cell"]}
        return 200, {"active": True}

    monkeypatch.setattr(main, "_get", fake_get)
    monkeypatch.setattr(main, "_post", fake_post)
    monkeypatch.setattr(main, "_call", fake_call)
    monkeypatch.setattr(main, "_probe", lambda url, timeout=3: urlsplit(url).hostname not in world["down"])
    return main, TestClient(main.app), k8s, world, posts, calls


def _rows(main, **match):
    return [e for e in main.AUDIT.entries(0) if all(e.get(k) == v for k, v in match.items())]


# --------------------------------------------------------------- catalogue --
def test_catalogue_lists_every_id_and_reads_active_from_each_owner(api):
    main, client, _, world, _, _ = api
    cat = client.get("/api/scenarios").json()
    assert [s["id"] for s in cat] == IDS
    assert all(set(s) == FIELDS for s in cat)
    assert {s["id"]: s["owner"] for s in cat} == {"PS0": None, "PS1": "plant", "PS2": "plant", "PS3": "plant",
                                                   "PS4A": "ews", "PS4B": "plant", "PS5": "plant", "PS6": "scada"}
    assert {s["id"]: s["active"] for s in cat} == {i: i == "PS0" for i in IDS}
    world["plant"]["active_faults"] = ["ps2"]
    world["ews"]["active"] = True
    world["chaos"]["active"] = True
    act = {s["id"]: s["active"] for s in client.get("/api/scenarios").json()}
    assert act == {"PS0": False, "PS1": False, "PS2": True, "PS3": False, "PS4A": True, "PS4B": False,
                   "PS5": False, "PS6": True}
    world["down"].add("ews.test")
    act = {s["id"]: s["active"] for s in client.get("/api/scenarios").json()}
    assert act["PS4A"] is None and act["PS0"] is None and act["PS2"] is True


def test_trigger_dispatches_to_each_owner(api):
    main, client, _, world, posts, calls = api
    for sid in ("PS1", "ps3", "PS4B"):
        r = client.post(f"/api/scenarios/{sid}/trigger", headers=H)
        assert r.status_code == 200 and r.json()["status"] == "fired", r.text
    assert posts == ["http://plant.test:9200/fault/PS1", "http://plant.test:9200/fault/PS3",
                     "http://plant.test:9200/fault/PS4B"]
    assert client.post("/api/scenarios/PS4A/trigger", headers=H).json()["owner"] == "ews"
    assert calls[-1][:2] == ("POST", "http://ews.test:8090/fault/PS4A")
    assert calls[-1][3]["X-Scada-Token"] == "write-token"
    assert client.post("/api/scenarios/PS6/trigger", headers=H).json()["plane"] == "edge"
    assert calls[-1][:3] == ("POST", "http://scada.test:9300/chaos/leak", {"mib_per_s": 0.5})
    assert calls[-1][3]["X-Scada-Token"] == "write-token"
    assert [e["target"] for e in _rows(main, verb="trigger", status="fired")] == ["PS1", "PS3", "PS4B", "PS4A", "PS6"]
    n = len(main.AUDIT.entries(0))
    assert client.post("/api/scenarios/PS0/trigger", headers=H).status_code == 501
    assert client.post("/api/scenarios/PS9/trigger", headers=H).status_code == 501
    assert len(main.AUDIT.entries(0)) == n


def test_reset_dispatches_to_each_owner_and_ps4a_restores_the_setpoint(api):
    main, client, _, world, posts, calls = api
    assert client.post("/api/scenarios/PS2/reset", headers=H).status_code == 200
    assert posts == ["http://plant.test:9200/reset"]
    assert client.post("/api/scenarios/PS6/reset", headers=H).status_code == 200
    assert calls[-1][1] == "http://scada.test:9300/chaos/reset"
    world["scada"] = _scada(derate=30)
    assert client.post("/api/scenarios/PS4A/reset", headers=H).status_code == 200
    write, ews = calls[-2], calls[-1]
    assert write[1] == "http://scada.test:9300/fleet/plc-stamping/write" and write[2] == {"tag": TAG, "value": 100}
    assert ews[1] == "http://ews.test:8090/reset" and ews[3]["X-Scada-Token"] == "write-token"
    [restore] = _rows(main, verb="restore")
    assert restore["status"] == "restored" and restore["target"] == "press-1"
    assert restore["evidence"] == {"plc": "plc-stamping", "tag": TAG, "from": 30, "to": 100, "reason": "scenario reset"}
    assert [i["to"] for i in main._INTENTS] == [100]                 # the write intent came first
    assert [e["target"] for e in _rows(main, verb="reset", status="reset")] == ["PS2", "PS6", "PS4A"]
    world["scada"] = _scada(derate=100)                               # already at the default: no write
    n_writes = sum(1 for c in calls if "/write" in c[1])
    assert client.post("/api/scenarios/PS4A/reset", headers=H).status_code == 200
    assert sum(1 for c in calls if "/write" in c[1]) == n_writes and len(_rows(main, verb="restore")) == 1
    assert main.AUDIT.verify()[0] is True


def test_reset_network_error_writes_an_error_row_and_answers_503(api):
    main, client, _, world, _, _ = api
    world["down"].add("plant.test")
    assert client.post("/api/scenarios/PS1/reset", headers=H).status_code == 503
    assert main.AUDIT.entries()[-1]["verb"] == "reset" and main.AUDIT.entries()[-1]["status"] == "error"
    world["down"] = {"ews.test"}
    assert client.post("/api/scenarios/PS4A/trigger", headers=H).status_code == 503
    assert main.AUDIT.entries()[-1]["status"] == "error"


def test_reset_all_resets_every_owner_with_one_row(api):
    main, client, _, world, posts, calls = api
    assert client.post("/api/scenarios/reset-all").status_code == 401
    assert main.AUDIT.entries()[-1]["target"] == "ALL" and main.AUDIT.entries()[-1]["status"] == "denied"
    world["scada"] = _scada(derate=30)
    r = client.post("/api/scenarios/reset-all", headers=H)
    assert r.status_code == 200, r.text
    assert posts == ["http://plant.test:9200/reset"]
    assert [c[1] for c in calls] == ["http://scada.test:9300/chaos/reset",
                                     "http://scada.test:9300/fleet/plc-stamping/write", "http://ews.test:8090/reset"]
    [row] = _rows(main, verb="reset", target="ALL", status="reset")
    assert row["evidence"] == {"owners": {"plant": "reset", "scada": "reset", "ews": "reset"}}
    world["down"].add("ews.test")
    world["scada"] = _scada(derate=100)
    assert client.post("/api/scenarios/reset-all", headers=H).status_code == 503
    last = main.AUDIT.entries()[-1]
    assert (last["target"], last["status"]) == ("ALL", "error") and last["evidence"]["owners"]["ews"] == "error"
    assert last["evidence"]["owners"]["plant"] == "reset"


# --------------------------------------------------------------- integrity --
def test_integrity_pass_flags_an_unsigned_write_blocks_the_asset_and_clears_on_reset(api):
    main, client, _, world, _, _ = api
    world["engine"] = {"root_cause_ranking": [{"pod": "press-1", "score": 0.9, "onset_s": 3}],
                       "edges": [{"src": "press-1", "dst": "psu-a", "r": 0.8, "lag_s": 2, "evidence": ["rail"]}],
                       "blast_radius": [], "findings": [], "meta": {}}
    assert client.get("/api/actions").json()["proposals"] != []
    t0 = time.time()
    assert main._integrity_pass(t0) == []
    world["scada"] = _scada(derate=30)                                # the rogue write: no intent, no row
    assert main._integrity_pass(t0 + 5) == []
    [ev] = main._integrity_pass(t0 + 40)
    [row] = _rows(main, verb="unsigned")
    assert (row["actor"], row["target"], row["status"]) == ("visr", TAG, "detected")
    assert row["evidence"]["clients"] == ["rogue-ews"] and row["evidence"]["reason"] == "no ledger row"
    view = client.get("/api/integrity").json()
    assert view["source"] == "live" and [f["kind"] for f in view["findings"]] == ["unsigned_write"]
    assert view["findings"][0]["clients"] == ["rogue-ews"]
    assert [f["tag"] for f in client.get("/api/graph").json()["integrity"]] == [TAG]
    main._SCADA_FLEET["ts"] = 0.0                                     # drop the 1 s /fleet cache
    acts = client.get("/api/actions").json()
    assert acts["proposals"] == []
    assert acts["blocked"] == [{"asset": "press-1", "plc": "plc-stamping", "reason": f"unsigned write on {TAG}"}]
    assert acts["active"][0]["value"] == 30 and acts["active"][0]["signed"] is False
    assert client.post("/api/scenarios/PS4A/reset", headers=H).status_code == 200
    world["scada"] = _scada(derate=100)
    [ev] = main._integrity_pass(time.time())
    assert ev["status"] == "cleared" and ev["evidence"]["reason"] == "write intent"
    assert client.get("/api/integrity").json()["findings"] == []
    assert main.AUDIT.verify()[0] is True


def test_integrity_pass_is_blind_when_scada_does_not_answer_and_keeps_the_findings(api):
    main, client, _, world, _, _ = api
    assert client.get("/api/integrity").json()["source"] == "off"
    main._integrity_pass(0)
    world["scada"] = _scada(derate=150)                               # out of range: at once
    assert [e["status"] for e in main._integrity_pass(5)] == ["detected"]
    world["down"].add("scada.test")
    assert main._integrity_pass(10) == []
    view = client.get("/api/integrity").json()
    assert view["source"] == "blind" and "scada /fleet" in view["blind"] and len(view["findings"]) == 1


def test_execute_records_an_intent_so_the_api_write_is_signed(api):
    main, client, _, world, _, _ = api
    world["engine"] = {"root_cause_ranking": [{"pod": "press-1", "score": 0.9}],
                       "edges": [{"src": "press-1", "dst": "psu-a", "r": 0.8, "lag_s": 2, "evidence": ["rail"]}]}
    main._integrity_pass()
    [prop] = client.get("/api/actions").json()["proposals"]
    assert client.post("/api/actions/execute", headers=H, json={"id": prop["id"]}).status_code == 200
    world["scada"] = _scada(derate=55)
    now = time.time()
    assert main._integrity_pass(now) == [] and main._integrity_pass(now + 60) == []
    assert client.get("/api/actions").json()["active"][0]["signed"] is True


def test_balance_mismatch_through_the_pass(api):
    main, client, _, world, _, _ = api
    world["plant"] = {"rails": {"psu-a": {"volts": 345.0, "amps": 80.0}},
                      "devices": {"press-1": {"rail": "psu-a"}, "cnc-1": {"rail": "psu-a"}}}
    world["tags"] = {"source": "scada", "tags": [
        {"tag": "PLANT.PRESS_1.AMPS", "asset": "press-1", "signal": "AMPS", "kind": "measured", "value": 55.0,
         "quality": "GOOD"},
        {"tag": "PLANT.CNC_1.AMPS", "asset": "cnc-1", "signal": "AMPS", "kind": "measured", "value": 25.0,
         "quality": "GOOD"}]}
    world["scada"][0]["tags"].append({"tag": "FLEET.PLC_STAMPING.PRESS_1.AMPS", "asset": "press-1", "signal": "AMPS",
                                      "address": "%IW0", "writable": False, "scale": 10, "value": 40.0,
                                      "quality": "GOOD"})
    assert main._integrity_pass(0) == [] and main._integrity_pass(5) == []
    [ev] = main._integrity_pass(10)
    assert (ev["verb"], ev["status"], ev["evidence"]["channel"]) == ("balance", "mismatch",
                                                                     "FLEET.PLC_STAMPING.PRESS_1.AMPS")
    assert client.get("/api/actions").json()["blocked"][0]["asset"] == "press-1"


# ----------------------------------------------------- refusals and health --
def test_refusals_write_rows_and_targets_are_clipped(api):
    main, client, k8s, _, _, _ = api
    k8s.create("deployments", {"metadata": {"name": "plc-stamping", "labels": {"app": "vplc",
                                                                                "visr/managed": "static"}}})
    assert client.delete("/api/fleet/plcs/plc-stamping", headers=H).status_code == 403
    assert _rows(main, verb="fleet-delete", target="plc-stamping", status="refused: base PLC")
    body = {"name": "plc-pack", "profile": "generic-iec", "task": "packaging-cell", "rail": "psu-c"}
    assert client.post("/api/fleet/plcs", headers=H, json=body).status_code == 202
    assert client.post("/api/fleet/plcs", headers=H, json=body).status_code == 409
    assert _rows(main, verb="fleet-create", target="plc-pack", status="refused: exists")
    assert client.put("/api/fleet/plcs/plc-pack/task", headers=H, json={"task": "stamping-line"}).status_code == 409
    assert _rows(main, verb="load-task", target="plc-pack", status="refused: layout")
    assert client.post("/api/actions/execute", headers=H, json={"id": "x" * 500}).status_code == 409
    assert len(main.AUDIT.entries()[-1]["target"]) == 64
    assert client.post("/api/scenarios/" + "p" * 300 + "/trigger").status_code == 401
    assert len(main.AUDIT.entries()[-1]["target"]) == 64
    assert main.AUDIT.verify()[0] is True


def test_health_reports_scada_and_plant_without_changing_ok(api):
    main, client, _, world, _, _ = api
    h = client.get("/api/health").json()
    assert h["services"] == {"aggregator": "up", "engine": "up", "scada": "up", "plant": "up"} and h["ok"] is True
    world["down"] = {"scada.test", "plant.test"}
    h = client.get("/api/health").json()
    assert h["services"]["scada"] == "down" and h["services"]["plant"] == "down" and h["ok"] is True
    world["down"] = {"engine.test"}
    assert client.get("/api/health").json()["ok"] is False


def test_plant_entities_include_segments_and_members(api):
    main, _, _, world, _, _ = api
    world["plant"]["segments"] = {"field-1": {"members": {"hmi-gw": {"kind": "talker"},
                                                          "plc-stamping": {"kind": "plc"}}}}
    main._PLANT_ENTITIES_TS = 0.0
    assert {"field-1", "hmi-gw", "plc-stamping", "press-1", "psu-a"} <= main._plant_entities()
    assert main.SIGNAL_RESOURCE["field_latency"] == "field network latency"
