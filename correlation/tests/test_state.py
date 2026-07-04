import numpy as np

from engine.state import GraphMemory, MemoryConfig


def _graph():
    return {
        "findings": [
            {"pod": "cooling-monitor-abc123-def45", "class": "burst", "onset_s": 10.0, "severity": 0.8},
            {"pod": "timescaledb-aaa111-bbb22", "class": "shift", "onset_s": 40.0, "severity": 0.6},
        ],
        "edges": [
            {
                "src": "cooling-monitor-abc123-def45",
                "dst": "timescaledb-aaa111-bbb22",
                "r": 0.8,
                "lag_s": 30,
                "evidence": ["write", "pvc", "temporal"],  # source-rooted S1 (LOG-060): writer->staller
            }
        ],
        "root_cause_ranking": [
            {"pod": "cooling-monitor-abc123-def45", "score": 1.0, "onset_s": 10.0}
        ],
        "blast_radius": [{"pod": "timescaledb-aaa111-bbb22", "impact": 0.56, "eta_s": 30}],
        "meta": {"pods": 2, "active": 2, "accepted_edges": 1},
    }


def test_edge_memory_persists_and_renders_held_edge(tmp_path):
    db = tmp_path / "memory.db"
    mem = GraphMemory(str(db), MemoryConfig(alpha=0.5, decay=0.1, show=0.6, hide=0.25))
    vectors = {
        "cooling-monitor-abc123-def45": np.zeros(180),
        "timescaledb-aaa111-bbb22": np.zeros(180),
    }

    first = mem.observe(_graph(), vectors, ts=1000.0)
    assert first["edges"][0]["source"] == "live"
    assert first["edges"][0]["confidence"] == 0.5

    second = mem.observe(_graph(), vectors, ts=1010.0)
    assert second["edges"][0]["confidence"] == 0.75
    assert second["meta"]["visible_memory_edges"] == 1

    quiet = {
        "findings": [{"pod": "timescaledb-aaa111-bbb22", "class": "shift", "onset_s": 40.0, "severity": 0.6}],
        "edges": [],
        "root_cause_ranking": [],
        "blast_radius": [],
        "meta": {"pods": 2, "active": 1, "accepted_edges": 0},
    }
    held = mem.observe(quiet, vectors, ts=1020.0)
    assert held["edges"], "confirmed edge should be held after one quiet pass"
    assert held["edges"][0]["source"] == "memory"
    assert held["edges"][0]["state"] == "decaying"
    assert held["meta"]["held_edges"] == 1

    reopened = GraphMemory(str(db), MemoryConfig(alpha=0.5, decay=0.1, show=0.6, hide=0.25))
    assert reopened.stats()["edge_memory"] == 1
    assert reopened.stats()["visible_memory_edges"] == 1


def _graph_named(src, dst):
    return {
        "findings": [
            {"pod": src, "class": "burst", "onset_s": 10.0, "severity": 0.8},
            {"pod": dst, "class": "shift", "onset_s": 40.0, "severity": 0.6},
        ],
        "edges": [
            {"src": src, "dst": dst, "r": 0.8, "lag_s": 30, "evidence": ["stat", "pvc", "temporal"]}
        ],
        "root_cause_ranking": [{"pod": src, "score": 1.0, "onset_s": 10.0}],
        "blast_radius": [{"pod": dst, "impact": 0.56, "eta_s": 30}],
        "meta": {"pods": 2, "active": 2, "accepted_edges": 1},
    }


def test_edge_memory_persists_across_pod_restart(tmp_path):
    cfg = MemoryConfig(alpha=0.5, decay=0.1, show=0.6, hide=0.25)
    mem = GraphMemory(str(tmp_path / "memory.db"), cfg)
    old = ("cooling-monitor-rs1-old11", "timescaledb-rs2-old22")
    vold = {old[0]: np.zeros(180), old[1]: np.zeros(180)}
    mem.observe(_graph_named(*old), vold, ts=1000.0)  # conf 0.5
    mem.observe(_graph_named(*old), vold, ts=1010.0)  # conf 0.75, visible

    # pod restart: brand-new hashes, SAME workloads
    new = ("cooling-monitor-rs1-new11", "timescaledb-rs2-new22")
    vnew = {new[0]: np.zeros(180), new[1]: np.zeros(180)}
    out = mem.observe(_graph_named(*new), vnew, ts=1020.0)

    # one workload-keyed row; confidence CONTINUED across the restart (0.75 -> 0.875)
    assert mem.stats()["edge_memory"] == 1
    assert out["edges"][0]["confidence"] == 0.875
    # rendered against the CURRENT pods, never the dead ones
    e = out["edges"][0]
    assert e["src"] == new[0] and e["dst"] == new[1]
    assert "old" not in e["src"] and "old" not in e["dst"]


def test_absent_workload_edge_is_not_rendered_as_ghost(tmp_path):
    cfg = MemoryConfig(alpha=0.5, decay=0.1, show=0.6, hide=0.25)
    mem = GraphMemory(str(tmp_path / "memory.db"), cfg)
    pods = ("cooling-monitor-rs1-aaaaa", "timescaledb-rs2-bbbbb")
    v = {pods[0]: np.zeros(180), pods[1]: np.zeros(180)}
    mem.observe(_graph_named(*pods), v, ts=1000.0)
    mem.observe(_graph_named(*pods), v, ts=1010.0)  # visible held edge

    # next pass: timescaledb is GONE (no live pod); only cooling-monitor present
    quiet = {
        "findings": [{"pod": pods[0], "class": "burst", "onset_s": 10.0, "severity": 0.8}],
        "edges": [],
        "root_cause_ranking": [],
        "blast_radius": [],
        "meta": {"pods": 1, "active": 1, "accepted_edges": 0},
    }
    out = mem.observe(quiet, {pods[0]: np.zeros(180)}, ts=1020.0)

    # confidence preserved in the DB (retraining), but NOT rendered: a participating
    # workload has no live pod, so no stale ghost reaches /graph
    assert mem.stats()["edge_memory"] == 1
    assert out["edges"] == []
    assert out["meta"]["held_edges"] == 0


def test_structural_floor_keeps_edge_from_decaying_to_zero(tmp_path):
    cfg = MemoryConfig(alpha=0.5, decay=0.2, show=0.6, hide=0.25, prior=0.2, floor_frac=0.4)
    mem = GraphMemory(str(tmp_path / "memory.db"), cfg)
    pods = ("cooling-monitor-x-1", "timescaledb-y-1")
    v = {pods[0]: np.zeros(180), pods[1]: np.zeros(180)}
    for t in range(5):                                 # storm: edge learns a high floor
        mem.observe(_graph_named(*pods), v, ts=1000.0 + t)
    row = mem.db.execute("SELECT confidence, base_conf FROM edge_memory").fetchone()
    assert row["base_conf"] >= 0.35                     # ~= 0.4 * peak confidence

    quiet = {"findings": [], "edges": [], "root_cause_ranking": [], "blast_radius": [],
             "meta": {"pods": 2, "active": 0, "accepted_edges": 0}}
    for t in range(50):                                 # long calm
        mem.observe(quiet, v, ts=2000.0 + t)
    row = mem.db.execute("SELECT confidence, base_conf FROM edge_memory").fetchone()
    assert abs(row["confidence"] - row["base_conf"]) < 1e-6   # settled AT the floor
    assert row["confidence"] > 0.3                            # did NOT decay to zero

    out = mem.observe(quiet, v, ts=2100.0)
    assert out["edges"], "floored edge should still render as the steady backbone"
    assert out["edges"][0]["state"] == "steady"


def test_disk_backbone_is_seeded_at_idle(tmp_path):
    from engine.gate import Witness
    mem = GraphMemory(str(tmp_path / "memory.db"), MemoryConfig(prior=0.2))
    pods = {"cooling-monitor-x-1": np.zeros(180), "timescaledb-y-1": np.zeros(180)}
    w = Witness(shared_relation={frozenset(("cooling-monitor-x-1", "timescaledb-y-1"))})
    idle = {"findings": [], "edges": [], "root_cause_ranking": [], "blast_radius": [],
            "meta": {"pods": 2, "active": 0, "accepted_edges": 0}}
    out = mem.observe(idle, pods, witness=w, ts=1000.0)
    assert out["edges"], "disk backbone should be seeded even with no storm"
    e = out["edges"][0]
    assert e["state"] == "steady" and e["source"] == "memory"
    assert 0.15 <= e["confidence"] <= 0.25
    assert e["src"] in pods and e["dst"] in pods       # rendered against current pods


def test_seeding_retrofits_floor_onto_decayed_edge(tmp_path):
    from engine.gate import Witness
    mem = GraphMemory(str(tmp_path / "memory.db"), MemoryConfig(prior=0.2, decay=0.5, floor_frac=0.0))
    pods = {"cooling-monitor-x-1": np.zeros(180), "timescaledb-y-1": np.zeros(180)}
    quiet = {"findings": [], "edges": [], "root_cause_ranking": [], "blast_radius": [],
             "meta": {"pods": 2, "active": 0, "accepted_edges": 0}}
    # an edge that storms once then decays to ~0 with NO learned floor (floor_frac=0)
    mem.observe(_graph_named("cooling-monitor-x-1", "timescaledb-y-1"), pods, ts=1000.0)
    for t in range(20):
        mem.observe(quiet, pods, ts=1100.0 + t)
    assert mem.db.execute("SELECT base_conf FROM edge_memory").fetchone()["base_conf"] < 0.01

    # a pass WITH the witness retrofits the topology floor -> backbone reappears
    w = Witness(shared_relation={frozenset(("cooling-monitor-x-1", "timescaledb-y-1"))})
    out = mem.observe(quiet, pods, witness=w, ts=2000.0)
    assert out["edges"] and out["edges"][0]["state"] == "steady"
    assert out["edges"][0]["confidence"] >= 0.19


def _case_graph(root, victims, edges):
    return {
        "findings": [],
        "root_cause_ranking": [{"pod": root, "score": 1.0, "onset_s": 10.0}],
        "blast_radius": [{"pod": v, "impact": 0.5, "eta_s": 5} for v in victims],
        "edges": [{"src": s, "dst": d, "r": 0.8, "lag_s": 5, "evidence": ["write", "pvc"]}
                  for (s, d) in edges],
        "meta": {},
    }


def _vecs(*workloads):
    return {w: np.zeros(180) for w in workloads}


def test_case_merge_folds_recurrence_and_minor_motif_variation(tmp_path):
    mem = GraphMemory(str(tmp_path / "memory.db"))  # tau_merge 0.85, tau_family 0.60
    base = _case_graph("cooling-monitor", ["timescaledb", "dcim-bridge"],
                       [("cooling-monitor", "timescaledb"), ("cooling-monitor", "dcim-bridge")])
    m1 = mem.observe(base, _vecs("cooling-monitor", "timescaledb", "dcim-bridge"), ts=1000.0)["meta"]
    assert m1["case_register"] == "novel"
    # identical incident -> recurrence (folds, no new case)
    m2 = mem.observe(base, _vecs("cooling-monitor", "timescaledb", "dcim-bridge"), ts=1010.0)["meta"]
    assert m2["case_register"] == "recurrence" and m2["case_id"] == m1["case_id"]
    # same victims/stressor, motif gains ONE edge -> still near-identical -> folds (no explosion)
    near = _case_graph("cooling-monitor", ["timescaledb", "dcim-bridge"],
                       [("cooling-monitor", "timescaledb"), ("cooling-monitor", "dcim-bridge"),
                        ("dcim-bridge", "timescaledb")])
    m3 = mem.observe(near, _vecs("cooling-monitor", "timescaledb", "dcim-bridge"), ts=1020.0)["meta"]
    assert m3["case_register"] == "recurrence"
    assert mem.stats()["cases"] == 1 and mem.stats()["families"] == 1


def test_case_merge_groups_variant_into_family(tmp_path):
    mem = GraphMemory(str(tmp_path / "memory.db"))
    base = _case_graph("cooling-monitor", ["timescaledb", "dcim-bridge"],
                       [("cooling-monitor", "timescaledb"), ("cooling-monitor", "dcim-bridge")])
    m1 = mem.observe(base, _vecs("cooling-monitor", "timescaledb", "dcim-bridge"), ts=1000.0)["meta"]
    # an extra victim -> a variant (same family), not a fold and not a new family
    variant = _case_graph("cooling-monitor", ["timescaledb", "dcim-bridge", "edge-ui"],
                          [("cooling-monitor", "timescaledb"), ("cooling-monitor", "dcim-bridge"),
                           ("cooling-monitor", "edge-ui")])
    m2 = mem.observe(variant, _vecs("cooling-monitor", "timescaledb", "dcim-bridge", "edge-ui"), ts=1010.0)["meta"]
    assert m2["case_register"] == "variant"
    assert m2["case_family"] == m1["case_family"] and m2["case_id"] != m1["case_id"]
    assert mem.stats()["cases"] == 2 and mem.stats()["families"] == 1


def test_case_merge_novel_opens_new_family(tmp_path):
    mem = GraphMemory(str(tmp_path / "memory.db"))
    g1 = _case_graph("cooling-monitor", ["timescaledb"], [("cooling-monitor", "timescaledb")])
    g2 = _case_graph("plc-gateway", ["critical-control-relay"], [("plc-gateway", "critical-control-relay")])
    m1 = mem.observe(g1, _vecs("cooling-monitor", "timescaledb"), ts=1000.0)["meta"]
    m2 = mem.observe(g2, _vecs("plc-gateway", "critical-control-relay"), ts=1010.0)["meta"]
    assert m2["case_register"] == "novel"
    assert m2["case_family"] != m1["case_family"]
    assert mem.stats()["families"] == 2


def test_psi_baseline_matures_and_ignores_storms(tmp_path):
    mem = GraphMemory(str(tmp_path / "memory.db"), MemoryConfig(base_min_n=12, dev_k=4.0, mad_floor=0.01))
    pod = "timescaledb-x-1"
    steady = np.full(180, 0.15)
    # immature -> no incident threshold yet (engine treats it as 'still learning')
    mem.update_baselines({pod: steady}, ts=1.0)
    assert mem.baseline_threshold("timescaledb") is None
    for t in range(20):
        mem.update_baselines({pod: steady}, ts=2.0 + t)
    thr = mem.baseline_threshold("timescaledb")
    assert thr is not None and 0.15 < thr < 0.30          # ~ median 0.15 + k*mad_floor
    # a storm must NOT drag the matured baseline up (it's skipped while deviating)
    before = thr
    for t in range(8):
        mem.update_baselines({pod: np.full(180, 0.9)}, ts=100.0 + t)
    assert abs(mem.baseline_threshold("timescaledb") - before) < 0.05


def test_case_is_promoted_from_incident(tmp_path):
    mem = GraphMemory(str(tmp_path / "memory.db"))
    vectors = {
        "cooling-monitor-abc123-def45": np.zeros(180),
        "timescaledb-aaa111-bbb22": np.zeros(180),
    }
    out = mem.observe(_graph(), vectors, ts=1000.0)

    assert out["meta"]["case_id"]
    assert out["meta"]["cases"] == 1
    case = mem.db.execute("SELECT * FROM cases").fetchone()
    assert case["signal"] == "psi_io"
    assert case["witness_kind"] == "pvc"
    assert case["occurrences"] == 1


def test_case_not_promoted_from_victim_cascade(tmp_path):
    """ROAD Stage 0 / REMAINING section 1: a psi-only victim->victim cascade whose ranked root
    owns no source(write) edge must NOT be promoted -- it is the wrong-direction case that the
    deviation gate cannot catch (both pods are real victims). Only source-rooted verdicts promote."""
    mem = GraphMemory(str(tmp_path / "memory.db"))
    cascade = {
        "findings": [],
        "root_cause_ranking": [{"pod": "dcim-bridge-x-1", "score": 1.0, "onset_s": 10.0}],
        "blast_radius": [{"pod": "timescaledb-y-1", "impact": 0.5, "eta_s": 30}],
        "edges": [{"src": "dcim-bridge-x-1", "dst": "timescaledb-y-1", "r": 0.7, "lag_s": 0,
                   "evidence": ["stat", "pvc"]}],  # NO write evidence -> not a real aggressor
        "meta": {},
    }
    out = mem.observe(cascade, _vecs("dcim-bridge-x-1", "timescaledb-y-1"), ts=1000.0)
    assert "case_id" not in out["meta"]          # nothing promoted
    assert mem.stats()["cases"] == 0

    # but the SAME pair WITH a source edge (a real aggressor) does promote
    sourced = dict(cascade)
    sourced["edges"] = [{"src": "dcim-bridge-x-1", "dst": "timescaledb-y-1", "r": 0.7, "lag_s": 5,
                         "evidence": ["write", "pvc", "temporal"]}]
    out2 = mem.observe(sourced, _vecs("dcim-bridge-x-1", "timescaledb-y-1"), ts=1010.0)
    assert out2["meta"].get("case_id")
    assert mem.stats()["cases"] == 1
