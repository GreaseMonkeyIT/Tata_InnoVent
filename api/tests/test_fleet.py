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
    pod = dep["spec"]["template"]["spec"]
    env = {e["name"]: e for e in pod["containers"][0]["env"]}
    # LOG-095: the token arrives as a file from the Secret, and the pod runs non-root with no capabilities
    assert env["TASK_DIR"]["value"] == "/task" and env["DEVICE_TOKEN_FILE"]["value"] == "/run/secrets/device/token"
    assert {"name": "device", "secret": {"secretName": "plc-pack-token"}} in pod["volumes"]
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["sysctls"] == [{"name": "net.ipv4.ip_unprivileged_port_start", "value": "0"}]
    assert pod["containers"][0]["securityContext"]["capabilities"] == {"drop": ["ALL"]}


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


def test_no_proposal_for_a_tripped_machine():
    """LOG-091, Scenario 1 on forge: the derate stayed on offer after press-1 had tripped."""
    tripped = {**PLANT, "devices": {"press-1": {"rail": "psu-a", "amps": 0.2, "tripped": True}}}
    assert fleet.proposals(GRAPH, _scada(), tripped, 55) == []


def test_root_derate_needs_current_above_normal():
    """LOG-091, Scenario 4B on forge: after the reset the verdict still named press-1 for ~40 s, and a
    derate appeared for a press that was back at its normal 43 A."""
    assert len(fleet.proposals(GRAPH, _scada(), PLANT, 55, normal_amps={"press-1": 43.0})) == 1   # 80 A
    back = {**PLANT, "devices": {"press-1": {"rail": "psu-a", "amps": 43.1}}}
    assert fleet.proposals(GRAPH, _scada(), back, 55, normal_amps={"press-1": 43.0}) == []
    assert len(fleet.proposals(GRAPH, _scada(), back, 55)) == 1          # no normal known: the old rule


def _two_press_scada():
    s = _scada()
    s[0]["tags"].append({"tag": "FLEET.PLC_STAMPING.PRESS_2.DERATE_PCT", "asset": "press-2",
                         "signal": "DERATE_PCT", "value": 100, "quality": "GOOD"})
    return s


def test_trip_card_on_a_controllable_machine_proposes_a_derate():
    """LOG-091, Scenario 5 on forge: three machines tripped and nothing was proposed, although
    press-1 and press-2 sit on the stamping PLC."""
    g = {"root": [{"pod": "chiller-1", "score": 0.6}],
         "edges": [{"src": "chiller-1", "dst": "press-1", "r": 0.9, "evidence": ["write", "loop"]}],
         "incipient": [{"pod": "press-2", "class": "trip", "eta_s": 90.0, "value": 71.0, "limit": 78.0,
                        "signal": "coolant_temp"},
                       {"pod": "press-1", "class": "trip", "eta_s": 40.0, "value": 74.0, "limit": 78.0,
                        "signal": "coolant_temp"},
                       {"pod": "furnace-1", "class": "trip", "eta_s": 20.0, "value": 76.0, "limit": 78.0},
                       {"pod": "tag-server", "class": "leak", "eta_s": 30.0}]}
    plant = {"devices": {"press-1": {"rail": "psu-a", "amps": 43.0}, "press-2": {"rail": "psu-a", "amps": 39.0},
                         "furnace-1": {"rail": "psu-b", "amps": 30.0}}}
    props = fleet.proposals(g, _two_press_scada(), plant, 55)
    assert [(p["asset"], p["reason"]) for p in props] == [("press-1", "forecast"), ("press-2", "forecast")]
    assert props[0]["cites"]["eta_s"] == 40.0 and "78" in props[0]["expected"]
    first = props[0]["id"]                             # the id holds while the card counts down
    g["incipient"][1]["eta_s"] = 25.0
    assert fleet.proposals(g, _two_press_scada(), plant, 55)[0]["id"] == first
    plant["devices"]["press-1"]["tripped"] = True      # tripped: no longer a candidate
    assert [p["asset"] for p in fleet.proposals(g, _two_press_scada(), plant, 55)] == ["press-2"]
    assert [p["asset"] for p in fleet.proposals(g, _two_press_scada(), plant, 55, {"press-2"})] == []


def _utilities(value=100):
    return [{"name": "plc-utilities", "profile": "siemens-s7-1200", "connected": True,
             "cell": {"name": "utilities", "machines": ["compressor-1", "chiller-1"]},
             "tags": [{"tag": "FLEET.PLC_UTILITIES.COMPRESSOR_1.DERATE_PCT", "asset": "compressor-1",
                       "signal": "DERATE_PCT", "value": value, "quality": "GOOD"},
                      {"tag": "FLEET.PLC_UTILITIES.CHILLER_1.DERATE_PCT", "asset": "chiller-1",
                       "signal": "DERATE_PCT", "value": value, "quality": "GOOD"}]}]


def test_the_compressor_root_proposal_is_a_stop_and_the_chiller_gets_none():
    """LOG-100: the compressor's DERATE_PCT is its run command, so the root action is a stop (0).
    A chiller is never an action target: less cooling only makes the loop hotter."""
    plant = {"devices": {"compressor-1": {"rail": "psu-b", "amps": 55.0}, "chiller-1": {"rail": "psu-b", "amps": 30.0}}}
    g = {"root": [{"pod": "compressor-1"}],
         "edges": [{"src": "compressor-1", "dst": "furnace-1", "r": 0.8, "evidence": ["write", "loop", "temporal"]}]}
    [p] = fleet.proposals(g, _utilities(), plant, 55, normal_amps={"compressor-1": 13.8})
    assert (p["verb"], p["asset"], p["to"], p["plc"]) == ("stop", "compressor-1", 0, "plc-utilities")
    assert p["expected"].startswith("compressor-1 stops: its heat leaves the loop")
    g_ch = {"root": [{"pod": "chiller-1"}],
            "edges": [{"src": "chiller-1", "dst": "furnace-1", "r": 0.8, "evidence": ["write", "loop"]}]}
    assert fleet.proposals(g_ch, _utilities(), plant, 55) == []
    cards = {"root": [], "edges": [], "incipient": [{"pod": "compressor-1", "class": "trip", "eta_s": 60.0}]}
    assert fleet.proposals(cards, _utilities(), plant, 55) == []
