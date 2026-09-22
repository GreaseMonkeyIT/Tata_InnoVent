"""2H fleet logic without a cluster (api/fleet.py, FLEET.md section 10)."""
import base64
import json
import os

import fleet

TASKS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vplc", "tasks")


def test_library_reads_every_task_with_source():
    lib = fleet.load_library(TASKS)
    assert {"stamping-line", "packaging-cell", "packaging-cell-rush"} <= set(lib)
    assert "PROGRAM" in lib["packaging-cell"]["st"]


def test_resolve_manifest_picks_the_lowest_free_names():
    m = fleet.load_library(TASKS)["packaging-cell"]["manifest"]
    taken = {"pack-conveyor-1", "press-1"}
    r = fleet.resolve_manifest(m, "plc-pack-2", "psu-b", taken)
    assert fleet.cell_machines(r) == ["pack-conveyor-2", "pack-wrapper-1", "pack-labeler-1"]
    assert r["cell"]["rail"] == "psu-b" and r["cell"]["name"] == "pack-2"
    assert "pack-wrapper-1" in taken                        # taken grows, so a second cell gets -2
    assert "name" not in m["cell"]["machines"][0]           # the library manifest stays untouched


def test_layout_groups_tasks_that_can_swap():
    lib = fleet.load_library(TASKS)
    assert fleet.layout(lib["packaging-cell"]["manifest"]) == fleet.layout(lib["packaging-cell-rush"]["manifest"])
    assert fleet.layout(lib["packaging-cell"]["manifest"]) != fleet.layout(lib["stamping-line"]["manifest"])


def test_objects_follow_the_profile_ports_and_carry_the_token():
    m = fleet.resolve_manifest(fleet.load_library(TASKS)["packaging-cell"]["manifest"], "plc-pack", "psu-c", set())
    objs = fleet.objects_for("plc-pack", "siemens-s7-1200", "packaging-cell", "PROGRAM x END_PROGRAM", m, "tok",
                             "localhost:5000/skn/vplc:v0.1", 1758000000.0)
    ports = {p["name"]: p["port"] for p in objs["services"]["spec"]["ports"]}
    assert ports == {"s7comm": 102, "field": 5020, "control": 8080}
    assert base64.b64decode(objs["secrets"]["data"]["token"]).decode() == "tok"
    assert json.loads(objs["configmaps"]["data"]["task.json"])["cell"]["machines"][0]["name"] == "pack-conveyor-1"
    dep = objs["deployments"]
    assert dep["metadata"]["labels"]["visr/managed"] == "ui"
    assert dep["metadata"]["annotations"]["visr/requested-at"] == "1758000000.000"
    env = {e["name"]: e for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["TASK_DIR"]["value"] == "/task" and "secretKeyRef" in env["DEVICE_TOKEN"]["valueFrom"]


def test_sim_cell_body_matches_the_contract():
    m = fleet.resolve_manifest(fleet.load_library(TASKS)["packaging-cell"]["manifest"], "plc-pack", "psu-c", set())
    body = fleet.sim_cell_body("plc-pack", m)
    assert body["plc_host"] == "plc-pack.fleet.svc.cluster.local" and body["field_port"] == 5020
    assert body["fail_open"] is False and body["rail"] == "psu-c"
    wrapper = body["machines"][1]
    assert wrapper["name"] == "pack-wrapper-1" and wrapper["cooled"] is True and wrapper["tau"] == 50.0


def test_window_hits_match_the_pod_workload_exactly():
    keys = ["fleet/plc-pack-1-6d9f8c7b5-x2k4q/psi_cpu", "plant/press-1/bus_voltage"]
    hits = fleet.window_hits(keys, {"plc-pack": ["pack-conveyor-9"], "plc-pack-1": [], "plc-stamping": ["press-1"]})
    assert hits == {"plc-pack-1", "plc-stamping"}


def _dep(name, managed="ui", created="2026-09-15T10:00:00Z", requested=None):
    meta = {"name": name, "creationTimestamp": created,
            "labels": {"app": "vplc", "visr/plc": name, "visr/profile": "generic-iec", "visr/managed": managed}}
    if requested:
        meta["annotations"] = {"visr/requested-at": str(requested)}
    return {"metadata": meta, "spec": {}}


def _pod(created, started=None, ready=False):
    status = {"containerStatuses": [{"ready": ready, "state": {"running": {"startedAt": started}} if started else {}}]}
    return {"metadata": {"creationTimestamp": created}, "status": status}


def test_plc_view_phases_and_state():
    dep = _dep("plc-pack", requested=1789466400.5)
    pods = [_pod("2026-09-15T10:00:01Z", "2026-09-15T10:00:04Z", True)]
    scada = {"name": "plc-pack", "enrolled_at": 1789466405.0, "first_good_at": 1789466406.0, "connected": True,
             "cell": {"name": "pack", "rail": "psu-c", "machines": ["pack-conveyor-1"]},
             "tags": [{"quality": "GOOD"}, {"quality": "STALE"}], "poll_rtt_ms": 2.0}
    vplc = {"state": "RUN", "task": {"name": "packaging-cell", "title": "Packaging", "sha256": "ab", "interval_ms": 100},
            "scan": {"last_ms": 0.1, "avg_ms": 0.1, "max_ms": 0.2, "overruns": 0}}
    v = fleet.plc_view(dep, pods, scada, vplc, 1789466410.0, 1789466420.0)
    ts = {p["phase"]: p["ts"] for p in v["phases"]}
    assert [p["phase"] for p in v["phases"]] == list(fleet.PHASES)
    assert ts["requested"] == 1789466400.5 and ts["scheduled"] == fleet.rfc3339("2026-09-15T10:00:01Z")
    assert ts["running"] and ts["enrolled"] == 1789466405.0 and ts["in_window"] == 1789466410.0
    assert v["state"] == "RUN" and v["scada"] == {"enrolled": True, "connected": True, "rtt_ms": 2.0, "tags": 2, "good": 1}
    assert v["profile_label"] == "IEC 61131-3 soft PLC"
    starting = fleet.plc_view(dep, [], None, None, None, 1789466420.0)
    assert starting["state"] == "STARTING" and starting["scada"]["enrolled"] is False


GRAPH = {"root": [{"pod": "press-1", "score": 0.9}],
         "edges": [{"src": "press-1", "dst": "psu-a", "r": 0.8, "evidence": ["write", "rail", "temporal"],
                    "confidence": 0.9, "signal": "bus_voltage"}]}
PLANT = {"devices": {"press-1": {"rail": "psu-a", "amps": 80.0}}, "rails": {"psu-a": {"volts": 345.0}}}


def _scada(value=100, quality="GOOD", connected=True):
    return [{"name": "plc-stamping", "profile": "siemens-s7-1200", "connected": connected,
             "cell": {"name": "stamping", "machines": ["press-1", "press-2"]},
             "tags": [{"tag": "FLEET.PLC_STAMPING.PRESS_1.DERATE_PCT", "asset": "press-1", "signal": "DERATE_PCT",
                       "value": value, "quality": quality}]}]


def test_proposal_cites_the_verdict_and_needs_every_condition():
    props = fleet.proposals(GRAPH, _scada(), PLANT, 55)
    assert len(props) == 1
    p = props[0]
    assert p["asset"] == "press-1" and p["plc"] == "plc-stamping" and p["to"] == 55
    assert p["cites"]["evidence"] == ["write", "rail", "temporal"] and "psu-a" in p["expected"]
    assert fleet.proposals({**GRAPH, "root": []}, _scada(), PLANT, 55) == []
    assert fleet.proposals({**GRAPH, "edges": [{**GRAPH["edges"][0], "evidence": []}]}, _scada(), PLANT, 55) == []
    assert fleet.proposals(GRAPH, _scada(quality="STALE"), PLANT, 55) == []
    assert fleet.proposals(GRAPH, _scada(value=55), PLANT, 55) == []            # already derated
    assert fleet.proposals(GRAPH, _scada(connected=False), PLANT, 55) == []


def test_proposal_id_changes_with_the_verdict():
    a = fleet.proposals(GRAPH, _scada(), PLANT, 55)[0]["id"]
    other = {**GRAPH, "edges": [{**GRAPH["edges"][0], "dst": "cnc-1"}]}
    assert fleet.proposals(other, _scada(), PLANT, 55)[0]["id"] != a


def test_active_derates_and_snapshot():
    assert fleet.active_derates(_scada(value=55))[0]["value"] == 55
    assert fleet.active_derates(_scada()) == []
    assert fleet.plant_snapshot(PLANT, "press-1") == {"asset": "press-1", "rail": "psu-a", "volts": 345.0, "amps": 80.0}
